"""Alembic: адрес базы берётся из SIMPAD_DATABASE_URL, схема — из app.models."""
import os

from alembic import context
from sqlalchemy import create_engine

from app.models import Base

target_metadata = Base.metadata
url = os.environ.get("SIMPAD_DATABASE_URL", "sqlite:///./simpad.db")


def run_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
