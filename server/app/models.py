"""Модель данных. Персональные данные клиента хранятся только в зашифрованных полях *_enc."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Stand(Base):
    __tablename__ = "stands"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stand_id: Mapped[str] = mapped_column(ForeignKey("stands.id"))
    name: Mapped[str] = mapped_column(String(200))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # desk — рабочее место оформления; hall — выдача талонов в зале; reception — приёмная офиса; board — табло
    role: Mapped[str] = mapped_column(String(12), default="desk")
    station: Mapped[str] = mapped_column(String(8), default="")  # номер стойки для вызова (desk)
    series: Mapped[str] = mapped_column(String(1), default="")  # буква серии талонов (hall, reception)


class Operator(Base):
    """Сотрудник стойки. Вход — код сотрудника + ПИН на зарегистрированном планшете."""
    __tablename__ = "operators"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stand_id: Mapped[str] = mapped_column(ForeignKey("stands.id"))
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    pin_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16), default="operator")  # operator | senior
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"))
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Bank(Base):
    __tablename__ = "banks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str] = mapped_column(String(300), default="")
    inn: Mapped[str] = mapped_column(String(12), default="")
    sim_brand: Mapped[str] = mapped_column(String(200), default="")
    mode: Mapped[str] = mapped_column(String(8), default="case")  # api | case
    open_from: Mapped[int] = mapped_column(Integer, default=9)
    open_to: Mapped[int] = mapped_column(Integer, default=21)
    api_key_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    hmac_secret: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prev_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prev_hmac_secret: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prev_valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cert_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)  # SHA-256 сертификата mTLS
    status_map: Mapped[dict] = mapped_column(JSON, default=dict)  # код банка -> код словаря статусов
    sim_authority_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    window: Mapped[str] = mapped_column(String(8), default="")  # номер окна банка в офисе — для вызова на табло


class BankNonce(Base):
    """Одноразовые значения запросов банка — защита от повторной отправки."""
    __tablename__ = "bank_nonces"
    __table_args__ = (UniqueConstraint("bank_id", "nonce"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bank_id: Mapped[str] = mapped_column(ForeignKey("banks.id"))
    nonce: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocPolicy(Base):
    """Правило банка: нужен ли нотариальный перевод паспорта для услуги по гражданству."""
    __tablename__ = "doc_policies"
    __table_args__ = (UniqueConstraint("bank_id", "service", "country"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bank_id: Mapped[str] = mapped_column(ForeignKey("banks.id"))
    service: Mapped[str] = mapped_column(String(16))  # card | snils | ebs | sim
    country: Mapped[str] = mapped_column(String(3))  # ISO-3 из MRZ
    rule: Mapped[str] = mapped_column(String(8), default="unknown")  # no|before|final|unknown
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[str] = mapped_column(String(200), default="")


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class ConsentText(Base):
    """Неизменяемая версия текста согласия. Любая правка — новая версия с новым хэшем."""
    __tablename__ = "consent_texts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient: Mapped[str] = mapped_column(String(16))  # simpad | bank | notary | insurer
    bank_id: Mapped[str | None] = mapped_column(ForeignKey("banks.id"), nullable=True)
    legal_name: Mapped[str] = mapped_column(String(300))
    inn: Mapped[str] = mapped_column(String(12))
    address: Mapped[str] = mapped_column(String(500), default="")
    purpose: Mapped[str] = mapped_column(Text)
    fields: Mapped[list] = mapped_column(JSON)
    retention: Mapped[str] = mapped_column(String(200), default="")
    lang: Mapped[str] = mapped_column(String(4), default="ru")
    body: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    version: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str] = mapped_column(String(200), default="")  # кто из юристов утвердил
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    number: Mapped[str] = mapped_column(String(16), unique=True)
    stand_id: Mapped[str] = mapped_column(ForeignKey("stands.id"))
    # Обращение, начатое агентом 1 этажа: планшет зала видит только свои и только до выдачи талона (миграция 0004)
    floor1_device_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lang: Mapped[str] = mapped_column(String(4), default="ru")
    purpose: Mapped[str | None] = mapped_column(String(16), nullable=True)  # patent|other
    pkg: Mapped[str | None] = mapped_column(String(8), nullable=True)  # card|sim|full
    opts: Mapped[dict] = mapped_column(JSON, default=dict)
    pay: Mapped[dict] = mapped_column(JSON, default=dict)
    bank_id: Mapped[str | None] = mapped_column(ForeignKey("banks.id"), nullable=True)
    ruid: Mapped[bool] = mapped_column(Boolean, default=False)  # только справочный признак
    passport_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    migcard_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    scan_source: Mapped[str | None] = mapped_column(String(8), nullable=True)
    country: Mapped[str | None] = mapped_column(String(3), nullable=True)
    has_ru_text: Mapped[bool] = mapped_column(Boolean, default=False)
    dup_key: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)  # HMAC, не хэш
    dup_of: Mapped[str | None] = mapped_column(String(16), nullable=True)
    imei_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    card_state: Mapped[str] = mapped_column(String(8), default="none")  # none|issued|active
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_mode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    handoff_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    bank_case_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ticket_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    consents: Mapped[list["Consent"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    steps: Mapped[list["Step"]] = relationship(back_populates="case", cascade="all, delete-orphan")


class Consent(Base):
    __tablename__ = "consents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    recipient: Mapped[str] = mapped_column(String(16))
    text_id: Mapped[int] = mapped_column(ForeignKey("consent_texts.id"))
    text_sha256: Mapped[str] = mapped_column(String(64))
    bank_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    method: Mapped[str] = mapped_column(String(16))  # paper | stylus
    explained_lang: Mapped[str] = mapped_column(String(4))
    evidence_enc: Mapped[str] = mapped_column(Text)  # скан бланка или изображение подписи, зашифровано
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    evidence_mime: Mapped[str] = mapped_column(String(32))
    operator_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    case: Mapped[Case] = relationship(back_populates="consents")


class Step(Base):
    """Статус шага. Для шагов банка — только из подписанных событий банка."""
    __tablename__ = "steps"
    __table_args__ = (UniqueConstraint("case_id", "key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    key: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(8), default="todo")
    note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default="")  # operator|bank|system
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    case: Mapped[Case] = relationship(back_populates="steps")


class OperatorClaim(Base):
    """Статус «со слов сотрудника банка». Не подтверждён: не влияет на шаги банка, KPI и расчёты."""
    __tablename__ = "operator_claims"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    code: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text, default="")
    operator_id: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Correction(Base):
    __tablename__ = "corrections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    field: Mapped[str] = mapped_column(String(32))
    old_enc: Mapped[str | None] = mapped_column(Text)
    new_enc: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(200))
    operator_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HandoffToken(Base):
    __tablename__ = "handoff_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    handoff_id: Mapped[str] = mapped_column(String(36), unique=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    bank_id: Mapped[str] = mapped_column(ForeignKey("banks.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)


class BankEvent(Base):
    __tablename__ = "bank_events"
    __table_args__ = (UniqueConstraint("bank_id", "event_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bank_id: Mapped[str] = mapped_column(ForeignKey("banks.id"))
    event_id: Mapped[str] = mapped_column(String(64))
    case_id: Mapped[str] = mapped_column(String(36), index=True)  # без внешнего ключа: события живут дольше обращения
    handoff_id: Mapped[str] = mapped_column(String(36))
    bank_case_id: Mapped[str] = mapped_column(String(64))
    bank_code: Mapped[str] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(32))
    schema_version: Mapped[str] = mapped_column(String(16))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    body_sha256: Mapped[str] = mapped_column(String(64))


class RejectedEvent(Base):
    """Отклонённые события банка — для разбора и сверки, состояние обращения не меняют."""
    __tablename__ = "rejected_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bank_id: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(64))
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    code: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TicketCounter(Base):
    """Счётчик номеров талонов по серии за день. Номер берётся атомарным UPDATE … RETURNING — без гонок."""
    __tablename__ = "ticket_counters"
    stand_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    series: Mapped[str] = mapped_column(String(1), primary_key=True)
    last: Mapped[int] = mapped_column(Integer, default=0)


class Ticket(Base):
    """Талон очереди. Персональных данных нет: номер, язык, время, статус, стойка вызова."""
    __tablename__ = "tickets"
    __table_args__ = (UniqueConstraint("stand_id", "day", "number"), UniqueConstraint("issued_device_id", "idem_key", name="uq_tickets_device_idem"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stand_id: Mapped[str] = mapped_column(ForeignKey("stands.id"))
    day: Mapped[str] = mapped_column(String(10))  # местная дата выдачи ГГГГ-ММ-ДД
    series: Mapped[str] = mapped_column(String(1))
    seq: Mapped[int] = mapped_column(Integer)
    number: Mapped[str] = mapped_column(String(8))  # например A317
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # QR талона — только цифры, хранится хэш
    lang: Mapped[str] = mapped_column(String(4), default="ru")
    source: Mapped[str] = mapped_column(String(12))  # hall | reception
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    issued_device_id: Mapped[int] = mapped_column(Integer)
    issued_operator_id: Mapped[int] = mapped_column(Integer)
    idem_key: Mapped[str | None] = mapped_column(String(64), nullable=True)  # ключ повтора запроса с планшета
    printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # issued → waiting (пришёл в офис) → called → serving → bank → done;  noshow / cancelled
    status: Mapped[str] = mapped_column(String(10), default="issued")
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_called_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    called_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    station_kind: Mapped[str] = mapped_column(String(8), default="")  # desk | bank
    station: Mapped[str] = mapped_column(String(8), default="")
    served_operator_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(64))
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")
