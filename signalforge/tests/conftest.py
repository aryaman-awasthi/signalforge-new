"""REQ traceability plumbing (REQ-006).

Every pytest run records, per test: the REQ ids from its @pytest.mark.req(...)
markers and its outcome, into var/rtm/test-results.json. scripts/gen_rtm.py
joins that file with specs/requirements.yaml to produce docs/20-RTM.md.
Works under --collect-only too (mapping recorded, outcomes empty).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def anyio_backend():
    """Async tests run on asyncio via the anyio pytest plugin."""
    return "asyncio"


@pytest.fixture
def api(tmp_path):
    """The API over a temp-file SQLite DB, fake clock, per-test container.
    File DB + NullPool keeps aiosqlite connections inside the client's loop."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from signalforge.api import deps
    from signalforge.api.main import app
    from signalforge.infrastructure.persistence.models import Base
    from signalforge.tests.helpers import (
        ApiCtx,
        FakeClock,
        make_test_llm_settings,
        make_test_settings,
    )

    db_path = tmp_path / "test.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    settings = make_test_settings(f"sqlite+aiosqlite:///{db_path}")
    clock = FakeClock()
    engine = create_async_engine(settings.db_url, poolclass=NullPool)
    container = deps.Container(
        settings=settings,
        engine=engine,
        clock=clock,
        llm_settings=make_test_llm_settings(),
    )
    app.dependency_overrides[deps.get_container] = lambda: container
    try:
        with TestClient(app) as client:
            yield ApiCtx(client=client, container=container, clock=clock, db_path=db_path)
    finally:
        app.dependency_overrides.clear()
RESULTS_PATH = ROOT / "var" / "rtm" / "test-results.json"

_tests: dict[str, dict] = {}


def _req_ids(item) -> list[str]:
    ids: list[str] = []
    for marker in item.iter_markers(name="req"):
        ids.extend(str(a) for a in marker.args)
    return ids


def pytest_collection_modifyitems(session, config, items):
    for item in items:
        _tests[item.nodeid] = {"reqs": _req_ids(item), "outcome": None}


def pytest_runtest_logreport(report):
    if report.when != "call" and not (report.when == "setup" and report.outcome != "passed"):
        return
    entry = _tests.setdefault(report.nodeid, {"reqs": [], "outcome": None})
    entry["outcome"] = report.outcome


def pytest_sessionfinish(session, exitstatus):
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "tests": dict(sorted(_tests.items())),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
