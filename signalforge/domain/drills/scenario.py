"""The scenario document: a rehearsed incident, written down (REQ-070, REQ-071).

A scenario is data. It says who acts, when in *scenario* time, what call that
act makes on the platform's public API, and what must then be true. It contains
no product logic at all: there is no branching, no computation and no way to
express "and then decide" - because a drill whose runner can decide is a second
implementation of the thing under test.

Six kinds of step, and the split is the vocabulary a game day actually uses:

* ``human``      - a person presses something. Carries the identity that presses it.
* ``machine``    - the platform, a pipeline or an adapter acts.
* ``world``      - the simulated application changes underneath: health flips,
                   a path starts timing out, an execution turns out to have
                   landed after all.
* ``inject``     - a fault goes in through the chaos adapter, always scoped to
                   this drill.
* ``clock``      - scenario time moves. Nothing sleeps.
* ``checkpoint`` - expected versus observed, against something already captured.

``at_s`` is an offset in scenario seconds from the scenario's start. The engine
advances the virtual clock to it before running the step, so the timeline a
replay reconstructs is the timeline the file describes, whatever the wall clock
was doing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from signalforge.domain.drills.checks import Expectation, parse_expectations
from signalforge.domain.drills.errors import ScenarioError

SCENARIO_SCHEMA_VERSION = 1

HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

WORLD_VERBS = frozenset(
    {
        "set_health",
        "set_metrics",
        "set_actions",
        "set_topology",
        "set_logs",
        "times_out",
        "clear_timeouts",
        "fail_path",
        "clear_failures",
        "record_execution",
        "set_probe_path",
    }
)
"""Everything a drill may do to the *simulated application*.

