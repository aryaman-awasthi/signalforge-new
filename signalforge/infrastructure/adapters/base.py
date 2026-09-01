"""Shared plumbing for the shipped adapter runtimes.

A mixin rather than a base class in the SPI hierarchy, because the SPI is frozen
and its thirteen kinds are the only base classes an adapter may claim. What it
provides is the four things every runtime does identically - read its own
binding's config, build a transport bounded by its own manifest's limits, ask
the platform clock for the time, and apply its own declared redaction - so that
no adapter re-implements them and none of them can drift apart.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from signalforge.contracts.errors import AdapterError
from signalforge.domain.adapters.manifest import AdapterManifest
from signalforge.domain.adapters.ports import AdapterContext
from signalforge.infrastructure.adapters.services import AdapterServices
from signalforge.infrastructure.adapters.transport import HttpTransport


class GenericAdapter:
    def __init__(self, context: AdapterContext, services: AdapterServices):
        self.context = context
        self.services = services

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(adapter={self.spec.id!r}, "
            f"binding={self.binding.id!r})"
        )

    # -- identity ----------------------------------------------------------

    @property
    def spec(self) -> AdapterManifest:
        """This adapter's own manifest.

        Called `spec` and not `manifest` because `manifest` is an SPI *method*
        on the actions and chaos kinds - an adapter that imports the integrated
        system's action manifest would otherwise shadow its own declaration and
        lose its limits, auth and redaction policy.
        """
        return self.context.manifest

    @property
    def binding(self):
        return self.context.binding

    @property
    def tenant_id(self) -> str:
        return self.binding.tenant_id

    @property
    def app_id(self) -> str:
        return self.binding.app_id

    def now(self) -> datetime:
        return self.services.clock.now()

    # -- configuration -----------------------------------------------------

    def config(self, key: str, default: Any = None) -> Any:
        return dict(self.binding.config).get(key, default)

    def required_config(self, key: str) -> Any:
        value = self.config(key)
        if value in (None, ""):
            # The manifest's config_schema already refused this at bind time;
            # reaching it means a manifest and its runtime disagree, which is a
            # packaging fault worth naming rather than an AttributeError later.
            raise AdapterError(
                f"adapter {self.spec.id} needs config {key!r}, which this "
                f"binding does not set"
            )
        return value

    # -- outbound ----------------------------------------------------------

    def transport(self, base_url: Optional[str] = None) -> HttpTransport:
        return HttpTransport(
            base_url=base_url or self.required_config("base_url"),
            auth=self.spec.auth,
            credentials=self.context.credentials,
            limits=self.spec.limits,
            budget=self.services.budget,
            # Budgets are per binding: two apps polling the same adapter have
            # separate ceilings, and one noisy app cannot spend another's.
            budget_key=f"{self.spec.id}:{self.binding.id}",
            verify_tls=bool(self.config("verify_tls", self.services.verify_tls)),
            client_factory=self.services.client_factory,
        )

    # -- redaction ---------------------------------------------------------

    def redact(self, text: Optional[str]) -> Optional[str]:
        """Everything free-text this adapter returns goes through here, so the
        manifest's redaction promise is applied once, at the boundary, before the
        text can reach an event, an evidence record or a prompt."""
        if text is None:
            return None
        return self.spec.redact_text(text)

    def truncate(self, rows: list, limit: Optional[int] = None) -> list:
        """Apply the manifest's max_rows ceiling. A caller asking for more than
        the adapter is allowed to return gets the ceiling, not an error: a bound
        that fails the query would make the limit useless in exactly the moment
        it matters."""
        ceiling = min(limit or self.spec.limits.max_rows, self.spec.limits.max_rows)
        return rows[:ceiling]
