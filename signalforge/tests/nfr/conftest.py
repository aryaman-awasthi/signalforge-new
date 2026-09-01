"""Fixtures for the non-functional suite (artifact 16, artifact 17).

Reuses the governance fixtures, because non-functional claims about a platform
that has never had an incident are not claims about anything. Every test here
runs against the real API, the real bus and a real application serving the
frozen observe contract.
"""

from __future__ import annotations

import json
from pathlib import Path

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
    validation_view,
)

RESULTS_DIR = Path(__file__).resolve().parents[3] / "var" / "nfr"


def record(name: str, payload: dict) -> Path:
    """Write one measurement file for the report generator to read.

    Under var/, which reset.sh clears: a performance number is evidence about a
    run, and a stale one left over from last week is worse than none.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


@pytest.fixture
def anyio_backend():
    return "asyncio"
