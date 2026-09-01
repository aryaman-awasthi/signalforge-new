"""Fixtures for the audit-replay and PIR suite (S7).

The incident these tests read is produced by the governance suite's own
fixtures, driving the real API: a real declaration, a real blocked
auto-execution, a real approval by a second person, a real timeout, a real
reconciliation and a real validation cycle. Nothing here writes an audit entry
by hand, because a replay tested against hand-written entries would prove only
that the test can write entries.
"""

from __future__ import annotations

import sqlite3

import pytest

from signalforge.tests.governance.conftest import (  # noqa: F401  (re-exported)
    APP_ID,
    Governed,
    act,
    connect,
    default_config,
    governed,
    import_actions,
    observe,
    request_execution,
    run_validation,
)


def tamper(db_path, *, seq: int, new_topic: str = "execution.succeeded") -> None:
    """Edit one stored audit record, the way somebody covering their tracks would.

    Deliberately crude and deliberately *plausible*: it rewrites a payload field
    in place and leaves every hash exactly as it was, which is precisely the
    edit a chain exists to catch.
    """
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "select payload from audit_log where seq = ?", (seq,)
        ).fetchone()
        if row is None:
            raise AssertionError(f"no audit record at seq {seq}")
        doctored = row[0].replace('"blocked"', '"succeeded"')
        if doctored == row[0]:
            doctored = row[0][:-1] + ',"tampered":true}'
        connection.execute(
            "update audit_log set payload = ? where seq = ?", (doctored, seq)
        )
        connection.commit()
    finally:
        connection.close()


def delete_audit_row(db_path, *, seq: int) -> None:
    """Remove a record entirely. The prev_hash of its successor no longer matches."""
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("delete from audit_log where seq = ?", (seq,))
        connection.commit()
    finally:
        connection.close()


def audit_seqs(db_path, tenant_id: str, topic: str) -> list[int]:
    connection = sqlite3.connect(db_path)
    try:
        return [
            row[0]
            for row in connection.execute(
                "select seq from audit_log where tenant_id = ? and topic = ? order by seq",
                (tenant_id, topic),
            ).fetchall()
        ]
    finally:
        connection.close()


@pytest.fixture
def anyio_backend():
    return "asyncio"
