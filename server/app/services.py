"""Бизнес-логика: обращения, согласия, передача в банк, события банка, сверка, отчёты."""
from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import rules
from .memo import EAEU
from .config import Settings
from .crypto import DataCipher, hmac_hex, sha256b
from .models import (Audit, Bank, BankEvent, Case, Consent, ConsentText, Correction, DocPolicy, HandoffToken,
                     OperatorClaim, RejectedEvent, Setting, Step, Ticket, utcnow)

PREFILL_FIELDS = ["surname", "given", "cyr", "dob", "sex", "country", "number", "expiry"]
MIG_FIELDS = ["series", "number", "entry_date", "kpp", "stay_until"]
EVIDENCE_MAGIC = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "application/pdf": b"%PDF-"}


def png_ok(raw: bytes) -> bool:
    """Структурная проверка PNG: заголовок IHDR с разумными размерами и завершающий блок IEND."""
    import struct
    if len(raw) < 57 or raw[12:16] != b"IHDR" or not raw.rstrip(b"\x00").endswith(b"IEND\xaeB`\x82"):
        return False
    w, h = struct.unpack(">II", raw[16:24])
    return 50 <= w <= 5000 and 30 <= h <= 5000


def snils_ok(d: str) -> bool:
    """Контрольное число СНИЛС (для номеров больше 001-001-998)."""
    n = int(d[:9])
    if n <= 1001998:
        return True
    s = sum(int(x) * (9 - i) for i, x in enumerate(d[:9]))
    s = s % 101
    return (0 if s == 100 else s) == int(d[9:])


