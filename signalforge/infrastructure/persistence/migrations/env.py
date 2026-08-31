"""Alembic environment. Uses a sync driver against the same database file the
async app uses; the URL comes from SIGNALFORGE_DB_URL via config."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from signalforge.infrastructure.config import load_settings
from signalforge.infrastructure.persistence.models import Base

target_metadata = Base.metadata


def _sync_url() -> str:
    return load_settings().db_url.replace("+aiosqlite", "").replace("+asyncpg", "")


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_sync_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
