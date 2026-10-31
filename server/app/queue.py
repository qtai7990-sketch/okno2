"""Электронная очередь: талон в зале → регистрация в офисе → вызов на табло.

Персональных данных здесь нет: только номер талона, язык, время, статус и номер стойки.
QR на талоне — 16 случайных цифр (только цифры: ручной сканер-«клавиатура» не исказит их раскладкой);
в базе хранится только хэш. Очередь в офисе — по времени прихода в офис, а не по номеру талона.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .crypto import sha256
from .models import Audit, Bank, Case, Device, Operator, Setting, Ticket, TicketCounter

# Серии — только буквы, одинаково выглядящие в латинице и кириллице: номер с талона нельзя набрать «не той» буквой.
SERIES = "ABEKMHPCTX"
CYR2LAT = str.maketrans("АВЕКМНРСТХавекмнрстх", "ABEKMHPCTXABEKMHPCTX")
LAT2CYR = str.maketrans("ABEKMHPCTX", "АВЕКМНРСТХ")
TOKEN_DIGITS = 16
LANGS = ("ru", "tj", "uz", "ky")
ACTIVE = ("waiting", "called", "serving", "bank")

KIND = "Талон электронной очереди офиса. Не является документом аэропорта."

TEXTS = {
    "ru": {"your": "Ваш номер", "route": "Поднимитесь на 2 этаж. Покажите талон на входе и ждите вызова на табло.",
           "valid": "Талон действует до конца дня."},
    "tj": {"your": "Рақами шумо", "route": "Ба ошёнаи 2 бароед. Талонро дар даромадгоҳ нишон диҳед ва даъватро дар табло интизор шавед.",
           "valid": "Талон то охири рӯз эътибор дорад."},
    "uz": {"your": "Sizning raqamingiz", "route": "2-qavatga chiqing. Kirishda talonni ko‘rsating va tablodagi chaqiruvni kuting.",
           "valid": "Talon kun oxirigacha amal qiladi."},
    "ky": {"your": "Сиздин номериңиз", "route": "2-кабатка чыгыңыз. Кире бериште талонду көрсөтүп, таблодо чакырууну күтүңүз.",
           "valid": "Талон күндүн аягына чейин жарактуу."},
}


def iso(dt: datetime | None) -> str | None:
    """Время всегда с часовым поясом: SQLite возвращает «наивное» UTC-время."""
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def display(number: str) -> str:
    """A317 → А317: на талоне и табло — кириллица, как её прочитает и объявит русский голос."""
    return number.translate(LAT2CYR)


def normalize(code: str) -> str:
    return "".join(ch for ch in code.strip().upper().translate(CYR2LAT) if ch.isalnum())


class Queue:
    def __init__(self, db: Session, tz, secret: str = "") -> None:
        self.db, self.tz = db, tz
        self._qr_key = hmac.new(secret.encode(), b"okno-ticket-qr-v1", hashlib.sha256).digest()

    def token(self, t: Ticket) -> str:
        """QR талона выводится из его id секретом сервера (производный от ключа шифрования данных, который не меняется:
        его смена сделала бы базу нечитаемой): повторная выдача и допечатка дают тот же QR, в базе — только хэш."""
        n = int.from_bytes(hmac.new(self._qr_key, t.id.encode(), hashlib.sha256).digest()[:8], "big")
        return f"{n % 10 ** TOKEN_DIGITS:0{TOKEN_DIGITS}d}"

    # ---------- служебное ----------
    def today(self) -> str:
        return datetime.now(self.tz).date().isoformat()

    def texts(self, lang: str) -> dict:
        base = dict(TEXTS.get(lang, TEXTS["ru"]))
        base["kind"] = KIND  # всегда по-русски: для сотрудников аэропорта
        row = self.db.get(Setting, "ticket_texts")
        over = (row.value if row else {}).get(lang, {})
        base.update({k: v for k, v in over.items() if k in base and isinstance(v, str) and v})
        return base

    def _audit(self, actor: str, action: str, detail: str = "", case_id: str | None = None) -> None:
        self.db.add(Audit(actor=actor, action=action, detail=detail, case_id=case_id))

    def _get(self, stand_id: str, ticket_id: str) -> Ticket:
        t = self.db.get(Ticket, ticket_id)
        if not t or t.stand_id != stand_id:
            raise HTTPException(404, "Нет такого талона")
        return t

    @staticmethod
    def brief(t: Ticket) -> dict:
        return {"id": t.id, "number": display(t.number), "lang": t.lang, "status": t.status, "source": t.source,
                "issued_at": iso(t.issued_at), "checked_in_at": iso(t.checked_in_at), "called_at": iso(t.called_at),
                "station": t.station, "station_kind": t.station_kind, "call_count": t.call_count, "case_id": t.case_id}

    # ---------- выдача ----------
    def _by_key(self, dev: Device, key: str | None) -> Ticket | None:
        if not key:
            return None
        return self.db.scalar(select(Ticket).where(Ticket.issued_device_id == dev.id, Ticket.idem_key == key))

    def issue(self, dev: Device, op: Operator, lang: str, source: str, actor: str,
              key: str | None = None) -> tuple[Ticket, str, bool]:
        """Выдача талона. Повтор запроса с тем же Idempotency-Key возвращает тот же талон (replay=True)."""
        old = self._by_key(dev, key)
        if old:
            return old, self.token(old), True
        if not dev.series:
            raise HTTPException(409, "У планшета не задана серия талонов — обратитесь к администратору")
        if lang not in LANGS:
            raise HTTPException(422, "Язык: ru, tj, uz или ky")
        day = self.today()
        for _ in range(5):
            seq = self._next_seq(dev.stand_id, day, dev.series)
            t = Ticket(id=str(uuid.uuid4()), stand_id=dev.stand_id, day=day, series=dev.series, seq=seq,
                       number=f"{dev.series}{seq:03d}", lang=lang, source=source, idem_key=key,
                       issued_device_id=dev.id, issued_operator_id=op.id,
                       status="waiting" if source == "reception" else "issued")
            token = self.token(t)
            t.token_hash = sha256(token)
            if source == "reception":
                t.checked_in_at = datetime.now(timezone.utc)  # выдан в самом офисе — сразу в очереди
            self.db.add(t)
            try:
                self.db.flush()
            except IntegrityError:
                self.db.rollback()
                old = self._by_key(dev, key)  # тот же запрос пришёл дважды одновременно — отдаём уже выданный
                if old:
                    return old, self.token(old), True
                continue
            self._audit(actor, "ticket.issue", f"{t.number} {lang} {source}")
            return t, token, False
        raise HTTPException(503, "Не удалось выдать номер — повторите")

    def _next_seq(self, stand_id: str, day: str, series: str) -> int:
        """Следующий номер серии: атомарное увеличение счётчика (строка блокируется до конца транзакции)."""
        for _ in range(3):
            seq = self.db.execute(update(TicketCounter).where(
                TicketCounter.stand_id == stand_id, TicketCounter.day == day, TicketCounter.series == series)
                .values(last=TicketCounter.last + 1).returning(TicketCounter.last)).scalar()
            if seq is not None:
                return seq
            self.db.add(TicketCounter(stand_id=stand_id, day=day, series=series, last=0))
            try:
                self.db.flush()
            except IntegrityError:
                self.db.rollback()  # счётчик только что создал параллельный запрос — берём номер из него
        raise HTTPException(503, "Не удалось выдать номер — повторите")

    def printed(self, dev: Device, ticket_id: str, actor: str) -> dict:
        t = self._get(dev.stand_id, ticket_id)
        if t.issued_device_id != dev.id:
            raise HTTPException(403, "Отметить печать может только планшет, выдавший талон")
        if not t.printed_at:
            t.printed_at = datetime.now(timezone.utc)
            self._audit(actor, "ticket.printed", t.number)
        return {"ok": True}

    def ticket_out(self, t: Ticket, token: str) -> dict:
        end = datetime.combine(datetime.fromisoformat(t.day).date(), datetime.max.time(), self.tz)
        return {**self.brief(t), "qr": token, "day": t.day, "valid_until": end.isoformat(), "print": self.texts(t.lang),
                "ahead": self.waiting_count(t.stand_id)}

    # ---------- приёмная ----------
    def find(self, stand_id: str, code: str) -> Ticket:
        c = normalize(code)
        if c.isdigit() and len(c) == TOKEN_DIGITS:
            t = self.db.scalar(select(Ticket).where(Ticket.token_hash == sha256(c), Ticket.stand_id == stand_id))
        elif len(c) >= 2 and c[0] in SERIES and c[1:].isdigit():
            t = self.db.scalar(select(Ticket).where(Ticket.stand_id == stand_id, Ticket.day == self.today(),
                                                    Ticket.number == f"{c[0]}{int(c[1:]):03d}"))
        else:
            raise HTTPException(422, "Отсканируйте QR талона или введите номер, например А317")
        if not t:
            raise HTTPException(404, "Талон не найден")
        return t

    def checkin(self, stand_id: str, code: str, actor: str) -> dict:
        t = self.find(stand_id, code)
        if t.day != self.today():
            raise HTTPException(410, "Талон выдан в другой день — выдайте новый")
        if t.status in ("serving", "bank", "done"):
            raise HTTPException(409, "Этот талон уже обслужен")
        if t.status == "cancelled":
            raise HTTPException(409, "Талон отменён — выдайте новый")
        if t.status in ("issued", "noshow"):  # не пришёл по вызову и вернулся — в конец очереди
            again = t.status == "noshow"
            t.status, t.checked_in_at, t.station, t.station_kind = "waiting", datetime.now(timezone.utc), "", ""
            self._audit(actor, "ticket.checkin", t.number + (" повторно" if again else ""))
            self.db.flush()
        return {**self.brief(t), "position": self.position(t)}

    # ---------- рабочее место ----------
    def call_next(self, dev: Device, op: Operator, actor: str) -> dict:
        if not dev.station:
            raise HTTPException(409, "У планшета не задан номер стойки — обратитесь к администратору")
        for _ in range(5):
            t = self.db.scalar(select(Ticket).where(Ticket.stand_id == dev.stand_id, Ticket.status == "waiting",
                                                    Ticket.day == self.today())
                               .order_by(Ticket.checked_in_at, Ticket.seq).limit(1))
            if not t:
                raise HTTPException(404, "Очередь пуста")
            now = datetime.now(timezone.utc)
            # захват талона атомарно: два рабочих места не вызовут одного человека
            got = self.db.execute(update(Ticket).where(Ticket.id == t.id, Ticket.status == "waiting").values(
                status="called", called_at=now, first_called_at=func.coalesce(Ticket.first_called_at, now),
                call_count=Ticket.call_count + 1, station=dev.station, station_kind="desk",
                served_operator_id=op.id)).rowcount
            if got:
                self.db.flush()
                self.db.refresh(t)
                self._audit(actor, "ticket.call", f"{t.number} → {dev.station}")
                return self.brief(t)
        raise HTTPException(409, "Очередь меняется слишком быстро — повторите")

    def _mine(self, dev: Device, ticket_id: str) -> Ticket:
        """Талон, вызванный именно к этой стойке: чужой вызов другая стойка не трогает."""
        t = self._get(dev.stand_id, ticket_id)
        if t.status == "called" and t.station != dev.station:
            raise HTTPException(409, f"Этот талон вызван к стойке {t.station}")
        return t

    def recall(self, dev: Device, ticket_id: str, actor: str) -> dict:
        t = self._mine(dev, ticket_id)
        if t.status not in ("called", "bank"):
            raise HTTPException(409, "Повторный вызов — только для вызванного талона")
        t.call_count += 1
        t.called_at = datetime.now(timezone.utc)
        self._audit(actor, "ticket.recall", t.number)
        self.db.flush()
        return self.brief(t)

    def claim(self, dev: Device, ticket_id: str) -> Ticket:
        """«Клиент подошёл»: атомарно переводит вызванный к этой стойке талон в обслуживание (дважды не выйдет)."""
        t = self._mine(dev, ticket_id)
        got = self.db.execute(update(Ticket).where(Ticket.id == t.id, Ticket.status == "called",
                                                   Ticket.station == dev.station).values(status="serving")).rowcount
        if not got:
            raise HTTPException(409, "Начать обслуживание можно только у вызванного к этой стойке талона")
        self.db.flush()
        self.db.refresh(t)
        return t

    def attach(self, t: Ticket, case: Case, actor: str) -> dict:
        t.case_id = case.id
        case.ticket_id, case.lang = t.id, (t.lang if t.lang in LANGS else "ru")
        self._audit(actor, "ticket.serve", t.number, case.id)
        self.db.flush()
        return self.brief(t)

    def noshow(self, dev: Device, ticket_id: str, actor: str) -> dict:
        t = self._mine(dev, ticket_id)
        if t.status != "called":
            raise HTTPException(409, "«Не подошёл» — только для вызванного талона")
        t.status, t.finished_at = "noshow", datetime.now(timezone.utc)
        self._audit(actor, "ticket.noshow", t.number)
        self.db.flush()
        return self.brief(t)

    def to_bank(self, stand_id: str, case: Case, actor: str) -> dict:
        t = self.db.get(Ticket, case.ticket_id) if case.ticket_id else None
        if not t or t.stand_id != stand_id:
            raise HTTPException(409, "У обращения нет талона — вызовите клиента к банку голосом")
        if case.closed_at or t.status not in ("serving", "bank"):
            raise HTTPException(409, "Обращение закрыто или талон уже обслужен")
        bank = self.db.get(Bank, case.bank_id) if case.bank_id else None
        if not bank or not bank.window:
            raise HTTPException(409, "У банка не задан номер окна — обратитесь к администратору")
        t.status, t.station, t.station_kind = "bank", bank.window, "bank"
        t.call_count += 1
        t.called_at = datetime.now(timezone.utc)
        self._audit(actor, "ticket.bank", f"{t.number} → окно {bank.window}", case.id)
        self.db.flush()
        return self.brief(t)

    def finish_case(self, case: Case) -> None:
        if case.ticket_id:
            t = self.db.get(Ticket, case.ticket_id)
            if t and t.status in ("serving", "bank"):
                t.status, t.finished_at = "done", datetime.now(timezone.utc)

    # ---------- обзор ----------
    def waiting_count(self, stand_id: str) -> int:
        return self.db.scalar(select(func.count(Ticket.id)).where(
            Ticket.stand_id == stand_id, Ticket.day == self.today(), Ticket.status == "waiting")) or 0

    def position(self, t: Ticket) -> int | None:
        if t.status != "waiting" or not t.checked_in_at:
            return None
        # тот же порядок, что при вызове: время прихода, при равенстве — порядковый номер
        return (self.db.scalar(select(func.count(Ticket.id)).where(
            Ticket.stand_id == t.stand_id, Ticket.day == t.day, Ticket.status == "waiting",
            or_(Ticket.checked_in_at < t.checked_in_at,
                and_(Ticket.checked_in_at == t.checked_in_at, Ticket.seq < t.seq)))) or 0) + 1

    def overview(self, stand_id: str) -> dict:
        rows = list(self.db.scalars(select(Ticket).where(Ticket.stand_id == stand_id, Ticket.day == self.today(),
                                                         Ticket.status.in_(("issued",) + ACTIVE))
                                    .order_by(Ticket.checked_in_at, Ticket.seq)))
        by = lambda st: [self.brief(t) for t in rows if t.status == st]  # noqa: E731
        return {"waiting": by("waiting"), "called": by("called"), "serving": by("serving"), "bank": by("bank"),
                "on_the_way": sum(1 for t in rows if t.status == "issued"),
                "by_lang": {lg: sum(1 for t in rows if t.status == "waiting" and t.lang == lg) for lg in LANGS}}

    def board(self, stand_id: str) -> dict:
        since = datetime.now(timezone.utc) - timedelta(minutes=30)
        rows = self.db.scalars(select(Ticket).where(Ticket.stand_id == stand_id, Ticket.called_at >= since,
                                                    Ticket.status.in_(("called", "bank")))
                               .order_by(Ticket.called_at.desc()).limit(8))
        return {"calls": [{"id": t.id, "number": display(t.number), "station": t.station, "kind": t.station_kind,
                           "lang": t.lang,
                           "call_count": t.call_count, "called_at": iso(t.called_at)} for t in rows],
                "waiting": self.waiting_count(stand_id)}

    def report(self, stand_id: str, day: str) -> dict:
        rows = list(self.db.scalars(select(Ticket).where(Ticket.stand_id == stand_id, Ticket.day == day)))
        came = [t for t in rows if t.checked_in_at]
        served = [t for t in rows if t.status in ("serving", "bank", "done")]
        waits = [(t.first_called_at - t.checked_in_at).total_seconds() / 60 for t in came
                 if t.first_called_at and t.checked_in_at and t.first_called_at >= t.checked_in_at]
        ops = {o.id: o for o in self.db.scalars(select(Operator).where(Operator.stand_id == stand_id))}
        def by_operator(source: str) -> dict[str, dict]:
            out: dict[str, dict] = {}
            for t in rows:
                if t.source != source:
                    continue
                o = ops.get(t.issued_operator_id)
                k = o.code if o else str(t.issued_operator_id)
                d = out.setdefault(k, {"name": o.name if o else "", "issued": 0, "came": 0, "served": 0})
                d["issued"] += 1
                d["came"] += bool(t.checked_in_at)
                d["served"] += t.status in ("serving", "bank", "done")
            return out
        hours: dict[int, int] = {}
        for t in rows:
            h = datetime.fromisoformat(iso(t.issued_at)).astimezone(self.tz).hour
            hours[h] = hours.get(h, 0) + 1
        hall = [t for t in rows if t.source == "hall"]
        floor1 = [t for t in rows if t.source == "floor1"]
        return {"date": day, "issued": len(rows), "issued_hall": len(hall), "issued_floor1": len(floor1),
                "printed_hall": sum(1 for t in hall if t.printed_at),
                "printed_floor1": sum(1 for t in floor1 if t.printed_at),
                "came": len(came), "came_from_hall_pct": round(sum(1 for t in hall if t.checked_in_at) / len(hall) * 100) if hall else None,
                "served": len(served), "noshow": sum(1 for t in rows if t.status == "noshow"),
                "avg_wait_min": round(sum(waits) / len(waits), 1) if waits else None,
                "max_wait_min": round(max(waits), 1) if waits else None,
                "by_lang": {lg: sum(1 for t in rows if t.lang == lg) for lg in LANGS},
                "by_hour": dict(sorted(hours.items())), "by_hall_operator": by_operator("hall"),
                "by_floor1_operator": by_operator("floor1"),
                "basis": "талоны без персональных данных; «пришли» — отсканированы в офисе"}