def aware(dt: datetime | None) -> datetime | None:
    """SQLite возвращает время без пояса; все времена в базе — UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def consent_text_hash(t: dict) -> str:
    keys = ("recipient", "bank_id", "legal_name", "inn", "address", "purpose", "fields", "retention", "lang", "body", "version")
    canon = json.dumps({k: t.get(k) for k in keys}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256b(canon.encode())


class Svc:
    def __init__(self, db: Session, cipher: DataCipher, settings: Settings) -> None:
        self.db, self.c, self.s = db, cipher, settings
        self.tz = settings.tz

    # ---------- общие ----------
    def audit(self, actor: str, action: str, case_id: str | None = None, detail: str = "") -> None:
        self.db.add(Audit(actor=actor, action=action, case_id=case_id, detail=detail))

    def hour_now(self) -> float:
        n = datetime.now(self.tz)
        return n.hour + n.minute / 60

    def setting(self, key: str, default):
        row = self.db.get(Setting, key)
        return row.value if row else default

    def snils_channel(self, bank_id: str | None) -> str:
        return (self.setting("snils_channel", {}) or {}).get(bank_id or "", "bank")

    def case_snils_channel(self, case: Case) -> str:
        """Канал СНИЛС фиксируется в обращении при передаче в банк: смена настройки банка
        не меняет уже переданные обращения (иначе события банка по ним начнут отклоняться)."""
        fixed = (case.opts or {}).get("snils_channel")
        return fixed if case.sent_at and fixed in rules.SNILS_CHANNELS else self.snils_channel(case.bank_id)

    def _fix_snils_channel(self, case: Case) -> None:
        case.opts = {**(case.opts or {}), "snils_channel": self.snils_channel(case.bank_id)}

    def services_off(self) -> list[str]:
        return [x for x in (self.setting("services_off", []) or []) if x in rules.SWITCHABLE]

    def get_case(self, case_id: str, stand_id: str | None = None) -> Case:
        case = self.db.get(Case, case_id)
        if not case or (stand_id and case.stand_id != stand_id):
            raise HTTPException(404, "Обращение не найдено")
        return case

    def _new_number(self) -> str:
        alphabet = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"
        while True:
            num = "".join(secrets.choice(alphabet) for _ in range(6))
            if not self.db.scalar(select(Case).where(Case.number == num)):
                return num

    # ---------- обращение ----------
    def create_case(self, stand_id: str, actor: str) -> Case:
        """Номер обращения выдаётся вставкой с проверкой уникальности в базе; при совпадении — новая попытка."""
        for _ in range(5):
            case = Case(id=str(uuid.uuid4()), number=self._new_number(), stand_id=stand_id,
                        opts={"dms": "dms" not in self.services_off(), "transfer": False, "phone": False},
                        pay={"dms": "mir", "sim": "bankcard"})
            self.db.add(case)
            try:
                self.db.flush()
            except IntegrityError:
                self.db.rollback()  # в сессии к этому моменту нет других изменений
                continue
            self.audit(actor, "case.create", case.id)
            return case
        raise HTTPException(503, "Не удалось выдать номер обращения — повторите")

    def dup_key(self, passport: dict, country: str | None) -> str:
        # HMAC с отдельным секретом: по утёкшей базе нельзя перебором восстановить номер паспорта
        return hmac_hex(self.s.dup_key, f"{passport.get('number', '')}|{passport.get('dob', '')}|{country}".encode())

    def set_scan(self, case: Case, passport: dict | None, migcard: dict | None, actor: str) -> None:
        if passport is not None:
            case.passport_enc = self.c.enc(passport)
            case.scan_source = passport.get("source")
            case.country = (passport.get("country") or "")[:3].upper() or None
            case.has_ru_text = bool(passport.get("has_ru_text"))
            key = self.dup_key(passport, case.country)
            case.dup_key = key
            dup = self.db.scalar(select(Case).where(Case.dup_key == key, Case.id != case.id, Case.closed_at.is_(None)))
            case.dup_of = dup.number if dup else None
            if case.lang == "ru":
                case.lang = {"TJK": "tj", "UZB": "uz", "KGZ": "ky"}.get(case.country or "", "ru")
            self.audit(actor, "scan.passport", case.id, f"source={passport.get('source', '?')}; dup={'yes' if dup else 'no'}")
        if migcard is not None:
            case.migcard_enc = self.c.enc(migcard)
            purpose = (migcard.get("purpose") or "").lower()
            case.purpose = "patent" if purpose.startswith("работ") else "other"
            self.audit(actor, "scan.migcard", case.id)
        self._touch_screen(case)

    def correct(self, case: Case, section: str, fld: str, value: str, reason: str, actor: str, operator_id: int) -> None:
        data = self.c.dec(case.passport_enc if section == "passport" else case.migcard_enc) or {}
        if fld not in data or fld in ("source", "checks", "has_ru_text", "country"):
            raise HTTPException(400, "Это поле нельзя исправить вручную")
        old = data[fld]
        data[fld] = value
        if section == "passport":
            case.passport_enc = self.c.enc(data)
            if fld in ("number", "dob"):
                case.dup_key = self.dup_key(data, case.country)
                dup = self.db.scalar(select(Case).where(Case.dup_key == case.dup_key, Case.id != case.id, Case.closed_at.is_(None)))
                case.dup_of = dup.number if dup else None
        else:
            case.migcard_enc = self.c.enc(data)
        self.db.add(Correction(case_id=case.id, field=f"{section}.{fld}", old_enc=self.c.enc(old),
                               new_enc=self.c.enc(value), reason=reason, operator_id=operator_id))
        self.audit(actor, "field.correct", case.id, f"{section}.{fld}; reason={reason}")

    # ---------- согласия ----------
    def consent_texts(self) -> list[dict]:
        rows = self.db.scalars(select(ConsentText).where(ConsentText.active.is_(True)).order_by(ConsentText.recipient, ConsentText.id))
        return [self.text_view(t) for t in rows]

    @staticmethod
    def text_view(t: ConsentText) -> dict:
        return {"id": t.id, "recipient": t.recipient, "bank_id": t.bank_id, "legal_name": t.legal_name, "inn": t.inn,
                "address": t.address, "purpose": t.purpose, "fields": t.fields, "retention": t.retention, "lang": t.lang,
                "body": t.body, "sha256": t.sha256, "version": t.version, "approved": bool(t.approved_by),
                "approved_by": t.approved_by}

    def add_consent_text(self, data: dict) -> ConsentText:
        if data["recipient"] == "bank":
            bank = self.db.get(Bank, data.get("bank_id") or "")
            if not bank:
                raise HTTPException(422, "Для согласия банку укажите bank_id существующего банка")
        else:
            data["bank_id"] = None
        h = consent_text_hash(data)
        if self.db.scalar(select(ConsentText).where(ConsentText.sha256 == h)):
            raise HTTPException(409, "Такой текст уже есть")
        t = ConsentText(**data, sha256=h)
        self.db.add(t)
        self.db.flush()
        return t

    def active_consents(self, case: Case) -> set[str]:
        """Действующие согласия. Согласие банку считается только если оно дано именно выбранному банку."""
        out = set()
        for c in case.consents:
            if c.revoked_at is not None:
                continue
            if c.recipient == "bank" and c.bank_id != case.bank_id:
                continue
            out.add(c.recipient)
        return out

    def grant_consent(self, case: Case, d: dict, actor: str, operator_id: int) -> None:
        recipient = d["recipient"]
        if recipient != "simpad" and "simpad" not in self.active_consents(case):
            raise HTTPException(409, "Сначала согласие на обработку данных — без него обслуживание невозможно")
        if recipient in self.active_consents(case):
            raise HTTPException(409, "Согласие этому получателю уже действует")
        t = self.db.get(ConsentText, d["text_id"])
        if not t or not t.active or t.recipient != recipient:
            raise HTTPException(422, "Текст согласия не найден или устарел")
        if not t.approved_by and not self.s.allow_demo_scan:
            raise HTTPException(409, "Текст согласия не утверждён юристом — рабочий сервер его не принимает")
        if recipient == "bank":
            if not case.bank_id:
                raise HTTPException(409, "Сначала выберите банк: согласие даётся конкретному банку")
            if t.bank_id != case.bank_id:
                raise HTTPException(422, "Текст согласия относится к другому банку")
        if d["method"] == "stylus" and d["evidence_mime"] != "image/png":
            raise HTTPException(422, "Подпись стилусом сохраняется как PNG")
        try:
            raw = base64.b64decode(d["evidence_b64"], validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "Доказательство согласия повреждено")
        if not raw or len(raw) > self.s.max_evidence_bytes:
            raise HTTPException(413, f"Файл доказательства пустой или больше {self.s.max_evidence_bytes // 1024 // 1024} МБ")
        if not raw.startswith(EVIDENCE_MAGIC[d["evidence_mime"]]):
            raise HTTPException(422, "Тип файла не совпадает с содержимым")
        if d["evidence_mime"] == "image/png" and not png_ok(raw):
            raise HTTPException(422, "Изображение подписи повреждено или пустое")
        case.consents.append(Consent(recipient=recipient, text_id=t.id, text_sha256=t.sha256, bank_id=t.bank_id,
                                     method=d["method"], explained_lang=d["explained_lang"],
                                     evidence_enc=self.c.enc_bytes(raw), evidence_sha256=sha256b(raw),
                                     evidence_mime=d["evidence_mime"], operator_id=operator_id))
        self.audit(actor, "consent.grant", case.id,
                   f"{recipient}; text={t.version}/{t.sha256[:12]}; method={d['method']}; lang={d['explained_lang']}; evidence={sha256b(raw)[:12]}")
        self._touch_screen(case)

    def revoke_consent(self, case: Case, recipient: str, actor: str) -> None:
        if recipient == "bank" and case.sent_at:
            raise HTTPException(409, "Данные уже переданы банку — отзыв оформляется через банк")
        for c in case.consents:
            if c.recipient == recipient and c.revoked_at is None:
                c.revoked_at = utcnow()
        if recipient == "bank":
            self._cancel_tokens(case)
        self.audit(actor, "consent.revoke", case.id, recipient)

    def _cancel_tokens(self, case: Case) -> None:
        for t in self.db.scalars(select(HandoffToken).where(HandoffToken.case_id == case.id, HandoffToken.used_at.is_(None))):
            t.cancelled = True

    def _touch_screen(self, case: Case) -> None:
        if case.passport_enc and case.migcard_enc and "simpad" in self.active_consents(case):
            self._set_step(case, "screen", "done", "system", "")

    # ---------- шаги ----------
    def _step(self, case: Case, key: str) -> Step:
        for s in case.steps:
            if s.key == key:
                return s
        s = Step(key=key)
        case.steps.append(s)
        return s

    def _set_step(self, case: Case, key: str, status: str, source: str, note: str) -> None:
        s = self._step(case, key)
        s.status, s.source, s.note, s.updated_at = status, source, note, utcnow()

    def update_case(self, case: Case, data: dict, actor: str) -> None:
        if case.closed_at:
            raise HTTPException(409, "Обращение закрыто")
        if "pkg" in data and data["pkg"] != case.pkg and case.sent_at:
            # маршрут, переданный банку, неизменен: по нему проверяются события банка и считаются KPI
            raise HTTPException(409, "Обращение уже передано в банк — пакет услуг не меняется, нужен новый визит")
        if "bank_id" in data and data["bank_id"] != case.bank_id:
            if case.sent_at:
                raise HTTPException(409, "Обращение уже передано в банк — смена банка через новое обращение")
            if not self.db.get(Bank, data["bank_id"]):
                raise HTTPException(400, "Неизвестный банк")
            # согласие давалось конкретному банку: при смене банка оно отзывается, выпущенные QR гаснут
            for c in case.consents:
                if c.recipient == "bank" and c.revoked_at is None:
                    c.revoked_at = utcnow()
                    self.audit(actor, "consent.revoke", case.id, "bank; причина: смена банка")
            self._cancel_tokens(case)
        off = self.services_off()
        if data.get("pkg") and data["pkg"] != case.pkg:
            need = {"card": ["card"], "sim": ["sim"], "full": ["card", "sim"]}[data["pkg"]]
            if any(x in off for x in need):
                raise HTTPException(409, "Этот пакет сейчас не оформляется: нет договора с исполнителем")
        for k, v in (data.get("opts") or {}).items():
            if v and k in off and not (case.opts or {}).get(k):
                raise HTTPException(409, "Эта услуга сейчас не оформляется: нет договора с исполнителем")
        if "pay" in data:
            for svc, m in data["pay"].items():
                if svc not in ("dms", "sim") or m not in rules.PAY_METHODS:
                    raise HTTPException(400, "Неверный способ оплаты")
        for k in ("pkg", "bank_id", "ruid", "lang"):
            if k in data:
                setattr(case, k, data[k])
        if "opts" in data:
            case.opts = {**case.opts, **{k: bool(v) for k, v in data["opts"].items() if k in ("dms", "transfer", "phone")}}  # snils_channel не трогается
        if "pay" in data:
            case.pay = {**case.pay, **data["pay"]}
        self.audit(actor, "case.update", case.id, json.dumps({k: v for k, v in data.items() if k != "lang"}, ensure_ascii=False))

    # ---------- правила ----------
    def doc_rules(self, case: Case) -> dict[str, dict]:
        out = {}
        for svc in rules.SERVICES:
            if case.has_ru_text:
                out[svc] = {"rule": "no", "confirmed": True, "version": "паспорт с русским текстом"}
                continue
            p = None
            if case.bank_id and case.country:
                p = self.db.scalar(select(DocPolicy).where(DocPolicy.bank_id == case.bank_id, DocPolicy.service == svc,
                                                           DocPolicy.country == case.country))
            out[svc] = {"rule": p.rule, "confirmed": p.confirmed, "version": p.version} if p else \
                {"rule": "unknown", "confirmed": False, "version": ""}
        return out

    def ctx(self, case: Case, online: bool = True) -> rules.Ctx:
        bank = self.db.get(Bank, case.bank_id) if case.bank_id else None
        notary = self.setting("notary_hours", [9, 20])
        opts = dict(case.opts)
        # граждане ЕАЭС работают без патента — подсказка про ДМС для патента им не нужна
        opts["patent_hint"] = case.purpose == "patent" and (case.country or "") not in EAEU
        return rules.Ctx(pkg=case.pkg, opts=opts, pay=case.pay or {}, has_ru_text=case.has_ru_text,
                         doc_rules={k: v["rule"] for k, v in self.doc_rules(case).items()},
                         card_state=case.card_state, consents=self.active_consents(case),
                         status={s.key: s.status for s in case.steps}, bank_name=bank.name if bank else None,
                         bank_mode=bank.mode if bank else None, bank_open=(bank.open_from, bank.open_to) if bank else None,
                         notary_open=(notary[0], notary[1]), hour=self.hour_now(), sent=bool(case.sent_at),
                         online=online, scanned=bool(case.passport_enc and case.migcard_enc),
                         snils_channel=self.case_snils_channel(case))

    def view(self, case: Case) -> dict:
        ctx = self.ctx(case)
        steps = []
        for k in rules.steps_for(ctx):
            st = next((s for s in case.steps if s.key == k), None)
            own = rules.owner(ctx, k)
            steps.append({"key": k, **rules.STEPS[k], "owner": own, "auto": own == "bank",
                          "doc_label": rules.DOC_STEPS.get(k, "") if rules.operator_can_mark(ctx, k) else "",
                          "status": st.status if st else "todo",
                          "note": self._note(st.note) if st else "", "source": st.source if st else "",
                          "updated_at": aware(st.updated_at).isoformat() if st else None,
                          "blockers": rules.blockers(ctx, k), "operator_can_mark": rules.operator_can_mark(ctx, k)})
        dr = self.doc_rules(case)
        corrections = [{"field": c.field, "old": self.c.dec(c.old_enc), "new": self.c.dec(c.new_enc), "reason": c.reason,
                        "at": aware(c.at).isoformat()} for c in self.db.scalars(select(Correction).where(Correction.case_id == case.id))]
        texts = {t.id: t for t in self.db.scalars(select(ConsentText).where(ConsentText.id.in_([c.text_id for c in case.consents])))}
        claims = [{"code": c.code, "note": self._note(c.note), "at": aware(c.at).isoformat()}
                  for c in self.db.scalars(select(OperatorClaim).where(OperatorClaim.case_id == case.id).order_by(OperatorClaim.id))]
        return {
            "id": case.id, "number": case.number, "created_at": aware(case.created_at).isoformat(), "lang": case.lang,
            "purpose": case.purpose, "pkg": case.pkg, "opts": case.opts, "pay": case.pay, "bank_id": case.bank_id,
            "ruid": case.ruid, "passport": self.c.dec(case.passport_enc), "migcard": self.c.dec(case.migcard_enc),
            "scan_source": case.scan_source, "imei_set": bool(case.imei_enc), "dup_of": case.dup_of,
            "card_state": case.card_state,
            "sent_at": aware(case.sent_at).isoformat() if case.sent_at else None, "sent_mode": case.sent_mode,
            "bank_case_id": case.bank_case_id,
            "need_transl": rules.need_transl(ctx),
            "doc_rules": dr, "card_rule": dr["card"],
            "consents": [{"recipient": c.recipient, "bank_id": c.bank_id,
                          "legal_name": texts[c.text_id].legal_name if c.text_id in texts else "",
                          "purpose": texts[c.text_id].purpose if c.text_id in texts else "",
                          "fields": texts[c.text_id].fields if c.text_id in texts else [],
                          "text_version": texts[c.text_id].version if c.text_id in texts else "",
                          "text_sha256": c.text_sha256, "method": c.method, "explained_lang": c.explained_lang,
                          "evidence_sha256": c.evidence_sha256,
                          "valid": c.revoked_at is None and (c.recipient != "bank" or c.bank_id == case.bank_id),
                          "granted_at": aware(c.granted_at).isoformat(),
                          "revoked_at": aware(c.revoked_at).isoformat() if c.revoked_at else None}
                         for c in case.consents],
            "claims": claims,
            "steps": steps, "warnings": rules.warnings(ctx), "plan": rules.plan(ctx),
            "corrections": corrections, "closed_at": aware(case.closed_at).isoformat() if case.closed_at else None,
            "ticket": self._ticket(case), "snils_channel": ctx.snils_channel, "eaeu": (case.country or "") in EAEU,
        }

    def _ticket(self, case: Case) -> dict | None:
        from .queue import display  # здесь, чтобы не было циклического импорта
        t = self.db.get(Ticket, case.ticket_id) if case.ticket_id else None
        return {"id": t.id, "number": display(t.number), "status": t.status, "station": t.station,
                "station_kind": t.station_kind} if t else None

    def _note(self, v: str | None) -> str:
        """Комментарии сотрудников хранятся зашифрованными (могут содержать ПДн)."""
        if not v:
            return ""
        try:
            return self.c.dec(v)
        except RuntimeError:
            return "[комментарий не расшифрован]"

    # ---------- шаги оператора ----------
    def operator_step(self, case: Case, key: str, status: str, note: str, actor: str, imei: str | None = None,
                      doc: str = "") -> None:
        if key not in rules.STEPS:
            raise HTTPException(400, "Неизвестный шаг")
        if case.closed_at:
            raise HTTPException(409, "Обращение закрыто")
        ctx = self.ctx(case)
        if not rules.operator_can_mark(ctx, key):
            raise HTTPException(403, "Этот шаг выполняет банк — статус приходит только от банка")
        if key == "snils" and ctx.status.get("snils") == "done":
            # от подтверждённого СНИЛС зависят принятые события банка (ЕБС, SIM) — отметка не меняется
            raise HTTPException(409, "СНИЛС уже подтверждён — отметку изменить нельзя")
        if key not in rules.steps_for(ctx):
            raise HTTPException(400, "Шага нет в маршруте клиента")
        if status in ("work", "done"):
            bl = rules.blockers(ctx, key)
            if bl:
                raise HTTPException(409, "; ".join(bl))
        if key == "imei" and status == "done":
            if not imei or not imei.isdigit() or len(imei) != 15:
                raise HTTPException(400, "IMEI — 15 цифр")
            case.imei_enc = self.c.enc(imei)
        note = (note or "").strip()
        if status == "fail" and len(note) < 5:
            raise HTTPException(422, "Укажите причину: без неё отметку «не удалось» поставить нельзя")
        doc = (doc or "").strip()
        if status == "done" and key in rules.DOC_STEPS:
            doc = self._check_doc(key, doc)
            note = f"№ {doc}" + (f" · {note}" if note else "")
        elif doc:
            raise HTTPException(422, "Номер документа указывается только вместе с отметкой «выполнено»")
        self._set_step(case, key, status, "sfr" if key == "snils" else "operator", self.c.enc(note) if note else "")
        # сам номер документа в журнал не пишем: журнал хранится открытым текстом
        self.audit(actor, "step.operator", case.id, f"{key}={status}" + ("; документ указан" if doc else ""))

    @staticmethod
    def _check_doc(key: str, doc: str) -> str:
        label = rules.DOC_STEPS[key]
        if key == "snils":
            d = "".join(ch for ch in doc if ch.isdigit())
            if len(d) != 11 or not snils_ok(d):
                raise HTTPException(422, "СНИЛС — 11 цифр с верной контрольной суммой")
            return "СНИЛС ***-***-" + d[6:9] + " " + d[9:]  # целиком не храним: достаточно для сверки с уведомлением
        if not re.fullmatch(r"[0-9A-Za-zА-Яа-яЁё№/. -]{3,40}", doc) or not any(ch.isdigit() for ch in doc):
            raise HTTPException(422, f"{label}: без него отметку «выполнено» поставить нельзя (3–40 символов, с цифрами)")
        return doc

    def operator_claim(self, case: Case, code: str, note: str, actor: str, operator_id: int) -> None:
        """Статус «со слов сотрудника банка». Хранится отдельно и НЕ меняет шаги, KPI и расчёты с банком."""
        if code not in rules.CODES:
            raise HTTPException(400, "Неизвестный код статуса")
        if not case.sent_at:
            raise HTTPException(409, "Обращение ещё не передано в банк")
        self.db.add(OperatorClaim(case_id=case.id, code=code, note=self.c.enc(note) if note else "", operator_id=operator_id))
        self.audit(actor, "claim." + code, case.id)  # текст комментария в журнал не пишем: может содержать ПДн

    # ---------- передача в банк ----------
    def preview(self, case: Case) -> dict:
        p = self.c.dec(case.passport_enc) or {}
        m = self.c.dec(case.migcard_enc) or {}
        bank = self.db.get(Bank, case.bank_id) if case.bank_id else None
        if not bank:
            raise HTTPException(409, "Банк не выбран")
        send = {k: p.get(k) for k in PREFILL_FIELDS}
        send.update({f"migcard_{k}": m.get(k) for k in MIG_FIELDS})
        return {"bank": {"id": bank.id, "name": bank.name, "mode": bank.mode, "legal_name": bank.legal_name},
                "case_number": case.number, "route": case.pkg,
                "fields": send if bank.mode == "api" else {},
                "note": "Банк сверяет оригиналы и проводит собственную идентификацию. Сканы, фото и биометрия не передаются."
                        if bank.mode == "api" else
                        "Режим «номер дела»: банк получает только номер обращения и маршрут, оригиналы сканирует своим сканером."}

    def recheck(self, case: Case) -> list[str]:
        issues = []
        have = self.active_consents(case)
        if "simpad" not in have:
            issues.append("Нет действующего согласия на обработку данных")
        if "bank" not in have:
            issues.append("Нет действующего согласия на передачу выбранному банку")
        if case.dup_of:
            dup = self.db.scalar(select(Case).where(Case.number == case.dup_of, Case.closed_at.is_(None)))
            if dup and dup.sent_at:
                issues.append(f"Дубль: обращение {dup.number} уже передано в банк")
        if case.closed_at:
            issues.append("Обращение закрыто")
        return issues

    def _handoff_checks(self, case: Case) -> Bank:
        ctx = self.ctx(case)
        if ctx.status.get("screen") != "done":
            raise HTTPException(409, "Сначала сканирование и согласия")
        if not case.pkg:
            raise HTTPException(409, "Не выбран пакет услуг")
        bank = self.db.get(Bank, case.bank_id) if case.bank_id else None
        if not bank or not bank.active:
            raise HTTPException(409, "Банк не выбран или отключён")
        if not rules.is_open(ctx.hour, (bank.open_from, bank.open_to)):
            raise HTTPException(409, f"{bank.name} на стойке работает {rules.span_text((bank.open_from, bank.open_to))}")
        if case.sent_at:
            raise HTTPException(409, "Обращение уже передано в банк")
        issues = self.recheck(case)
        if issues:
            raise HTTPException(409, "; ".join(issues))
        return bank

    def issue_handoff(self, case: Case, actor: str) -> dict:
        bank = self._handoff_checks(case)
        self._cancel_tokens(case)
        raw = secrets.token_urlsafe(24)
        exp = utcnow() + timedelta(seconds=self.s.token_ttl_sec)
        self.db.add(HandoffToken(handoff_id=str(uuid.uuid4()), case_id=case.id, bank_id=bank.id,
                                 token_hash=sha256b(raw.encode()), expires_at=exp))
        self.audit(actor, "handoff.issue", case.id, f"bank={bank.id}; mode={bank.mode}")
        # В QR — только непрозрачный токен. Персональных данных нет.
        return {"qr": f"OKNO1:{raw}", "expires_at": exp.isoformat(), "mode": bank.mode, "bank": bank.name,
                "case_number": case.number}

    def manual_handoff(self, case: Case, actor: str) -> dict:
        """Режим «номер дела» без интеграции: сотрудник банка принял номер обращения и сканирует оригиналы сам.
        Персональные данные из системы в банк не уходят. Статусы — только из файла сверки или API событий банка."""
        bank = self._handoff_checks(case)
        if bank.mode != "case":
            raise HTTPException(409, "Для банка с API передача только через QR и запрос банка")
        self._cancel_tokens(case)
        case.sent_at, case.sent_mode, case.handoff_id = utcnow(), "case", str(uuid.uuid4())
        self._fix_snils_channel(case)
        self.audit(actor, "handoff.manual", case.id, f"bank={bank.id}")
        return {"ok": True, "case_number": case.number, "handoff_id": case.handoff_id}

    def redeem(self, bank: Bank, qr: str) -> dict:
        raw = qr.split(":", 1)[1] if qr.startswith("OKNO1:") else qr
        t = self.db.scalar(select(HandoffToken).where(HandoffToken.token_hash == sha256b(raw.encode())))
        now = utcnow()
        if not t or t.bank_id != bank.id:
            raise HTTPException(404, "Токен не найден")
        if t.cancelled or t.used_at:
            raise HTTPException(410, "Токен уже использован или отменён")
        if aware(t.expires_at) < now:
            raise HTTPException(410, "Срок токена истёк")
        case = self.db.get(Case, t.case_id)
        if case.bank_id != bank.id:
            raise HTTPException(404, "Токен не найден")
        issues = self.recheck(case)
        if issues:
            raise HTTPException(409, "; ".join(issues))
        # Атомарно: из двух одновременных запросов с одним QR выиграет ровно один
        res = self.db.execute(update(HandoffToken)
                              .where(HandoffToken.id == t.id, HandoffToken.used_at.is_(None), HandoffToken.cancelled.is_(False))
                              .values(used_at=now))
        if res.rowcount != 1:
            raise HTTPException(410, "Токен уже использован или отменён")
        res = self.db.execute(update(Case).where(Case.id == case.id, Case.sent_at.is_(None))
                              .values(sent_at=now, sent_mode=bank.mode, handoff_id=t.handoff_id))
        if res.rowcount != 1:
            raise HTTPException(409, "Обращение уже передано в банк")
        self.db.refresh(case)
        self._fix_snils_channel(case)
        self.audit(f"bank:{bank.id}", "handoff.redeem", case.id, f"mode={bank.mode}")
        body = {"case_id": case.id, "case_number": case.number, "handoff_id": t.handoff_id, "route": case.pkg,
                "services": [k for k in rules.bank_services_ctx(self.ctx(case)) if k in rules.steps_for(self.ctx(case))],
                "consent": self._bank_consent_ref(case)}
        if bank.mode == "api":
            p = self.c.dec(case.passport_enc) or {}
            m = self.c.dec(case.migcard_enc) or {}
            body["prefill"] = {**{k: p.get(k) for k in PREFILL_FIELDS}, **{f"migcard_{k}": m.get(k) for k in MIG_FIELDS}}
            if case.imei_enc:
                body["prefill"]["imei"] = self.c.dec(case.imei_enc)
        return body

    def _bank_consent_ref(self, case: Case) -> dict:
        c = next((c for c in case.consents if c.recipient == "bank" and c.revoked_at is None and c.bank_id == case.bank_id), None)
        if not c:
            return {}
        t = self.db.get(ConsentText, c.text_id)
        return {"recipient": t.legal_name, "inn": t.inn, "text_version": t.version, "text_sha256": c.text_sha256,
                "method": c.method, "granted_at": aware(c.granted_at).isoformat()}

    # ---------- события банка ----------
    def _reject(self, bank_id: str, ev: dict, body_hash: str, status: int, reason: str) -> None:
        """Отклонённое событие сохраняется для разбора, состояние обращения не меняется:
        все изменения текущей обработки откатываются, записывается только сам отказ."""
        self.db.rollback()
        self.db.add(RejectedEvent(bank_id=bank_id, event_id=str(ev.get("event_id", ""))[:64],
                                  case_id=(ev.get("case_id") or None), code=str(ev.get("bank_code", ""))[:64],
                                  reason=reason, body_sha256=body_hash))
        self.audit(f"bank:{bank_id}", "event.reject", None, f"{ev.get('event_id')}: {reason}")
        self.db.commit()
        raise HTTPException(status, reason)

    def bank_event(self, bank: Bank, ev: dict, raw_body: bytes, via: str = "api") -> dict:
        body_hash = sha256b(raw_body)
        exist = self.db.scalar(select(BankEvent).where(BankEvent.bank_id == bank.id, BankEvent.event_id == ev["event_id"]))
        if exist:
            if exist.body_sha256 == body_hash or via == "recon" and exist.code == (bank.status_map or {}).get(ev["bank_code"], ev["bank_code"]):
                return {"status": "duplicate", "code": exist.code}
            self._reject(bank.id, ev, body_hash, 409, "event_id уже использован с другим содержимым")
        code = (bank.status_map or {}).get(ev["bank_code"], ev["bank_code"])
        if code not in rules.CODES:
            self._reject(bank.id, ev, body_hash, 422, f"Код {ev['bank_code']} не сопоставлен со словарём статусов")
        case = self.db.get(Case, ev["case_id"]) if ev.get("case_id") else None
        if not case or case.bank_id != bank.id:
            # одинаковый ответ для «нет такого» и «чужое» — нельзя перебором узнать чужие обращения
            self._reject(bank.id, ev, body_hash, 403, "Обращение не передавалось этому банку")
        if via == "api":
            if not case.sent_at or not case.handoff_id or case.handoff_id != ev.get("handoff_id"):
                self._reject(bank.id, ev, body_hash, 409, "handoff_id не совпадает с переданным этому банку обращением")
        elif not case.sent_at or "bank" not in self.active_consents(case):
            self._reject(bank.id, ev, body_hash, 409, "Обращение не отмечено как переданное в банк или нет согласия на передачу этому банку")
        occurred = datetime.fromisoformat(ev["occurred_at"]).astimezone(timezone.utc)
        now = utcnow()
        if occurred > now + timedelta(minutes=5) or occurred < aware(case.created_at) - timedelta(minutes=5):
            self._reject(bank.id, ev, body_hash, 422, "Время события раньше создания обращения или в будущем")
        # Атомарная привязка bank_case_id (compare-and-set). В PostgreSQL этот UPDATE блокирует строку обращения
        # до конца транзакции: события по одному обращению обрабатываются строго по очереди.
        bound = self.db.execute(update(Case)
                                .where(Case.id == case.id,
                                       or_(Case.bank_case_id.is_(None), Case.bank_case_id == ev["bank_case_id"]))
                                .values(bank_case_id=ev["bank_case_id"])).rowcount
        if bound != 1:
            self._reject(bank.id, ev, body_hash, 409, "bank_case_id не совпадает с ранее присланным по этому обращению")
        self.db.refresh(case)
        svc = rules.CODE_SERVICE.get(code)
        sfr = self.case_snils_channel(case) == "sfr"
        if svc == "snils" and sfr:
            self._reject(bank.id, ev, body_hash, 409, "СНИЛС по этому банку подтверждается через Соцфонд, а не банком")
        if svc and svc not in rules.bank_services(case.pkg):
            self._reject(bank.id, ev, body_hash, 409, f"Услуги «{rules.STEPS[svc]['title']}» нет в маршруте клиента")
        # Предпосылка засчитывается, только если она подтверждена этим же банком И произошла не позже текущего события
        prior = self.db.execute(select(BankEvent.code, BankEvent.occurred_at)
                                .where(BankEvent.case_id == case.id, BankEvent.bank_id == bank.id)).all()
        limit = occurred + timedelta(seconds=rules.CLOCK_SKEW_SEC)
        missing = [p for p in rules.EVENT_PREREQ.get(code, [])
                   if not (sfr and p == "ESIA_CONFIRMED") and not any(c == p and aware(t) <= limit for c, t in prior)]
        if sfr and "ESIA_CONFIRMED" in rules.EVENT_PREREQ.get(code, []):
            # предпосылку заменяет СНИЛС, подтверждённый сотрудником по уведомлению Соцфонда до этого события
            sn = next((s for s in case.steps if s.key == "snils"), None)
            if not sn or sn.status != "done" or aware(sn.updated_at) > limit:
                missing.append("СНИЛС (Соцфонд)")
        if missing:
            self._reject(bank.id, ev, body_hash, 409,
                         f"Нарушен обязательный порядок: {code} раньше {', '.join(missing)} (по факту получения или по времени occurred_at)")
        act = rules.CODES[code]
        if act and act[1] == "fail":
            cur = next((s.status for s in case.steps if s.key == act[0]), "todo")
            if cur == "done":
                self._reject(bank.id, ev, body_hash, 409, "Отказ противоречит ранее подтверждённому результату")
        self.db.add(BankEvent(bank_id=bank.id, event_id=ev["event_id"], case_id=case.id,
                              handoff_id=ev.get("handoff_id") or f"recon:{ev.get('source_doc', '')}"[:36],
                              bank_case_id=ev["bank_case_id"], bank_code=ev["bank_code"], code=code,
                              schema_version=ev.get("schema_version", "1"),
                              occurred_at=occurred, body_sha256=body_hash))
        try:
            self.db.flush()
        except IntegrityError:  # одновременно пришёл тот же event_id
            self.db.rollback()
            other = self.db.scalar(select(BankEvent).where(BankEvent.bank_id == bank.id, BankEvent.event_id == ev["event_id"]))
            if other and other.body_sha256 == body_hash:
                return {"status": "duplicate", "code": code}
            self._reject(bank.id, ev, body_hash, 409, "event_id уже использован с другим содержимым")
        if act:
            key, st = act
            cur = next((s.status for s in case.steps if s.key == key), "todo")
            if not (cur == "done" and st == "work"):  # запоздавшее «в работе» не откатывает подтверждённый результат
                # свободный текст банка не храним: в нём могут оказаться персональные данные
                self._set_step(case, key, st, "bank" if via == "api" else "bank_file", "")
            if code == "CARD_ISSUED" and case.card_state == "none":
                case.card_state = "issued"
            if code == "CARD_ACTIVE":
                case.card_state = "active"
        self.audit(f"bank:{bank.id}", f"event.{code}", case.id, f"via={via}; event={ev['event_id']}")
        return {"status": "accepted", "code": code}

    def reconcile(self, bank: Bank, rows: list[dict], source_doc: str) -> dict:
        """Официальный файл сверки банка (режим «номер дела» без API событий)."""
        ok, dup, rejected = 0, 0, []
        for r in rows:
            case = self.db.scalar(select(Case).where(Case.number == r["case_number"]))
            ev = {"event_id": r["event_id"], "case_id": case.id if case else "", "bank_case_id": r["bank_case_id"],
                  "bank_code": r["bank_code"], "occurred_at": r["occurred_at"], "schema_version": "recon",
                  "source_doc": source_doc, "note": f"сверка: {source_doc}"}
            raw = json.dumps({**r, "source_doc": source_doc}, ensure_ascii=False, sort_keys=True).encode()
            try:
                res = self.bank_event(bank, ev, raw, via="recon")
                self.db.commit()
                if res["status"] == "accepted":
                    ok += 1
                else:
                    dup += 1
            except HTTPException as e:
                rejected.append({"event_id": r["event_id"], "reason": e.detail})
        return {"accepted": ok, "duplicate": dup, "rejected": rejected}

    # ---------- связь с банками ----------
    def bank_link(self, bank: Bank, now: datetime | None = None) -> dict:
        """Состояние обмена с банком — по последнему подписанному запросу банка или принятому файлу сверки.
        none — обмена не было ни разу; ok — был за последние 24 часа; wait — был раньше, сейчас тишина."""
        now = now or utcnow()
        seen = self.setting(f"bank_seen:{bank.id}", None)
        last = datetime.fromisoformat(seen["at"]) if seen else None
        ev = self.db.scalar(select(func.max(BankEvent.received_at)).where(BankEvent.bank_id == bank.id))
        if ev and (last is None or aware(ev) > last):
            last = aware(ev)
        if last is None:
            return {"link": "none", "link_text": "не подключён", "last_seen": None}
        if now - last <= timedelta(hours=24):
            return {"link": "ok", "link_text": "на связи", "last_seen": last.isoformat()}
        return {"link": "wait", "link_text": "нет обмена больше суток", "last_seen": last.isoformat()}

    # ---------- памятка и отчёт ----------
    def memo(self, case: Case, lang: str) -> dict:
        from .memo import build_memo
        bank = self.db.get(Bank, case.bank_id) if case.bank_id else None
        return build_memo(case, self.view(case), bank.name if bank else None, lang, self.case_snils_channel(case))

    def shift_report(self, stand_id: str, day: datetime) -> dict:
        """KPI только по подтверждённым банком событиям. Статусы «со слов банка» показываются отдельно."""
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        cases = list(self.db.scalars(select(Case).where(Case.stand_id == stand_id, Case.created_at >= start.astimezone(timezone.utc),
                                                        Case.created_at < end.astimezone(timezone.utc))))
        total = len(cases)
        sent = sum(1 for c in cases if c.sent_at)
        ids = [c.id for c in cases] or ["-"]
        verified = {(e.case_id, e.code) for e in self.db.scalars(select(BankEvent).where(BankEvent.case_id.in_(ids)))}
        want_sim = [c for c in cases if c.pkg in ("sim", "full")]
        sim_done = sum(1 for c in want_sim if (c.id, "SIM_ISSUED") in verified)
        failed = sum(1 for c in cases if any(s.status == "fail" for s in c.steps))
        no_ticket = sum(1 for c in cases if not c.ticket_id)
        corr = self.db.scalar(select(func.count(Correction.id)).where(Correction.case_id.in_(ids))) or 0
        claims = self.db.scalar(select(func.count(OperatorClaim.id)).where(OperatorClaim.case_id.in_(ids))) or 0
        by_bank: dict[str, dict] = {}
        for c in cases:
            if not c.bank_id:
                continue
            b = by_bank.setdefault(c.bank_id, {"cases": 0, "cards": 0, "sims": 0})
            b["cases"] += 1
            b["cards"] += (c.id, "CARD_ISSUED") in verified
            b["sims"] += (c.id, "SIM_ISSUED") in verified
        return {"date": start.date().isoformat(), "cases": total, "reached_bank": sent,
                "reached_bank_pct": round(sent / total * 100) if total else 0,
                "sim_chain_pct": round(sim_done / len(want_sim) * 100) if want_sim else None,
                "with_failures": failed, "with_ticket": total - no_ticket, "without_ticket": no_ticket, "corrections": corr, "unverified_claims": claims, "by_bank": by_bank,
                "basis": "только подтверждённые события банка и файлы сверки"}
