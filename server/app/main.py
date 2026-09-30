"""Сервер «Единого окна». Демонстрационный прототип v0.2.

Контуры доступа:
  /api/v1   — планшеты стойки: ключ планшета (X-Device-Key) + сессия сотрудника по ПИН (X-Session)
  /bank/v1  — банки-партнёры: сертификат mTLS (проверяет прокси) + ключ API + подпись HMAC с nonce
  /admin/v1 — администратор системы (X-Admin-Token)
"""
from __future__ import annotations

import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DbSession

from . import rules
from .config import Settings, load_settings
from .crypto import (DataCipher, bank_canonical, consteq, hash_pin, hmac_hex, new_secret, norm_fingerprint, sha256,
                     verify_pin)
from .db import init_db, make_engine, make_sessionmaker
from .models import (Audit, Bank, BankNonce, Case, Consent, ConsentText, Correction, Device, DocPolicy, HandoffToken,
                     Operator, OperatorClaim, RejectedEvent, Session, Setting, Stand, Step)
from .queue import SERIES, Queue
from .services import Svc, aware

VERSION = "0.2.8"
MAX_SKEW_SEC = 300
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
IDEM_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_PIN_FAILS = 5
LOCK_MIN = 15
DUMMY_PIN_HASH = hash_pin("000000-dummy")


# ---------- схемы запросов ----------
class PassportIn(BaseModel):
    surname: str = Field(max_length=100)
    given: str = Field(max_length=100)
    cyr: str = Field(default="", max_length=200)
    country: str = Field(min_length=3, max_length=3)
    country_name: str = Field(default="", max_length=100)
    number: str = Field(max_length=20)
    dob: str = Field(max_length=10)
    sex: str = Field(default="", max_length=8)
    expiry: str = Field(max_length=10)
    has_ru_text: bool = False
    source: Literal["chip", "mrz", "ocr", "demo"] = "demo"
    checks: list[str] = []


class MigcardIn(BaseModel):
    series: str = Field(max_length=10)
    number: str = Field(max_length=20)
    entry_date: str = Field(max_length=10)
    kpp: str = Field(default="", max_length=100)
    purpose: str = Field(default="", max_length=50)
    stay_until: str = Field(default="", max_length=10)


class ScanIn(BaseModel):
    passport: PassportIn | None = None
    migcard: MigcardIn | None = None


class CorrectionIn(BaseModel):
    section: Literal["passport", "migcard"]
    field: str = Field(max_length=32)
    value: str = Field(max_length=200)
    # причина — только из списка: свободный текст мог бы содержать персональные данные
    reason: Literal["Низкая уверенность распознавания", "Расхождение оригинала и OCR",
                    "Обязательное требование банка", "Написание по нотариальному переводу"]


class ConsentIn(BaseModel):
    recipient: Literal["simpad", "bank", "notary", "insurer"]
    text_id: int
    method: Literal["paper", "stylus"]
    explained_lang: Literal["ru", "tj", "uz", "ky"]
    evidence_mime: Literal["image/png", "image/jpeg", "application/pdf"]
    evidence_b64: str = Field(max_length=6_000_000)


class CasePatch(BaseModel):
    pkg: Literal["card", "sim", "full"] | None = None
    opts: dict[str, bool] | None = None
    pay: dict[str, str] | None = None
    bank_id: str | None = None
    ruid: bool | None = None
    lang: Literal["ru", "tj", "uz", "ky"] | None = None


class StepIn(BaseModel):
    status: Literal["todo", "work", "done", "fail"]
    note: str = Field(default="", max_length=500)
    imei: str | None = Field(default=None, max_length=15)
    doc: str = Field(default="", max_length=40)  # номер подтверждающего документа для «выполнено»


class ClaimIn(BaseModel):
    code: str = Field(max_length=32)
    note: str = Field(default="", max_length=500)


class PinIn(BaseModel):
    pin: str = Field(min_length=4, max_length=12)


class LoginIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    pin: str = Field(min_length=4, max_length=12)


class RedeemIn(BaseModel):
    qr: str = Field(max_length=200)


def _iso_aware(v: str) -> str:
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        raise ValueError("дата в формате ISO 8601")
    if dt.tzinfo is None:
        raise ValueError("укажите часовой пояс, например +03:00")
    return v


class EventIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(max_length=36)
    handoff_id: str = Field(min_length=1, max_length=36)
    bank_case_id: str = Field(min_length=1, max_length=64)
    bank_code: str = Field(max_length=64)
    occurred_at: str
    schema_version: str = Field(default="2", max_length=16)
    note: str = Field(default="", max_length=500)

    @field_validator("occurred_at")
    @classmethod
    def _tz(cls, v: str) -> str:
        return _iso_aware(v)


class ReconRow(BaseModel):
    event_id: str = Field(min_length=1, max_length=64)
    case_number: str = Field(max_length=16)
    bank_case_id: str = Field(min_length=1, max_length=64)
    bank_code: str = Field(max_length=64)
    occurred_at: str

    @field_validator("occurred_at")
    @classmethod
    def _tz(cls, v: str) -> str:
        return _iso_aware(v)


class ReconIn(BaseModel):
    source_doc: str = Field(min_length=3, max_length=200)  # реквизиты файла/акта сверки банка
    rows: list[ReconRow] = Field(max_length=5000)


class StandIn(BaseModel):
    id: str = Field(max_length=32)
    name: str = Field(max_length=200)


Role = Literal["desk", "hall", "reception", "board"]
STATION_RE = r"^[0-9A-Za-z-]{0,8}$"
SERIES_RE = r"^[" + SERIES + r"]?$"


class DeviceIn(BaseModel):
    stand_id: str
    name: str = Field(max_length=200)
    role: Role = "desk"
    station: str = Field(default="", pattern=STATION_RE)
    series: str = Field(default="", pattern=SERIES_RE)


class DevicePatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    role: Role | None = None
    station: str | None = Field(default=None, pattern=STATION_RE)
    series: str | None = Field(default=None, pattern=SERIES_RE)


class TicketIn(BaseModel):
    lang: Literal["ru", "tj", "uz", "ky"] = "ru"


class CheckinIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)


class TicketTextIn(BaseModel):
    your: str | None = Field(default=None, max_length=60)
    route: str | None = Field(default=None, max_length=300)
    valid: str | None = Field(default=None, max_length=120)


