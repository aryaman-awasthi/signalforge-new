"""Async engine and session factory. SQLite runs in WAL mode with foreign keys
on; both pragmas are no-ops on other dialects, keeping this Postgres-ready."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


def make_engine(db_url: str, *, null_pool: bool = False) -> AsyncEngine:
    """An async engine, with the SQLite pragmas applied on every connect.

    `null_pool` opens a connection per session instead of keeping a pool. It is
    used for the derived rollup database, where the writer is a five-second loop
    that may be driven from more than one event loop (the worker group, a forced
    evaluation from a request, a test): an aiosqlite connection belongs to the
    loop that opened it, and a pooled one handed to a different loop is a hang
    rather than an error. Opening a SQLite connection is cheap, and SQLite
    serialises writers regardless, so nothing is lost.
    """
    if db_url.startswith("sqlite") and ":///" in db_url:
        db_path = db_url.split(":///", 1)[1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = (
        create_async_engine(db_url, poolclass=NullPool)
        if null_pool
        else create_async_engine(db_url)
    )
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
