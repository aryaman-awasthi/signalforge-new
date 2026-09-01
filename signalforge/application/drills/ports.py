"""What a drill needs from the outside world (REQ-070).

Three ports, and the boundary between them is the boundary that makes a drill
worth running:

* **DrillTransport** is SignalForge's own public API. Every human and machine
  step goes through it, over the same routes a browser uses, with a token minted
  by the real auth service. There is no back door into the container.
* **DrillWorld** is the *application being watched* - the reference harness or a
  double serving the frozen observe contract. A drill may make it unhealthy,
  make it slow, make it stop answering, or reveal that an action landed after
  the platform gave up waiting. It may not touch SignalForge.
* **DrillClock** is scenario time. Advancing it is how an approval expires and a
  stability window completes without anything sleeping.

That the world and the platform are different ports is the reason a drill proves
anything: the platform's conclusions come out of the platform, from telemetry it
read itself.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class DrillResponse:
    status: int
    body: Any

    @property
    def json(self) -> Any:
        return self.body


class DrillTransport(ABC):
    """SignalForge, reached the way anything else reaches it."""

    @abstractmethod
    async def request(
        self,
        method: str,
        path: str,
        *,
        token: Optional[str] = None,
        json: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
    ) -> DrillResponse: ...


class DrillWorld(ABC):
    """The application under observation, and the faults a drill may put in it."""

    @abstractmethod
    def apply(self, verb: str, argument: Any) -> None:
        """Perform one closed-vocabulary world verb (domain/drills/scenario.py)."""

    @abstractmethod
    async def inject(
        self, *, scenario_id: str, params: Mapping[str, Any], scope: str, key: str
    ) -> Mapping[str, Any]:
        """Inject a fault through the application's chaos contract.

        ``scope`` is always the drill's own ``drill:<id>``; the chaos contract
        refuses anything else, which is what keeps a game day's faults inside a
        game day.
        """


class DrillClock(ABC):
    """Scenario time, moved explicitly."""

    @abstractmethod
    def now(self) -> datetime: ...

    @abstractmethod
    def advance(self, seconds: float) -> None: ...

    def pause(self) -> None:  # pragma: no cover - optional capability
        raise NotImplementedError

    def resume(self) -> None:  # pragma: no cover - optional capability
        raise NotImplementedError

    def set_speed(self, speed: float) -> None:  # pragma: no cover - optional
        raise NotImplementedError