class OperatorIn(BaseModel):
    stand_id: str
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(max_length=200)
    pin: str = Field(pattern=r"^\d{6,8}$")
    role: Literal["operator", "senior"] = "operator"


class OperatorPatch(BaseModel):
    pin: str | None = Field(default=None, pattern=r"^\d{6,8}$")
    active: bool | None = None


def check_hours(start: int, end: int) -> None:
    """Часы банка: начало 0–23, конец 1–24, не равны. Начало больше конца — ночной интервал (22–6)."""
    if (start, end) == (0, 24):
        return  # круглосуточно
    if not (0 <= start <= 23 and 1 <= end <= 24 and start != end % 24):
        raise HTTPException(422, "Часы работы: с 0–23 до 1–24, не совпадают; ночной интервал, например 22–6")


class BankIn(BaseModel):
    id: str = Field(max_length=32)
    name: str = Field(max_length=200)
    legal_name: str = Field(default="", max_length=300)
    inn: str = Field(default="", max_length=12)
    sim_brand: str = ""
    mode: Literal["api", "case"] = "case"
    open_from: int = Field(default=9, ge=0, le=24)
    open_to: int = Field(default=21, ge=0, le=24)


class BankPatch(BaseModel):
    name: str | None = None
    legal_name: str | None = None
    inn: str | None = None
    sim_brand: str | None = None
    mode: Literal["api", "case"] | None = None
    open_from: int | None = Field(default=None, ge=0, le=24)
    open_to: int | None = Field(default=None, ge=0, le=24)
    status_map: dict[str, str] | None = None
    sim_authority_confirmed: bool | None = None
    cert_fingerprint: str | None = Field(default=None, max_length=100)
    active: bool | None = None
    window: str | None = Field(default=None, pattern=r"^[0-9A-Za-z-]{0,8}$")
    snils_channel: Literal["bank", "sfr"] | None = None


class PolicyIn(BaseModel):
    bank_id: str
    service: Literal["card", "snils", "ebs", "sim"] = "card"
    country: str = Field(min_length=3, max_length=3)
    rule: Literal["no", "before", "final", "unknown"]
    confirmed: bool = False
    version: str = ""


class ConsentTextIn(BaseModel):
    recipient: Literal["simpad", "bank", "notary", "insurer"]
    bank_id: str | None = None
    legal_name: str = Field(min_length=3, max_length=300)
    inn: str = Field(pattern=r"^\d{10}(\d{2})?$")
    address: str = Field(default="", max_length=500)
    purpose: str = Field(min_length=5)
    fields: list[str] = Field(min_length=1)
    retention: str = Field(min_length=3, max_length=200)
    lang: Literal["ru"] = "ru"
    body: str = Field(min_length=50)
    version: str = Field(min_length=1, max_length=64)
    approved_by: str = Field(default="", max_length=200)


