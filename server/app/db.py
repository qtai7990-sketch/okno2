from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base


def make_engine(url: str):
    kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    return create_engine(url, **kw)


def init_db(engine) -> None:
    Base.metadata.create_all(engine)


def make_sessionmaker(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)
