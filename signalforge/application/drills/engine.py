"""The drill engine (REQ-070).

Reads a scenario, moves scenario time to each step's offset, performs the step
through one of the three ports, captures what came back, and evaluates
checkpoints against captures. That is the whole engine, and the shortness is the
design: everything a drill *concludes* is concluded by the product, over its own
API, and this file only writes down what it said and when.

Two properties are worth stating because a drill that lacked either would be
theatre:

* **Nothing sleeps.** Approval expiry, stability windows, escalation countdowns
  and retry backoff all read the clock, and the clock is a port. A drill that
  needs ten minutes to pass advances ten minutes.
* **Nothing is captured that was not returned.** A capture is a real response
  body, keyed by name. A checkpoint can only read those. There is no way to
  write a checkpoint that passes because the runner computed the answer.

Templating in call bodies and paths is a single substitution - ``{{name.path}}``
resolved against the captures - so a scenario can say "approve the execution the
previous step created" without the runner knowing what an execution is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional

from signalforge.application.drills.ports import (
    DrillClock,
    DrillResponse,
    DrillTransport,
    DrillWorld,
)
from signalforge.domain.drills.checks import (
    MISSING,
    CheckResult,
    evaluate,
    resolve,
)
from signalforge.domain.drills.errors import CheckpointFailed, StepError
from signalforge.domain.drills.scenario import Call, Scenario, Step, StepKind

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.\[\]-]+)\s*\}\}")


@dataclass
class StepRecord:
    """One line of the drill's own timeline."""

    step_id: str
    kind: str
    label: str
    actor: Optional[str]
    at: datetime
    detail: str
    status: Optional[int] = None
    checks: tuple[CheckResult, ...] = ()

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


@dataclass
class DrillRun:
    drill_id: str
    name: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    records: list[StepRecord] = field(default_factory=list)
    captures: dict[str, Any] = field(default_factory=dict)
    identities: dict[str, str] = field(default_factory=dict)
    user_ids: dict[str, str] = field(default_factory=dict)

    @property
    def checkpoints(self) -> list[StepRecord]:
        return [r for r in self.records if r.kind == StepKind.CHECKPOINT.value]

    @property
    def passed(self) -> bool:
        return all(record.ok for record in self.records)

    def capture(self, name: str) -> Any:
        return self.captures[name]

    def summary(self) -> str:
        checkpoints = self.checkpoints
        passed = sum(1 for c in checkpoints if c.ok)
        return (
            f"{self.drill_id}: {len(self.records)} steps, "
            f"{passed}/{len(checkpoints)} checkpoints passed"
        )