def parse(model: type[BaseModel], body: bytes) -> BaseModel:
    """Тело запроса банка читается вручную (оно нужно для подписи), поэтому ошибки формата переводим в 422."""
    try:
        return model.model_validate_json(body)
    except ValidationError as e:
        raise HTTPException(422, "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()))


class ApproveIn(BaseModel):
    approved_by: str = Field(min_length=5, max_length=200)  # ФИО юриста и реквизиты приказа/решения


# ---------- приложение ----------
def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    if not settings.dup_key or settings.dup_key == settings.data_key:
        raise RuntimeError("SIMPAD_DUP_KEY должен быть задан и отличаться от SIMPAD_DATA_KEY")
    if not settings.bank_mtls and not settings.allow_demo_scan:
        raise RuntimeError("SIMPAD_BANK_MTLS=false допустим только на учебном сервере (SIMPAD_ALLOW_DEMO_SCAN=true)")
    engine = make_engine(settings.database_url)
    if settings.auto_create_schema:
        init_db(engine)
    SessionLocal = make_sessionmaker(engine)
    cipher = DataCipher(settings.data_key)

    app = FastAPI(title="Единое окно (демонстрационный прототип)", version=VERSION,
                  docs_url=None, redoc_url=None)
    app.state.settings = settings
    if settings.cors_origins:  # планшет ходит через нативный мост, CORS нужен только для учебного браузерного режима
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                           allow_headers=["X-Device-Key", "X-Session", "Content-Type", "Idempotency-Key"], allow_credentials=False)

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        resp: Response = await call_next(request)
        if request.url.path.startswith(("/api/", "/bank/", "/admin/")):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def db():
        s = SessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def svc(s: DbSession = Depends(db, scope="function")) -> Svc:
        return Svc(s, cipher, settings)

    def device(x_device_key: str = Header(default=""), x_app_version: str = Header(default=""),
               s: DbSession = Depends(db, scope="function")) -> Device:
        if not x_device_key:
            raise HTTPException(401, "Нет ключа устройства")
        d = s.scalar(select(Device).where(Device.key_hash == sha256(x_device_key), Device.active.is_(True)))
        if not d:
            raise HTTPException(401, "Устройство не зарегистрировано или отключено")
        # Устаревшее приложение (например, с истёкшим закреплением сертификата) не обслуживается.
        # Табло — страница в браузере экрана, приложения у него нет: проверка версии к нему не относится.
        if d.role != "board" and settings.min_app_version and (
                not x_app_version.isdigit() or int(x_app_version) < settings.min_app_version):
            raise HTTPException(426, "Версия приложения устарела — установите обновление через систему управления планшетами")
        return d

    # Что разрешено планшету каждой роли. Рабочее место (desk) — всё; табло — вход сотрудника не нужен вовсе.
    _common = r"/api/v1/(me|logout|reauth|tickets|tickets/[0-9a-f-]{36}/printed)"
    ROLE_PATHS = {
        "hall": re.compile(rf"^{_common}$"),
        "reception": re.compile(rf"^({_common}|/api/v1/tickets/checkin|/api/v1/queue|/api/v1/reports/queue)$"),
        "board": re.compile(r"^$"),  # табло не входит как сотрудник
    }

    class Who:
        def __init__(self, d: Device, op: Operator):
            self.device, self.op = d, op
            self.stand_id = d.stand_id
            self.actor = f"op:{op.code}/dev:{d.id}"

    def operator(request: Request, x_session: str = Header(default=""), d: Device = Depends(device),
                 s: DbSession = Depends(db, scope="function")) -> Who:
        allowed = ROLE_PATHS.get(d.role)
        if allowed is not None and not allowed.match(request.url.path):
            raise HTTPException(403, "Это действие недоступно на планшете этой роли")
        if not x_session:
            raise HTTPException(401, "Войдите по коду сотрудника и ПИН")
        now = datetime.now(timezone.utc)
        ses = s.scalar(select(Session).where(Session.token_hash == sha256(x_session)))
        if not ses or ses.revoked or ses.device_id != d.id or aware(ses.expires_at) <= now:
            raise HTTPException(401, "Сессия закончилась — войдите снова")
        op = s.get(Operator, ses.operator_id)
        if not op or not op.active or op.stand_id != d.stand_id:
            raise HTTPException(401, "Сотрудник отключён")
        # скользящий срок: 15 минут бездействия, но не дольше смены
        ses.expires_at = min(now + timedelta(minutes=settings.session_idle_min),
                             aware(ses.created_at) + timedelta(hours=settings.session_max_hours))
        s.commit()  # продление сессии фиксируется, даже если само действие завершится ошибкой
        return Who(d, op)

    def admin(x_admin_token: str = Header(default="")) -> None:
        if not x_admin_token or not consteq(x_admin_token, settings.admin_token):
            raise HTTPException(401, "Нет доступа")

    async def raw_body(request: Request) -> bytes:
        return await request.body()

    def bank_auth(request: Request, body: bytes = Depends(raw_body),
                  s: DbSession = Depends(db, scope="function")) -> tuple[Bank, bytes]:
        key = request.headers.get("X-Bank-Key", "")
        ts = request.headers.get("X-Timestamp", "")
        nonce = request.headers.get("X-Nonce", "")
        sig = request.headers.get("X-Signature", "")
        now = datetime.now(timezone.utc)
        bank, secret = None, None
        if key:
            kh = sha256(key)
            bank = s.scalar(select(Bank).where(Bank.api_key_hash == kh, Bank.active.is_(True)))
            secret = bank.hmac_secret if bank else None
            if not bank:  # старый ключ действует ограниченное время после ротации
                bank = s.scalar(select(Bank).where(Bank.prev_key_hash == kh, Bank.active.is_(True)))
                if bank and bank.prev_valid_until and aware(bank.prev_valid_until) > now:
                    secret = bank.prev_hmac_secret
                else:
                    bank = None
        if not bank or not secret:
            raise HTTPException(401, "Банк не распознан")
        if settings.bank_mtls:
            fp = norm_fingerprint(request.headers.get("X-Client-Cert-Sha256", ""))
            if not bank.cert_fingerprint or not fp or not consteq(fp, bank.cert_fingerprint):
                raise HTTPException(401, "Нет действующего сертификата банка (mTLS)")
        try:
            skew = abs(time.time() - int(ts))
        except ValueError:
            raise HTTPException(401, "Нет метки времени")
        if skew > MAX_SKEW_SEC:
            raise HTTPException(401, "Метка времени вне допустимого окна")
        if not NONCE_RE.match(nonce):
            raise HTTPException(401, "Нет одноразового значения X-Nonce (16–64 символа)")
        expected = hmac_hex(secret, bank_canonical(request.method, request.url.path, ts, nonce, body))
        if not consteq(expected, sig):
            raise HTTPException(401, "Подпись запроса неверна")
        s.execute(delete(BankNonce).where(BankNonce.at < now - timedelta(seconds=MAX_SKEW_SEC * 3)))
        s.add(BankNonce(bank_id=bank.id, nonce=nonce, at=now))
        try:
            s.commit()  # nonce фиксируется сразу: повтор запрещён, даже если сам запрос завершится ошибкой
        except IntegrityError:
            s.rollback()
            raise HTTPException(401, "Повтор запроса: X-Nonce уже использован")
        mark_seen(s, bank.id, now)
        return bank, body

    def mark_seen(s: DbSession, bank_id: str, now: datetime) -> None:
        """Время последнего подписанного запроса банка — для индикатора связи на стойке. Не влияет на обработку."""
        try:
            row = s.get(Setting, f"bank_seen:{bank_id}")
            if row:
                if now - datetime.fromisoformat(row.value["at"]) < timedelta(seconds=60):
                    return  # не чаще раза в минуту: индикатору достаточно, лишних записей в базу нет
                row.value = {"at": now.isoformat()}
            else:
                s.add(Setting(key=f"bank_seen:{bank_id}", value={"at": now.isoformat()}))
            s.commit()
        except SQLAlchemyError:  # индикатор не должен ломать запрос банка (одновременная запись, занятая база)
            s.rollback()

    # ----- служебное -----
    @app.get("/healthz")
    def healthz(response: Response, s: DbSession = Depends(db, scope="function")):
        """Проверка живости с запросом к базе: при недоступной базе — 503 (используется проверкой здоровья Docker)."""
        try:
            s.execute(select(1))
            db_ok = True
        except Exception:
            db_ok = False
            response.status_code = 503
        return {"ok": db_ok, "db": db_ok, "version": VERSION, "demo_scan": settings.allow_demo_scan, "bank_mtls": settings.bank_mtls}

    # ----- вход сотрудника -----
    @app.post("/api/v1/login")
    def login(body: LoginIn, d: Device = Depends(device), s: DbSession = Depends(db, scope="function")):
        if d.role == "board":
            raise HTTPException(403, "На табло сотрудники не входят")
        now = datetime.now(timezone.utc)
        op = s.scalar(select(Operator).where(Operator.code == body.code, Operator.stand_id == d.stand_id))
        if op and op.locked_until and aware(op.locked_until) > now:
            raise HTTPException(429, f"Вход заблокирован до {aware(op.locked_until).astimezone(settings.tz):%H:%M} после неверных ПИН")
        # для неизвестного кода тоже считаем scrypt — по времени ответа нельзя узнать, какие коды существуют
        pin_ok = verify_pin(body.pin, op.pin_hash if op else DUMMY_PIN_HASH)
        if not op or not op.active or not pin_ok:
            if op:
                s.execute(update(Operator).where(Operator.id == op.id).values(failed=Operator.failed + 1))
                s.execute(update(Operator).where(Operator.id == op.id, Operator.failed >= MAX_PIN_FAILS)
                          .values(failed=0, locked_until=now + timedelta(minutes=LOCK_MIN)))
                s.add(Audit(actor=f"dev:{d.id}", action="login.fail", detail=body.code))
            s.commit()
            raise HTTPException(401, "Неверный код сотрудника или ПИН")
        op.failed, op.locked_until = 0, None
        token = secrets.token_urlsafe(32)
        exp = now + timedelta(minutes=settings.session_idle_min)
        s.add(Session(token_hash=sha256(token), operator_id=op.id, device_id=d.id, created_at=now, expires_at=exp))
        s.add(Audit(actor=f"op:{op.code}/dev:{d.id}", action="login"))
        return {"session": token, "idle_min": settings.session_idle_min,
                "operator": {"code": op.code, "name": op.name, "role": op.role}}

    @app.post("/api/v1/reauth")
    def reauth(body: PinIn, x_session: str = Header(default=""), w: Who = Depends(operator),
               s: DbSession = Depends(db, scope="function")):
        """Возврат планшета от клиента: сотрудник подтверждает себя ПИН. 5 ошибок — блокировка и конец сессии."""
        now = datetime.now(timezone.utc)
        op = w.op
        if op.locked_until and aware(op.locked_until) > now:
            raise HTTPException(429, "Вход заблокирован после неверных ПИН")
        if not verify_pin(body.pin, op.pin_hash):
            s.execute(update(Operator).where(Operator.id == op.id).values(failed=Operator.failed + 1))
            locked = s.execute(update(Operator).where(Operator.id == op.id, Operator.failed >= MAX_PIN_FAILS)
                               .values(failed=0, locked_until=now + timedelta(minutes=LOCK_MIN))).rowcount
            if locked:
                ses = s.scalar(select(Session).where(Session.token_hash == sha256(x_session)))
                if ses:
                    ses.revoked = True
            s.add(Audit(actor=w.actor, action="reauth.fail"))
            s.commit()
            raise HTTPException(403, "Неверный ПИН")
        op.failed = 0
        s.add(Audit(actor=w.actor, action="reauth"))
        return {"ok": True}

    @app.post("/api/v1/logout")
    def logout(x_session: str = Header(default=""), w: Who = Depends(operator), s: DbSession = Depends(db, scope="function")):
        ses = s.scalar(select(Session).where(Session.token_hash == sha256(x_session)))
        if ses:
            ses.revoked = True
        s.add(Audit(actor=w.actor, action="logout"))
        return {"ok": True}

    # ----- стойка -----
    @app.get("/api/v1/me")
    def me(w: Who = Depends(operator), s: DbSession = Depends(db, scope="function")):
        st = s.get(Stand, w.stand_id)
        return {"device": w.device.name, "stand": {"id": st.id, "name": st.name},
                "role": w.device.role, "station": w.device.station, "series": w.device.series,
                "operator": {"code": w.op.code, "name": w.op.name, "role": w.op.role},
                "demo_scan": settings.allow_demo_scan, "version": VERSION}

    @app.get("/api/v1/consent-texts")
    def consent_texts(_: Who = Depends(operator), sv: Svc = Depends(svc)):
        return sv.consent_texts()

    @app.get("/api/v1/banks")
    def banks(_: Who = Depends(operator), sv: Svc = Depends(svc)):
        h = sv.hour_now()
        notary = sv.setting("notary_hours", [9, 20])
        out = [{"id": b.id, "name": b.name, "legal_name": b.legal_name, "sim_brand": b.sim_brand, "mode": b.mode,
                "open_from": b.open_from, "open_to": b.open_to, "open_now": rules.is_open(h, (b.open_from, b.open_to)),
                "hours": rules.span_text((b.open_from, b.open_to)),
                "sim_authority_confirmed": b.sim_authority_confirmed, "snils_channel": sv.snils_channel(b.id),
                **sv.bank_link(b)}
               for b in sv.db.scalars(select(Bank).where(Bank.active.is_(True)).order_by(Bank.name))]
        return {"banks": out, "notary": {"open_from": notary[0], "open_to": notary[1], "open_now": notary[0] <= h < notary[1]},
                "hour": round(h, 2), "services_off": sv.services_off(), "demo": settings.allow_demo_scan}

    @app.get("/api/v1/cases")
    def list_cases(w: Who = Depends(operator), sv: Svc = Depends(svc), include_closed: bool = False):
        q = select(Case).where(Case.stand_id == w.stand_id)
        if not include_closed:
            q = q.where(Case.closed_at.is_(None))
        since = datetime.now(timezone.utc) - timedelta(days=2)
        q = q.where(Case.created_at >= since).order_by(Case.created_at)
        out = []
        for c in sv.db.scalars(q):
            v = sv.view(c)
            steps = v["steps"]
            out.append({"id": c.id, "number": c.number, "created_at": v["created_at"],
                        "name": (v["passport"] or {}).get("cyr") or "", "country": (v["passport"] or {}).get("country_name") or "",
                        "pkg": c.pkg, "bank_id": c.bank_id, "done": sum(1 for x in steps if x["status"] == "done"),
                        "total": len(steps), "dup_of": c.dup_of})
        return out

    @app.post("/api/v1/cases", status_code=201)
    def create_case(w: Who = Depends(operator), sv: Svc = Depends(svc)):
        c = sv.create_case(w.stand_id, w.actor)
        sv.db.flush()
        return sv.view(c)

    @app.get("/api/v1/cases/{case_id}")
    def get_case(case_id: str, w: Who = Depends(operator), sv: Svc = Depends(svc)):
        return sv.view(sv.get_case(case_id, w.stand_id))

    @app.put("/api/v1/cases/{case_id}/scan")
    def put_scan(case_id: str, body: ScanIn, w: Who = Depends(operator), sv: Svc = Depends(svc)):
        c = sv.get_case(case_id, w.stand_id)
        if c.sent_at:
            raise HTTPException(409, "Обращение уже передано в банк")
        if "simpad" not in sv.active_consents(c):
            raise HTTPException(409, "Сначала согласие клиента на обработку данных — без него документы не сканируются")
        if body.passport and body.passport.source == "demo" and not settings.allow_demo_scan:
            raise HTTPException(400, "Демо-сканер отключён на этом сервере. Нужен настоящий сканер документов")
        sv.set_scan(c, body.passport.model_dump() if body.passport else None,
                    body.migcard.model_dump() if body.migcard else None, w.actor)
        sv.db.flush()
        return sv.view(c)

    @app.post("/api/v1/cases/{case_id}/corrections")
    def correct(case_id: str, body: CorrectionIn, w: Who = Depends(operator), sv: Svc = Depends(svc)):
        c = sv.get_case(case_id, w.stand_id)
        if c.sent_at:
            raise HTTPException(409, "Обращение уже передано в банк — правки через банк")
        sv.correct(c, body.section, body.field, body.value, body.reason, w.actor, w.op.id)
        sv.db.flush()
        return sv.view(c)

    @app.post("/api/v1/cases/{case_id}/consents")
    def consents(case_id: str, body: ConsentIn, w: Who = Depends(operator), sv: Svc = Depends(svc)):
        c = sv.get_case(case_id, w.stand_id)
        sv.grant_consent(c, body.model_dump(), w.actor, w.op.id)
        sv.db.flush()
        return sv.view(c)

    @app.delete("/api/v1/cases/{case_id}/consents/{recipient}")
    def revoke(case_id: str, recipient: Literal["simpad", "bank", "notary", "insurer"], w: Who = Depends(operator),
               sv: Svc = Depends(svc)):
        c = sv.get_case(case_id, w.stand_id)
        sv.revoke_consent(c, recipient, w.actor)
        sv.db.flush()
        return sv.view(c)

    @app.patch("/api/v1/cases/{case_id}")
    def patch_case(case_id: str, body: CasePatch, w: Who = Depends(operator), sv: Svc = Depends(svc)):
        c = sv.get_case(case_id, w.stand_id)
        sv.update_case(c, body.model_dump(exclude_none=True), w.actor)
        sv.db.flush()
        return sv.view(c)

    @app.post("/api/v1/cases/{case_id}/steps/{key}")
    def step(case_id: str, key: str, body: StepIn, w: Who = Depends(operator), sv: Svc = Depends(svc)):
        c = sv.get_case(case_id, w.stand_id)
        sv.operator_step(c, key, body.status, body.note, w.actor, imei=body.imei, doc=body.doc)
        sv.db.flush()
        return sv.view(c)

    @app.post("/api/v1/cases/{case_id}/bank-claims")
    def bank_claim(case_id: str, body: ClaimIn, w: Who = Depends(operator), sv: Svc = Depends(svc)):
        c = sv.get_case(case_id, w.stand_id)
        sv.operator_claim(c, body.code, body.note, w.actor, w.op.id)
        sv.db.flush()
        return sv.view(c)

    @app.get("/api/v1/cases/{case_id}/handoff/preview")
    def handoff_preview(case_id: str, w: Who = Depends(operator), sv: Svc = Depends(svc)):
        return sv.preview(sv.get_case(case_id, w.stand_id))

    @app.post("/api/v1/cases/{case_id}/recheck")
    def recheck(case_id: str, w: Who = Depends(operator), sv: Svc = Depends(svc)):
        c = sv.get_case(case_id, w.stand_id)
        issues = sv.recheck(c)
        sv.audit(w.actor, "case.recheck", c.id, "; ".join(issues) or "ok")
        return {"ok": not issues, "issues": issues}

    @app.post("/api/v1/cases/{case_id}/handoff")
    def handoff(case_id: str, w: Who = Depends(operator), sv: Svc = Depends(svc)):
        return sv.issue_handoff(sv.get_case(case_id, w.stand_id), w.actor)

    @app.post("/api/v1/cases/{case_id}/handoff/manual")
    def handoff_manual(case_id: str, w: Who = Depends(operator), sv: Svc = Depends(svc)):
        c = sv.get_case(case_id, w.stand_id)
        res = sv.manual_handoff(c, w.actor)
        sv.db.flush()
        return {**res, "view": sv.view(c)}

    @app.post("/api/v1/cases/{case_id}/close")
    def close(case_id: str, w: Who = Depends(operator), sv: Svc = Depends(svc)):
        c = sv.get_case(case_id, w.stand_id)
        c.closed_at = datetime.now(timezone.utc)
        Queue(sv.db, settings.tz, settings.data_key).finish_case(c)
        sv.audit(w.actor, "case.close", c.id)
        return {"ok": True}

    # ----- очередь: талон в зале → приёмная → вызов на табло -----
    def q(s: DbSession = Depends(db, scope="function")) -> Queue:
        return Queue(s, settings.tz, settings.data_key)

    @app.post("/api/v1/tickets", status_code=201)
    def issue_ticket(body: TicketIn, response: Response, w: Who = Depends(operator), qu: Queue = Depends(q),
                     idempotency_key: str = Header(default="")):
        """Повтор с тем же заголовком Idempotency-Key (плохая связь, повторное нажатие) возвращает тот же талон."""
        if idempotency_key and not IDEM_RE.match(idempotency_key):
            raise HTTPException(422, "Idempotency-Key: 8–64 символа [A-Za-z0-9_-]")
        source = "hall" if w.device.role == "hall" else "reception"
        t, token, replay = qu.issue(w.device, w.op, body.lang, source, w.actor, idempotency_key or None)
        if replay:
            response.status_code = 200
        return {**qu.ticket_out(t, token), "replay": replay}

    @app.post("/api/v1/tickets/{ticket_id}/printed")
    def ticket_printed(ticket_id: str, w: Who = Depends(operator), qu: Queue = Depends(q)):
        return qu.printed(w.device, ticket_id, w.actor)

    @app.post("/api/v1/tickets/checkin")
    def checkin(body: CheckinIn, w: Who = Depends(operator), qu: Queue = Depends(q)):
        return qu.checkin(w.stand_id, body.code, w.actor)

    @app.get("/api/v1/queue")
    def queue_view(w: Who = Depends(operator), qu: Queue = Depends(q)):
        return qu.overview(w.stand_id)

    @app.post("/api/v1/queue/next")
    def call_next(w: Who = Depends(operator), qu: Queue = Depends(q)):
        return qu.call_next(w.device, w.op, w.actor)

    @app.post("/api/v1/tickets/{ticket_id}/recall")
    def recall(ticket_id: str, w: Who = Depends(operator), qu: Queue = Depends(q)):
        return qu.recall(w.device, ticket_id, w.actor)

    @app.post("/api/v1/tickets/{ticket_id}/noshow")
    def noshow(ticket_id: str, w: Who = Depends(operator), qu: Queue = Depends(q)):
        return qu.noshow(w.device, ticket_id, w.actor)

    @app.post("/api/v1/tickets/{ticket_id}/start", status_code=201)
    def start_ticket(ticket_id: str, w: Who = Depends(operator), sv: Svc = Depends(svc)):
        """Клиент подошёл к стойке: открывается обращение, привязанное к талону. Язык берётся из талона."""
        qu = Queue(sv.db, settings.tz, settings.data_key)
        t = qu.claim(w.device, ticket_id)  # атомарно: второй запрос получит 409, обращение не создаётся
        c = sv.create_case(w.stand_id, w.actor)
        qu.attach(t, c, w.actor)
        sv.db.flush()
        return sv.view(c)

    @app.post("/api/v1/cases/{case_id}/to-bank")
    def to_bank(case_id: str, w: Who = Depends(operator), sv: Svc = Depends(svc)):
        """Вызов клиента к окну банка на табло — после передачи обращения в банк."""
        c = sv.get_case(case_id, w.stand_id)
        if not c.sent_at:
            raise HTTPException(409, "Сначала передайте обращение в банк")
        return Queue(sv.db, settings.tz, settings.data_key).to_bank(w.stand_id, c, w.actor)

    @app.get("/api/v1/reports/queue")
    def queue_report(date: str | None = None, w: Who = Depends(operator), qu: Queue = Depends(q)):
        if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise HTTPException(422, "Дата в формате ГГГГ-ММ-ДД")
        return qu.report(w.stand_id, date or qu.today())

    @app.get("/api/v1/board")
    def board(d: Device = Depends(device), qu: Queue = Depends(q)):
        """Табло офиса: только номера талонов и стоек. Вход сотрудника не нужен, нужен ключ устройства-табло."""
        if d.role not in ("board", "reception", "desk"):
            raise HTTPException(403, "Табло недоступно на этом устройстве")
        return qu.board(d.stand_id)

    @app.get("/api/v1/cases/{case_id}/memo")
    def memo(case_id: str, lang: Literal["ru", "tj", "uz", "ky"] = "ru", w: Who = Depends(operator), sv: Svc = Depends(svc)):
        return sv.memo(sv.get_case(case_id, w.stand_id), lang)

    @app.get("/api/v1/reports/shift")
    def shift(date: str | None = None, w: Who = Depends(operator), sv: Svc = Depends(svc)):
        try:
            day = datetime.fromisoformat(date).replace(tzinfo=settings.tz) if date else datetime.now(settings.tz)
        except ValueError:
            raise HTTPException(422, "Дата в формате ГГГГ-ММ-ДД")
        return sv.shift_report(w.stand_id, day)

    # ----- банк -----
    @app.post("/bank/v1/handoff/redeem")
    def redeem(auth=Depends(bank_auth), sv: Svc = Depends(svc)):
        bank, body = auth
        data = parse(RedeemIn, body)
        return sv.redeem(bank, data.qr)

    @app.post("/bank/v1/events")
    def events(auth=Depends(bank_auth), sv: Svc = Depends(svc)):
        bank, body = auth
        ev = parse(EventIn, body)
        return sv.bank_event(bank, ev.model_dump(), body)

    # ----- администратор -----
    @app.post("/admin/v1/stands", dependencies=[Depends(admin)])
    def add_stand(body: StandIn, s: DbSession = Depends(db, scope="function")):
        if s.get(Stand, body.id):
            raise HTTPException(409, "Стойка уже есть")
        s.add(Stand(id=body.id, name=body.name))
        return {"ok": True}

    @app.post("/admin/v1/devices", dependencies=[Depends(admin)])
    def add_device(body: DeviceIn, s: DbSession = Depends(db, scope="function")):
        if not s.get(Stand, body.stand_id):
            raise HTTPException(404, "Нет такой стойки")
        _check_device(s, None, body.stand_id, body.role, body.station, body.series)
        key = new_secret(32)
        dev = Device(stand_id=body.stand_id, name=body.name, key_hash=sha256(key), role=body.role,
                     station=body.station, series=body.series)
        s.add(dev)
        s.flush()
        return {"id": dev.id, "device_key": key, "role": dev.role, "station": dev.station, "series": dev.series,
                "note": "Ключ показывается один раз. Вводится на планшете при настройке."}

    def _check_device(s: DbSession, dev_id: int | None, stand_id: str, role: str, station: str, series: str) -> None:
        if role in ("hall", "reception") and not series:
            raise HTTPException(422, f"Планшету выдачи талонов нужна серия — одна буква из {SERIES}")
        if role == "desk" and series:
            raise HTTPException(422, "Серия талонов задаётся только планшетам зала и приёмной")
        if series:
            other = s.scalar(select(Device).where(Device.stand_id == stand_id, Device.series == series,
                                                  Device.active.is_(True), Device.id != (dev_id or 0)))
            if other:
                raise HTTPException(409, f"Серия {series} уже у устройства «{other.name}» — номера талонов совпадут")
        if role == "desk" and station:
            other = s.scalar(select(Device).where(Device.stand_id == stand_id, Device.role == "desk", Device.station == station,
                                                  Device.active.is_(True), Device.id != (dev_id or 0)))
            if other:
                raise HTTPException(409, f"Стойка {station} уже у устройства «{other.name}» — табло позовёт к двум стойкам сразу")

    @app.patch("/admin/v1/devices/{dev_id}", dependencies=[Depends(admin)])
    def patch_device(dev_id: int, body: DevicePatch, s: DbSession = Depends(db, scope="function")):
        d = s.get(Device, dev_id)
        if not d:
            raise HTTPException(404, "Нет устройства")
        new = body.model_dump(exclude_none=True)
        role, station, series = new.get("role", d.role), new.get("station", d.station), new.get("series", d.series)
        _check_device(s, d.id, d.stand_id, role, station, series)
        for k, v in new.items():
            setattr(d, k, v)
        return {"id": d.id, "name": d.name, "role": d.role, "station": d.station, "series": d.series}

    @app.get("/admin/v1/devices", dependencies=[Depends(admin)])
    def list_devices(s: DbSession = Depends(db, scope="function")):
        return [{"id": d.id, "stand_id": d.stand_id, "name": d.name, "role": d.role, "station": d.station,
                 "series": d.series, "active": d.active} for d in s.scalars(select(Device).order_by(Device.id))]

    @app.put("/admin/v1/ticket-texts/{lang}", dependencies=[Depends(admin)])
    def ticket_texts(lang: Literal["ru", "tj", "uz", "ky"], body: TicketTextIn, s: DbSession = Depends(db, scope="function")):
        row = s.get(Setting, "ticket_texts")
        val = dict(row.value) if row else {}
        val[lang] = {**val.get(lang, {}), **body.model_dump(exclude_none=True)}
        if row:
            row.value = val
        else:
            s.add(Setting(key="ticket_texts", value=val))
        return {"ok": True, "texts": Queue(s, settings.tz, settings.data_key).texts(lang) | val[lang]}

    @app.get("/admin/v1/reports/queue", dependencies=[Depends(admin)])
    def admin_queue_report(stand_id: str, date: str | None = None, s: DbSession = Depends(db, scope="function")):
        qu = Queue(s, settings.tz, settings.data_key)
        if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise HTTPException(422, "Дата в формате ГГГГ-ММ-ДД")
        return qu.report(stand_id, date or qu.today())

    @app.delete("/admin/v1/devices/{dev_id}", dependencies=[Depends(admin)])
    def disable_device(dev_id: int, s: DbSession = Depends(db, scope="function")):
        d = s.get(Device, dev_id)
        if not d:
            raise HTTPException(404, "Нет устройства")
        d.active = False
        for ses in s.scalars(select(Session).where(Session.device_id == dev_id)):
            ses.revoked = True
        return {"ok": True}

    @app.post("/admin/v1/operators", dependencies=[Depends(admin)])
    def add_operator(body: OperatorIn, s: DbSession = Depends(db, scope="function")):
        if not s.get(Stand, body.stand_id):
            raise HTTPException(404, "Нет такой стойки")
        if s.scalar(select(Operator).where(Operator.code == body.code)):
            raise HTTPException(409, "Такой код сотрудника уже есть")
        s.add(Operator(stand_id=body.stand_id, code=body.code, name=body.name, pin_hash=hash_pin(body.pin), role=body.role))
        return {"ok": True}

    @app.patch("/admin/v1/operators/{code}", dependencies=[Depends(admin)])
    def patch_operator(code: str, body: OperatorPatch, s: DbSession = Depends(db, scope="function")):
        op = s.scalar(select(Operator).where(Operator.code == code))
        if not op:
            raise HTTPException(404, "Нет сотрудника")
        if body.pin:
            op.pin_hash, op.failed, op.locked_until = hash_pin(body.pin), 0, None
        if body.active is not None:
            op.active = body.active
        for ses in s.scalars(select(Session).where(Session.operator_id == op.id, Session.revoked.is_(False))):
            ses.revoked = True
        return {"ok": True}

    @app.post("/admin/v1/banks", dependencies=[Depends(admin)])
    def add_bank(body: BankIn, s: DbSession = Depends(db, scope="function")):
        if s.get(Bank, body.id):
            raise HTTPException(409, "Банк уже есть")
        check_hours(body.open_from, body.open_to)
        key, secret = new_secret(32), new_secret(32)
        s.add(Bank(**body.model_dump(), api_key_hash=sha256(key), hmac_secret=secret, status_map={}))
        return {"bank_api_key": key, "hmac_secret": secret,
                "note": "Передайте банку по защищённому каналу. Показывается один раз."}

    @app.post("/admin/v1/banks/{bank_id}/rotate", dependencies=[Depends(admin)])
    def rotate_bank(bank_id: str, s: DbSession = Depends(db, scope="function")):
        b = s.get(Bank, bank_id)
        if not b:
            raise HTTPException(404, "Нет банка")
        key, secret = new_secret(32), new_secret(32)
        b.prev_key_hash, b.prev_hmac_secret = b.api_key_hash, b.hmac_secret
        b.prev_valid_until = datetime.now(timezone.utc) + timedelta(hours=settings.bank_key_grace_hours)
        b.api_key_hash, b.hmac_secret = sha256(key), secret
        s.add(Audit(actor="admin", action="bank.rotate", detail=bank_id))
        return {"bank_api_key": key, "hmac_secret": secret, "old_valid_until": b.prev_valid_until.isoformat()}

    @app.patch("/admin/v1/banks/{bank_id}", dependencies=[Depends(admin)])
    def patch_bank(bank_id: str, body: BankPatch, s: DbSession = Depends(db, scope="function")):
        b = s.get(Bank, bank_id)
        if not b:
            raise HTTPException(404, "Нет банка")
        data = body.model_dump(exclude_none=True)
        if "status_map" in data:
            bad = [v for v in data["status_map"].values() if v not in rules.CODES]
            if bad:
                raise HTTPException(422, f"Неизвестные коды словаря статусов: {', '.join(bad)}")
        if "open_from" in data or "open_to" in data:
            check_hours(data.get("open_from", b.open_from), data.get("open_to", b.open_to))
        if "cert_fingerprint" in data:
            fp = norm_fingerprint(data["cert_fingerprint"])
            if not re.fullmatch(r"[0-9a-f]{64}", fp):
                raise HTTPException(422, "Отпечаток сертификата — SHA-256, 64 шестнадцатеричных символа")
            data["cert_fingerprint"] = fp
        ch = data.pop("snils_channel", None)
        if ch:
            row = s.get(Setting, "snils_channel")
            cur = dict(row.value) if row else {}
            cur[bank_id] = ch
            if row:
                row.value = cur
            else:
                s.add(Setting(key="snils_channel", value=cur))
        for k, v in data.items():
            setattr(b, k, v)
        s.add(Audit(actor="admin", action="bank.patch", detail=f"{bank_id}: {', '.join([*data, *(['snils_channel=' + ch] if ch else [])])}"))
        return {"ok": True}

    @app.put("/admin/v1/policies", dependencies=[Depends(admin)])
    def put_policies(items: list[PolicyIn], s: DbSession = Depends(db, scope="function")):
        for it in items:
            if not s.get(Bank, it.bank_id):
                raise HTTPException(404, f"Нет банка {it.bank_id}")
            p = s.scalar(select(DocPolicy).where(DocPolicy.bank_id == it.bank_id, DocPolicy.service == it.service,
                                                 DocPolicy.country == it.country.upper()))
            if not p:
                p = DocPolicy(bank_id=it.bank_id, service=it.service, country=it.country.upper())
                s.add(p)
            p.rule, p.confirmed, p.version = it.rule, it.confirmed, it.version
        s.add(Audit(actor="admin", action="policies", detail=f"count={len(items)}"))
        return {"ok": True, "count": len(items)}

    @app.post("/admin/v1/consent-texts", dependencies=[Depends(admin)])
    def add_consent_text(body: ConsentTextIn, sv: Svc = Depends(svc)):
        t = sv.add_consent_text(body.model_dump())
        sv.audit("admin", "consent_text.add", None, f"{t.recipient}; {t.version}; {t.sha256[:12]}")
        return sv.text_view(t)

    @app.post("/admin/v1/consent-texts/{text_id}/approve", dependencies=[Depends(admin)])
    def approve_consent_text(text_id: int, body: ApproveIn, s: DbSession = Depends(db, scope="function")):
        t = s.get(ConsentText, text_id)
        if not t:
            raise HTTPException(404, "Нет текста")
        if t.approved_by:
            raise HTTPException(409, "Текст уже утверждён")
        t.approved_by = body.approved_by  # содержимое текста и его хэш не меняются
        s.add(Audit(actor="admin", action="consent_text.approve", detail=f"{text_id}; {body.approved_by}"))
        return {"ok": True}

    @app.post("/admin/v1/consent-texts/{text_id}/deactivate", dependencies=[Depends(admin)])
    def deactivate_consent_text(text_id: int, s: DbSession = Depends(db, scope="function")):
        t = s.get(ConsentText, text_id)
        if not t:
            raise HTTPException(404, "Нет текста")
        t.active = False  # сам текст не удаляется и не меняется: на него ссылаются выданные согласия
        return {"ok": True}

    @app.get("/admin/v1/consents/{consent_id}/evidence", dependencies=[Depends(admin)])
    def consent_evidence(consent_id: int, s: DbSession = Depends(db, scope="function")):
        c = s.get(Consent, consent_id)
        if not c:
            raise HTTPException(404, "Нет согласия")
        s.add(Audit(actor="admin", action="consent.evidence.read", case_id=c.case_id, detail=str(consent_id)))
        return Response(cipher.dec_bytes(c.evidence_enc), media_type=c.evidence_mime,
                        headers={"X-Evidence-Sha256": c.evidence_sha256, "X-Text-Sha256": c.text_sha256})

    @app.post("/admin/v1/banks/{bank_id}/reconcile", dependencies=[Depends(admin)])
    def reconcile(bank_id: str, body: ReconIn, sv: Svc = Depends(svc)):
        b = sv.db.get(Bank, bank_id)
        if not b:
            raise HTTPException(404, "Нет банка")
        return sv.reconcile(b, [r.model_dump() for r in body.rows], body.source_doc)

    @app.get("/admin/v1/rejected-events", dependencies=[Depends(admin)])
    def rejected(limit: int = 200, s: DbSession = Depends(db, scope="function")):
        q = select(RejectedEvent).order_by(RejectedEvent.id.desc()).limit(min(limit, 1000))
        return [{"at": aware(r.received_at).isoformat(), "bank_id": r.bank_id, "event_id": r.event_id, "case_id": r.case_id,
                 "code": r.code, "reason": r.reason} for r in s.scalars(q)]

    @app.put("/admin/v1/settings/notary_hours", dependencies=[Depends(admin)])
    def notary_hours(hours: list[int], s: DbSession = Depends(db, scope="function")):
        if len(hours) != 2 or not (0 <= hours[0] < hours[1] <= 24):
            raise HTTPException(422, "Формат: [с, до], например [9, 20]")
        row = s.get(Setting, "notary_hours")
        if row:
            row.value = hours
        else:
            s.add(Setting(key="notary_hours", value=hours))
        return {"ok": True}

    @app.put("/admin/v1/settings/services_off", dependencies=[Depends(admin)])
    def services_off(items: list[str], s: DbSession = Depends(db, scope="function")):
        bad = [x for x in items if x not in rules.SWITCHABLE]
        if bad:
            raise HTTPException(422, f"Неизвестные услуги: {', '.join(bad)}. Можно: {', '.join(rules.SWITCHABLE)}")
        val = sorted(set(items))
        row = s.get(Setting, "services_off")
        if row:
            row.value = val
        else:
            s.add(Setting(key="services_off", value=val))
        s.add(Audit(actor="admin", action="settings.services_off", detail=",".join(val) or "—"))
        return {"ok": True, "services_off": val}

    @app.get("/admin/v1/audit", dependencies=[Depends(admin)])
    def audit(case_id: str | None = None, limit: int = 200, s: DbSession = Depends(db, scope="function")):
        q = select(Audit).order_by(Audit.id.desc()).limit(min(limit, 1000))
        if case_id:
            q = q.where(Audit.case_id == case_id)
        return [{"at": aware(a.at).isoformat(), "actor": a.actor, "case_id": a.case_id, "action": a.action, "detail": a.detail}
                for a in s.scalars(q)]

    @app.post("/admin/v1/purge", dependencies=[Depends(admin)])
    def purge(days: int = 30, s: DbSession = Depends(db, scope="function")):
        """Удаляет закрытые обращения старше N дней вместе с персональными данными.
        Остаются: журнал действий и события банка (без персональных данных) — для сверки вознаграждения."""
        if days < 1:
            raise HTTPException(422, "days ≥ 1")
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        ids = [c.id for c in s.scalars(select(Case).where(Case.closed_at.is_not(None), Case.closed_at < cutoff))]
        for model in (Consent, Step, Correction, HandoffToken, OperatorClaim):
            s.execute(delete(model).where(model.case_id.in_(ids)))
        s.execute(delete(Case).where(Case.id.in_(ids)))
        s.execute(delete(Session).where(or_(Session.revoked.is_(True), Session.expires_at < cutoff)))
        s.add(Audit(actor="admin", action="purge", detail=f"removed={len(ids)}; days={days}"))
        return {"removed": len(ids)}

    if settings.serve_ui_dir:
        app.mount("/ui", StaticFiles(directory=settings.serve_ui_dir, html=True), name="ui")

    return app


def get_app() -> FastAPI:  # точка входа для uvicorn --factory
    return create_app()
