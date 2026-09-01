"""The in-process ports a drill runs against (REQ-070).

SignalForge is reached through its own ASGI application over the shared test
client, so every drill step is a real route with real auth, real permissions and
a real transaction. The application being watched is the observe double the
adapter suite already uses, so every fault a drill injects and every health
reading the platform takes goes over the frozen observe contract.

Neither port can see the other, and neither can see the container. That is the
whole point: a drill's conclusions are the platform's conclusions.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from signalforge.application.drills.ports import (
    DrillResponse,
    DrillTransport,
    DrillWorld,
)


class InProcessTransport(DrillTransport):
    """SignalForge over the FastAPI test client."""

    def __init__(self, client):
        self._client = client
        self.calls: list[tuple[str, str, int]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        token: Optional[str] = None,
        json: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
    ) -> DrillResponse:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = self._client.request(
            method,
            path,
            headers=headers,
            json=dict(json) if json is not None else None,
            params=dict(params) if params is not None else None,
        )
        self.calls.append((method, path, response.status_code))
        body: Any
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = response.text
        return DrillResponse(status=response.status_code, body=body)


class ObserveWorld(DrillWorld):
    """The watched application: the observe double, and the faults put into it."""

    def __init__(self, harness):
        self._harness = harness

    @property
    def state(self):
        return self._harness.state

    def apply(self, verb: str, argument: Any) -> None:  # noqa: C901
        state = self._harness.state
        if verb == "set_health":
            state.health = dict(argument)
        elif verb == "set_metrics":
            state.metrics_text = str(argument)
        elif verb == "set_actions":
            state.actions = dict(argument)
        elif verb == "set_topology":
            state.topology = dict(argument)
        elif verb == "set_logs":
            state.logs = [dict(row) for row in argument]
        elif verb == "times_out":
            for path in _as_list(argument):
                self._harness.times_out(str(path))
        elif verb == "clear_timeouts":
            self._harness.timing_out.clear()
        elif verb == "fail_path":
            for path, status in dict(argument).items():
                state.fail(str(path), int(status))
        elif verb == "clear_failures":
            state.status_overrides.clear()
        elif verb == "record_execution":
            record = dict(argument)
            state.executions[str(record["idempotency_key"])] = record
        elif verb == "set_probe_path":
            # A probe target the application does serve, or does not: the
            # customer-path failure a drill needs is a real 404 from a real
            # route, not a stubbed verdict.
            state.status_overrides.pop(str(argument), None)
        else:  # pragma: no cover - the scenario loader refuses unknown verbs
            raise NotImplementedError(verb)

    async def inject(
        self, *, scenario_id: str, params: Mapping[str, Any], scope: str, key: str
    ) -> Mapping[str, Any]:
        async with self._harness.client_factory(10.0) as client:
            response = await client.post(
                f"{self._harness.BASE_URL}/observe/chaos/{scenario_id}/inject",
                headers={"Authorization": f"Bearer {self._harness.state.token}"},
                json={"params": dict(params), "scope": scope, "idempotency_key": key},
            )
            response.raise_for_status()
            return response.json()


def _as_list(argument: Any) -> list[Any]:
    return list(argument) if isinstance(argument, (list, tuple)) else [argument]
