"""Reaching a running SignalForge, and a running application, over HTTP.

Used by scripts/drill_run.py against a live install. The in-process equivalent
used by the drill suite is in the tests, because an ASGI test transport is a
testing technology and does not belong in a shipped adapter.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

import httpx

from signalforge.application.drills.ports import DrillResponse, DrillTransport, DrillWorld

DEFAULT_TIMEOUT_S = 30.0


class HttpDrillTransport(DrillTransport):
    """SignalForge's public API over a socket."""

    def __init__(self, base_url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_s
        )

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
        response = await self._client.request(
            method, path, headers=headers, json=json, params=params
        )
        body: Any
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = response.text
        return DrillResponse(status=response.status_code, body=body)

    async def aclose(self) -> None:
        await self._client.aclose()


class HttpDrillWorld(DrillWorld):
    """A live application, reached through the frozen observe contract.

    A live harness is not a dictionary: a drill cannot set its health, it can
    only inject a declared chaos scenario and let the application become
    unhealthy on its own. So the world verbs a file may use against a live
    application are a strict subset, and the ones that only make sense against a
    double are refused here rather than silently ignored.
    """

    LIVE_VERBS = frozenset({"clear_timeouts", "clear_failures"})

    def __init__(
        self, base_url: str, *, credential: str, timeout_s: float = DEFAULT_TIMEOUT_S
    ):
        self._base_url = base_url.rstrip("/")
        self._credential = credential
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout_s)

    def apply(self, verb: str, argument: Any) -> None:
        if verb in self.LIVE_VERBS:
            return
        raise NotImplementedError(
            f"world verb {verb!r} sets state on a test double; a live application "
            f"is changed by injecting a chaos scenario, not by assignment"
        )

    async def inject(
        self, *, scenario_id: str, params: Mapping[str, Any], scope: str, key: str
    ) -> Mapping[str, Any]:
        response = await self._client.post(
            f"/observe/chaos/{scenario_id}/inject",
            headers={"Authorization": f"Bearer {self._credential}"},
            json={"params": dict(params), "scope": scope, "idempotency_key": key},
        )
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()
