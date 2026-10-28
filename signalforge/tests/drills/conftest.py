"""Fixtures for the drill suite (S7).

Everything a drill needs is already built: the API test client, the observe
double that serves the frozen contract, and a clock the container holds and a
drill may move. This wires those three into the engine's three ports and gets
out of the way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from signalforge.application.drills.engine import DrillEngine
from signalforge.infrastructure.drills.clock import SteppedDrillClock
from signalforge.infrastructure.drills.loader import load_scenario_file
from signalforge.tests.adapters.conftest import (  # noqa: F401  (re-exported)
    ObserveHarness,
    connect,
    default_config,
    observe,
)
from signalforge.tests.drills.harness import (
    InProcessTransport,
    ObserveWorld,
    WorkerScheduler,
)

DRILLS_DIR = Path(__file__).resolve().parents[3] / "drills"


@pytest.fixture
def drill_clock(api):
    return SteppedDrillClock(api.clock)


@pytest.fixture
def drill_world(observe):  # noqa: F811  (the fixture is re-exported above)
    return ObserveWorld(observe)


@pytest.fixture
def drill_engine(api, drill_world, drill_clock):
    return DrillEngine(
        transport=InProcessTransport(api.client),
        world=drill_world,
        clock=drill_clock,
        scheduler=WorkerScheduler(api.container),
    )


@pytest.fixture
def scenario_file():
    def _load(name: str):
        return load_scenario_file(DRILLS_DIR / name)

    return _load


@pytest.fixture
def anyio_backend():
    return "asyncio"