Deliberately closed. A drill can make the application unhealthy, slow, or
quietly successful after a timeout; it cannot reach into SignalForge's own
state, because the whole claim of a drill is that the platform reached its
conclusions itself.
"""

CLOCK_VERBS = frozenset({"advance_s", "pause", "resume", "set_speed", "step_s"})


class StepKind(str, Enum):
    HUMAN = "human"
    MACHINE = "machine"
    WORLD = "world"
    INJECT = "inject"
    CLOCK = "clock"
    CHECKPOINT = "checkpoint"


@dataclass(frozen=True)
class Identity:
    """Somebody the drill signs in as, and how their token is found.

    A drill never carries a token: it says which field of which response holds
    one. Tokens are minted by the real auth service during the run.
    """

    name: str
    token_path: str
    user_id_path: Optional[str] = None


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    body: Optional[Mapping[str, Any]] = None
    params: Optional[Mapping[str, Any]] = None
    expect_status: tuple[int, ...] = (200, 201)
    unauthenticated: bool = False


@dataclass(frozen=True)
class Step:
    id: str
    kind: StepKind
    label: str
    at_s: Optional[float] = None
    actor: Optional[str] = None
    call: Optional[Call] = None
    capture: Optional[str] = None
    identity: Optional[Identity] = None
    world: Mapping[str, Any] = field(default_factory=dict)
    clock: Mapping[str, Any] = field(default_factory=dict)
    inject: Mapping[str, Any] = field(default_factory=dict)
    expect: tuple[Expectation, ...] = ()
    note: str = ""

    @property
    def is_checkpoint(self) -> bool:
        return self.kind is StepKind.CHECKPOINT


@dataclass(frozen=True)
class Scenario:
    drill_id: str
    name: str
    scope: str
    description: str
    starts_at: datetime
    speed: float
    steps: tuple[Step, ...]
    requires_model: bool = False
    app_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def checkpoints(self) -> tuple[Step, ...]:
        return tuple(step for step in self.steps if step.is_checkpoint)

    def step(self, step_id: str) -> Step:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise KeyError(step_id)


def load_scenario(document: Mapping[str, Any]) -> Scenario:
    """Validate a scenario document into a Scenario, or refuse it entirely."""
    if not isinstance(document, Mapping):
        raise ScenarioError("a scenario must be a mapping")
    version = document.get("schema_version", SCENARIO_SCHEMA_VERSION)
    if version != SCENARIO_SCHEMA_VERSION:
        raise ScenarioError(
            f"scenario schema_version {version!r} is not {SCENARIO_SCHEMA_VERSION}"
        )
    drill_id = _required_str(document, "drill_id")
    scope = str(document.get("scope") or f"drill:{drill_id}")
    if not scope.startswith("drill:"):
        raise ScenarioError(
            f"{drill_id}: scope must start with 'drill:' - chaos is refused outside a drill scope"
        )
    clock = document.get("clock") or {}
    if not isinstance(clock, Mapping):
        raise ScenarioError(f"{drill_id}: clock must be a mapping")
    starts_at = _parse_start(clock.get("starts_at"), drill_id=drill_id)
    speed = float(clock.get("speed", 1.0))
    if speed <= 0:
        raise ScenarioError(f"{drill_id}: clock speed must be positive")

    raw_steps = document.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ScenarioError(f"{drill_id}: a scenario needs at least one step")

    steps: list[Step] = []
    seen: set[str] = set()
    last_at: Optional[float] = None
    for index, raw in enumerate(raw_steps):
        step = _load_step(raw, drill_id=drill_id, index=index)
        if step.id in seen:
            raise ScenarioError(f"{drill_id}: duplicate step id {step.id!r}")
        seen.add(step.id)
        if step.at_s is not None:
            if last_at is not None and step.at_s < last_at:
                raise ScenarioError(
                    f"{drill_id}: step {step.id!r} moves scenario time backwards "
                    f"({step.at_s}s after {last_at}s); a drill never rewinds"
                )
            last_at = step.at_s
        steps.append(step)

    return Scenario(
        drill_id=drill_id,
        name=str(document.get("name") or drill_id),
        scope=scope,
        description=str(document.get("description") or ""),
        starts_at=starts_at,
        speed=speed,
        steps=tuple(steps),
        requires_model=bool(document.get("requires_model", False)),
        app_id=document.get("app_id"),
        metadata=dict(document.get("metadata") or {}),
    )


def _required_str(document: Mapping[str, Any], key: str) -> str:
    value = document.get(key)
    if not value or not isinstance(value, str):
        raise ScenarioError(f"a scenario needs a {key}")
    return value


def _load_step(raw: Any, *, drill_id: str, index: int) -> Step:  # noqa: C901
    if not isinstance(raw, Mapping):
        raise ScenarioError(f"{drill_id}: step {index} must be a mapping")
    step_id = str(raw.get("id") or f"step-{index + 1}")
    where = f"{drill_id}/{step_id}"
    try:
        kind = StepKind(str(raw.get("kind")))
    except ValueError:
        raise ScenarioError(
            f"{where}: kind must be one of {[k.value for k in StepKind]}"
        ) from None

    at_s = raw.get("at_s")
    if at_s is not None:
        at_s = float(at_s)
        if at_s < 0:
            raise ScenarioError(f"{where}: at_s must not be negative")

    call = _load_call(raw.get("call"), where=where)
    identity = _load_identity(raw.get("identity"), where=where)
    world = dict(raw.get("world") or {})
    clock = dict(raw.get("clock") or {})
    inject = dict(raw.get("inject") or {})

    if kind in {StepKind.HUMAN, StepKind.MACHINE} and call is None:
        raise ScenarioError(f"{where}: a {kind.value} step must make a call")
    if kind is StepKind.HUMAN and not raw.get("actor"):
        raise ScenarioError(
            f"{where}: a human step names the person who acts - that is the record"
        )
    if kind is StepKind.WORLD:
        if not world:
            raise ScenarioError(f"{where}: a world step must change something")
        unknown = set(world) - WORLD_VERBS
        if unknown:
            raise ScenarioError(
                f"{where}: unknown world verb(s) {sorted(unknown)}; "
                f"allowed: {sorted(WORLD_VERBS)}"
            )
    if kind is StepKind.CLOCK:
        if not clock:
            raise ScenarioError(f"{where}: a clock step must move the clock")
        unknown = set(clock) - CLOCK_VERBS
        if unknown:
            raise ScenarioError(
                f"{where}: unknown clock verb(s) {sorted(unknown)}; "
                f"allowed: {sorted(CLOCK_VERBS)}"
            )
    if kind is StepKind.INJECT and not inject.get("scenario_id"):
        raise ScenarioError(f"{where}: an inject step must name a chaos scenario_id")

    try:
        expect = parse_expectations(raw.get("expect"), where=where)
    except ValueError as exc:
        raise ScenarioError(str(exc)) from None
    if kind is StepKind.CHECKPOINT and not expect:
        raise ScenarioError(f"{where}: a checkpoint that expects nothing checks nothing")

    return Step(
        id=step_id,
        kind=kind,
        label=str(raw.get("label") or step_id),
        at_s=at_s,
        actor=raw.get("actor"),
        call=call,
        capture=raw.get("capture"),
        identity=identity,
        world=world,
        clock=clock,
        inject=inject,
        expect=expect,
        note=str(raw.get("note") or ""),
    )


def _load_call(raw: Any, *, where: str) -> Optional[Call]:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ScenarioError(f"{where}: call must be a mapping")
    method = str(raw.get("method") or "GET").upper()
    if method not in HTTP_METHODS:
        raise ScenarioError(f"{where}: {method} is not an HTTP method a drill may use")
    path = raw.get("path")
    if not path or not str(path).startswith("/"):
        raise ScenarioError(f"{where}: call.path must be an absolute path")
    expect_status = raw.get("expect_status", (200, 201))
    if isinstance(expect_status, int):
        expect_status = (expect_status,)
    expect_status = tuple(int(code) for code in expect_status)
    if not expect_status:
        raise ScenarioError(f"{where}: expect_status must name at least one code")
    return Call(
        method=method,
        path=str(path),
        body=raw.get("body"),
        params=raw.get("params"),
        expect_status=expect_status,
        unauthenticated=bool(raw.get("unauthenticated", False)),
    )


def _load_identity(raw: Any, *, where: str) -> Optional[Identity]:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ScenarioError(f"{where}: identity must be a mapping")
    name = raw.get("name")
    token_path = raw.get("token_path")
    if not name or not token_path:
        raise ScenarioError(f"{where}: identity needs a name and a token_path")
    return Identity(
        name=str(name),
        token_path=str(token_path),
        user_id_path=raw.get("user_id_path"),
    )


def _parse_start(raw: Any, *, drill_id: str) -> datetime:
    if raw is None:
        return datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    if isinstance(raw, datetime):
        moment = raw
    else:
        try:
            moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            raise ScenarioError(
                f"{drill_id}: clock.starts_at {raw!r} is not an ISO-8601 instant"
            ) from None
    if moment.tzinfo is None:
        raise ScenarioError(f"{drill_id}: clock.starts_at must be timezone-aware")
    return moment.astimezone(timezone.utc)


def scenario_step_ids(steps: Sequence[Step]) -> tuple[str, ...]:
    return tuple(step.id for step in steps)