class DrillEngine:
    """Runs one scenario against one platform, one world and one clock."""

    def __init__(
        self,
        *,
        transport: DrillTransport,
        world: DrillWorld,
        clock: DrillClock,
        on_step=None,
    ):
        self._transport = transport
        self._world = world
        self._clock = clock
        self._on_step = on_step

    async def run(
        self,
        scenario: Scenario,
        *,
        stop_on_failure: bool = True,
        identities: Optional[Mapping[str, str]] = None,
        captures: Optional[Mapping[str, Any]] = None,
    ) -> DrillRun:
        """Run a scenario.

        ``identities`` and ``captures`` seed a run that continues from a state
        somebody else established - a suite that has already onboarded an
        application, or a second scenario resuming after the first. A scenario
        that starts from nothing needs neither.
        """
        run = DrillRun(
            drill_id=scenario.drill_id,
            name=scenario.name,
            started_at=self._clock.now(),
            captures=dict(captures or {}),
            identities=dict(identities or {}),
        )
        start = scenario.starts_at
        for step in scenario.steps:
            self._advance_to(step, start=start)
            record = await self._perform(step, scenario=scenario, run=run)
            run.records.append(record)
            if self._on_step is not None:
                self._on_step(record)
            if not record.ok and stop_on_failure:
                run.finished_at = self._clock.now()
                raise CheckpointFailed(
                    step.id,
                    tuple(check.detail for check in record.checks if not check.ok),
                )
        run.finished_at = self._clock.now()
        return run

    # -- step kinds ------------------------------------------------------------

    async def _perform(self, step: Step, *, scenario: Scenario, run: DrillRun) -> StepRecord:
        if step.kind is StepKind.CHECKPOINT:
            return self._checkpoint(step, run=run)
        if step.kind is StepKind.WORLD:
            return self._world_step(step)
        if step.kind is StepKind.CLOCK:
            return self._clock_step(step)
        if step.kind is StepKind.INJECT:
            return await self._inject_step(step, scenario=scenario, run=run)
        return await self._call_step(step, run=run)

    async def _call_step(self, step: Step, *, run: DrillRun) -> StepRecord:
        call = step.call
        assert call is not None  # guaranteed by the scenario loader
        token = None if call.unauthenticated else self._token_for(step, run=run)
        path = _substitute(call.path, run.captures)
        body = _substitute(call.body, run.captures) if call.body is not None else None
        params = (
            _substitute(call.params, run.captures) if call.params is not None else None
        )
        response = await self._transport.request(
            call.method, path, token=token, json=body, params=params
        )
        if response.status not in call.expect_status:
            raise StepError(
                step.id,
                f"{call.method} {path} -> {response.status}, expected "
                f"{list(call.expect_status)}: {_short(response.body)}",
            )
        if step.capture:
            run.captures[step.capture] = response.body
        if step.identity is not None:
            self._register_identity(step, response=response, run=run)
        record = StepRecord(
            step_id=step.id,
            kind=step.kind.value,
            label=step.label,
            actor=step.actor,
            at=self._clock.now(),
            detail=f"{call.method} {path}",
            status=response.status,
        )
        record.checks = self._evaluate(step, run=run, immediate=response.body)
        return record

    def _checkpoint(self, step: Step, *, run: DrillRun) -> StepRecord:
        return StepRecord(
            step_id=step.id,
            kind=step.kind.value,
            label=step.label,
            actor=step.actor,
            at=self._clock.now(),
            detail="expected versus observed",
            checks=self._evaluate(step, run=run, immediate=None),
        )

    def _world_step(self, step: Step) -> StepRecord:
        for verb, argument in step.world.items():
            self._world.apply(verb, argument)
        return StepRecord(
            step_id=step.id,
            kind=step.kind.value,
            label=step.label,
            actor=step.actor,
            at=self._clock.now(),
            detail=", ".join(sorted(step.world)),
        )

    def _clock_step(self, step: Step) -> StepRecord:
        for verb, argument in step.clock.items():
            if verb in {"advance_s", "step_s"}:
                self._clock.advance(float(argument))
            elif verb == "pause":
                self._clock.pause()
            elif verb == "resume":
                self._clock.resume()
            elif verb == "set_speed":
                self._clock.set_speed(float(argument))
        return StepRecord(
            step_id=step.id,
            kind=step.kind.value,
            label=step.label,
            actor=step.actor,
            at=self._clock.now(),
            detail=", ".join(f"{k}={v}" for k, v in sorted(step.clock.items())),
        )

    async def _inject_step(
        self, step: Step, *, scenario: Scenario, run: DrillRun
    ) -> StepRecord:
        scenario_id = str(step.inject["scenario_id"])
        params = _substitute(dict(step.inject.get("params") or {}), run.captures)
        key = str(step.inject.get("idempotency_key") or f"{scenario.drill_id}-{step.id}")
        result = await self._world.inject(
            scenario_id=scenario_id, params=params, scope=scenario.scope, key=key
        )
        if step.capture:
            run.captures[step.capture] = result
        record = StepRecord(
            step_id=step.id,
            kind=step.kind.value,
            label=step.label,
            actor=step.actor,
            at=self._clock.now(),
            detail=f"{scenario_id} {params} scope={scenario.scope}",
        )
        record.checks = self._evaluate(step, run=run, immediate=result)
        return record

    # -- helpers ---------------------------------------------------------------

    def _advance_to(self, step: Step, *, start: datetime) -> None:
        if step.at_s is None:
            return
        target = start.timestamp() + step.at_s
        delta = target - self._clock.now().timestamp()
        if delta > 0:
            self._clock.advance(delta)

    def _token_for(self, step: Step, *, run: DrillRun) -> Optional[str]:
        if step.actor is None:
            return None
        token = run.identities.get(step.actor)
        if token is None and step.identity is None:
            raise StepError(
                step.id,
                f"nobody has signed in as {step.actor!r} yet; "
                f"known: {sorted(run.identities)}",
            )
        return token

    def _register_identity(
        self, step: Step, *, response: DrillResponse, run: DrillRun
    ) -> None:
        identity = step.identity
        assert identity is not None
        token = resolve(response.body, identity.token_path)
        if token is MISSING or not isinstance(token, str):
            raise StepError(
                step.id,
                f"identity {identity.name!r}: no token at {identity.token_path!r}",
            )
        run.identities[identity.name] = token
        if identity.user_id_path:
            user_id = resolve(response.body, identity.user_id_path)
            if user_id is not MISSING:
                run.user_ids[identity.name] = str(user_id)

    def _evaluate(
        self, step: Step, *, run: DrillRun, immediate: Any
    ) -> tuple[CheckResult, ...]:
        if not step.expect:
            return ()
        root = dict(run.captures)
        root["_"] = immediate
        root["identities"] = dict(run.identities)
        root["users"] = dict(run.user_ids)
        return tuple(evaluate(root, expectation) for expectation in step.expect)


def _substitute(value: Any, captures: Mapping[str, Any]) -> Any:
    """Resolve ``{{capture.path}}`` inside strings, recursively.

    A placeholder that is the entire string resolves to the *value*, so a
    scenario can pass a list or a number; one embedded in text resolves to its
    string form, so a path can be built.
    """
    if isinstance(value, str):
        whole = PLACEHOLDER.fullmatch(value.strip())
        if whole:
            resolved = resolve(captures, whole.group(1))
            if resolved is MISSING:
                raise KeyError(f"drill placeholder {{{{{whole.group(1)}}}}} is unresolved")
            return resolved
        def replace(match: re.Match) -> str:
            resolved = resolve(captures, match.group(1))
            if resolved is MISSING:
                raise KeyError(f"drill placeholder {{{{{match.group(1)}}}}} is unresolved")
            return str(resolved)
        return PLACEHOLDER.sub(replace, value)
    if isinstance(value, Mapping):
        return {key: _substitute(item, captures) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, captures) for item in value]
    return value


def _short(body: Any, limit: int = 300) -> str:
    text = str(body)
    return text if len(text) <= limit else text[:limit] + "..."
