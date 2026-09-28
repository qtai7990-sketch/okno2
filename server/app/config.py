"""Настройки сервера. Все секреты — только из переменных окружения, в коде их нет."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


def _req(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None or val == "":
        raise RuntimeError(f"Не задана переменная окружения {name}")
    return val


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() == "true"


@dataclass(frozen=True)
class Settings:
    database_url: str
    data_key: str  # ключ Fernet для шифрования персональных данных в базе
    admin_token: str
    tz: ZoneInfo
    token_ttl_sec: int
    dup_key: str = ""  # отдельный секрет для ключа поиска дублей (HMAC), не равен data_key
    cors_origins: list[str] = field(default_factory=list)
    serve_ui_dir: str | None = None
    allow_demo_scan: bool = False  # в рабочем контуре — только False
    bank_mtls: bool = True  # запросы банков принимаются только с сертификатом клиента, проверенным прокси
    session_idle_min: int = 15  # сессия сотрудника закрывается после 15 минут бездействия
    session_max_hours: int = 12  # и не живёт дольше смены
    auto_create_schema: bool = False  # только для тестов и учебного сервера; рабочий — миграции Alembic
    max_evidence_bytes: int = 4 * 1024 * 1024
    bank_key_grace_hours: int = 24  # сколько действует старый ключ банка после ротации
    min_app_version: int = 0  # минимальная версия (versionCode) приложения планшета; старые версии не обслуживаются


def load_settings() -> Settings:
    return Settings(
        database_url=_req("SIMPAD_DATABASE_URL", "sqlite:///./simpad.db"),
        data_key=_req("SIMPAD_DATA_KEY"),
        admin_token=_req("SIMPAD_ADMIN_TOKEN"),
        dup_key=_req("SIMPAD_DUP_KEY"),
        tz=ZoneInfo(os.environ.get("SIMPAD_TZ", "Europe/Moscow")),
        token_ttl_sec=int(os.environ.get("SIMPAD_TOKEN_TTL_SEC", "600")),
        cors_origins=[o.strip() for o in os.environ.get("SIMPAD_CORS_ORIGINS", "").split(",") if o.strip()],
        serve_ui_dir=os.environ.get("SIMPAD_UI_DIR") or None,
        allow_demo_scan=_flag("SIMPAD_ALLOW_DEMO_SCAN", "false"),
        bank_mtls=_flag("SIMPAD_BANK_MTLS", "true"),
        session_idle_min=int(os.environ.get("SIMPAD_SESSION_IDLE_MIN", "15")),
        session_max_hours=int(os.environ.get("SIMPAD_SESSION_MAX_HOURS", "12")),
        auto_create_schema=_flag("SIMPAD_AUTO_CREATE_SCHEMA", "false"),
        min_app_version=int(os.environ.get("SIMPAD_MIN_APP_VERSION", "0")),
    )
