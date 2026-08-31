"""Connector bindings: one application wired to one adapter (REQ-030).

A manifest says what an adapter can do. A binding says that *this* tenant's
*this* application uses it, with this configuration, at this credential scope.
The credentials themselves are never here - the binding holds only the names of
the secrets, and the values live Fernet-encrypted in the secret store (REQ-015),
so a binding can be listed, logged and returned over the API without a decrypt.

Bindings are per (tenant, app, adapter, credential scope). The scope is part of
the key on purpose: read-only and remediation are separate credentials for
separate blast radii, so they are separate rows, and revoking one does not touch
the other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping, Optional

import jsonschema

from signalforge.contracts.canonical import HealthStatus
from signalforge.contracts.envelope import AdapterKind
from signalforge.domain.adapters.capabilities import is_mutating
from signalforge.domain.adapters.errors import ConnectorConfigError, CredentialScopeError
from signalforge.domain.adapters.manifest import (
    AdapterManifest,
    AdapterMode,
    CredentialScope,
)

BINDING_NAME_PATTERN = re.compile(r"^[\w][\w .:/-]{0,79}$")

SECRET_PREFIX = "connector"

DOWN_AFTER_FAILURES = 3
"""One failed poll is a blip and reads DEGRADED; the third consecutive failure
is a fact and reads DOWN. Health that flaps to DOWN on a single dropped packet
is health nobody looks at."""


def secret_name(binding_id: str, credential_field: str) -> str:
    """Where a binding's credential lives in the tenant's secret store. Encoding
    the binding id means deleting a binding can delete exactly its own secrets."""
    return f"{SECRET_PREFIX}/{binding_id}/{credential_field}"


@dataclass(frozen=True)
class ConnectorBinding:
    id: str
    tenant_id: str
    app_id: str
    adapter_id: str
    name: str
    credential_scope: CredentialScope
    config: Mapping[str, Any] = field(default_factory=dict)
    credential_fields: tuple[str, ...] = ()
    enabled: bool = True
    created_at: Optional[datetime] = None
    created_by: str = ""
    updated_at: Optional[datetime] = None
    status: HealthStatus = HealthStatus.UNKNOWN
    status_detail: Optional[str] = None
    last_checked_at: Optional[datetime] = None
    last_ok_at: Optional[datetime] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0

    def __post_init__(self) -> None:
        if not BINDING_NAME_PATTERN.match(self.name or ""):
            raise ConnectorConfigError(
                "connector name must be 1-80 printable characters"
            )
        if not self.app_id:
            raise ConnectorConfigError("a connector binding must name an app_id")

    # -- health transitions -------------------------------------------------

    def observed_ok(self, at: datetime, detail: Optional[str] = None) -> "ConnectorBinding":
        return replace(
            self,
            status=HealthStatus.UP,
            status_detail=detail,
            last_checked_at=at,
            last_ok_at=at,
            last_error=None,
            consecutive_failures=0,
        )

    def observed_failure(
        self, at: datetime, error: str, *, terminal: bool = False
    ) -> "ConnectorBinding":
        """`terminal` marks a failure that will not heal on its own - rejected
        credentials, an uninstalled adapter - so it reads DOWN immediately
        instead of spending two more polls pretending it might recover."""
        failures = self.consecutive_failures + 1
        status = (
            HealthStatus.DOWN
            if terminal or failures >= DOWN_AFTER_FAILURES
            else HealthStatus.DEGRADED
        )
        return replace(
            self,
            status=status,
            status_detail=error,
            last_checked_at=at,
            last_error=error,
            consecutive_failures=failures,
        )

    def observed_unknown(self, at: datetime, detail: str) -> "ConnectorBinding":
        """Nothing was measured. Not a failure, and deliberately not health
        either: an adapter with no runtime installed here is UNKNOWN, and the
        integration health screen says so rather than showing a green tick."""
        return replace(
            self,
            status=HealthStatus.UNKNOWN,
            status_detail=detail,
            last_checked_at=at,
            consecutive_failures=0,
        )

    def freshness_s(self, now: datetime) -> Optional[float]:
        if self.last_ok_at is None:
            return None
        return max((now - self.last_ok_at).total_seconds(), 0.0)

    def with_config(
        self, config: Mapping[str, Any], *, at: datetime
    ) -> "ConnectorBinding":
        return replace(self, config=dict(config), updated_at=at)

    def with_enabled(self, enabled: bool, *, at: datetime) -> "ConnectorBinding":
        return replace(self, enabled=enabled, updated_at=at)


def validate_config(manifest: AdapterManifest, config: Mapping[str, Any]) -> dict[str, Any]:
    """Check a binding's config against the manifest's own config_schema.

    The schema is the adapter's; the enforcement is the platform's. A wizard
    step renders fields from this schema and a bad value is refused here, once,
    rather than by whatever the adapter does with a missing base URL at 3am.
    """
    if not isinstance(config, Mapping):
        raise ConnectorConfigError("connector config must be an object")
    document = dict(config)
    if not manifest.config_schema:
        return document
    validator = jsonschema.Draft202012Validator(dict(manifest.config_schema))
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(str(p) for p in e.absolute_path) or 'config'}: {e.message}"
            for e in errors[:5]
        )
        raise ConnectorConfigError(f"{manifest.id} config invalid - {detail}")
    return document


def assert_scope_offered(manifest: AdapterManifest, scope: CredentialScope) -> None:
    """A binding may only claim a scope the adapter offers."""
    if not manifest.allows_scope(scope):
        raise CredentialScopeError(
            f"adapter {manifest.id} does not offer the {scope.value!r} credential "
            f"scope; it offers {[s.value for s in manifest.credential_scopes]}"
        )


def assert_can_invoke(
    manifest: AdapterManifest, binding: ConnectorBinding, capability: str
) -> None:
    """The credential-scope floor (CLAUDE.md rule 4).

    Checked here, in the domain, so every future caller - the Tool Gateway, a
    drill, an agent, a hand-written script - passes through the same three
    rules rather than each re-deriving them:

    1. The adapter must actually declare the capability.
    2. A read-only credential can never drive a mutating capability.
    3. Chaos runs only on a drill credential, and a drill credential runs only
       chaos - a drill key must not become a general remediation key.
    """
    if not manifest.supports(capability):
        raise CredentialScopeError(
            f"adapter {manifest.id} does not support {capability!r}"
        )
    mutating = is_mutating(manifest.kind, capability)
    if mutating and binding.credential_scope is CredentialScope.READ_ONLY:
        raise CredentialScopeError(
            f"{capability!r} changes {manifest.id} and cannot run on a read_only "
            "credential"
        )
    if manifest.kind is AdapterKind.CHAOS and binding.credential_scope is not (
        CredentialScope.DRILL
    ):
        raise CredentialScopeError(
            "chaos injection requires a drill-scoped credential"
        )
    if (
        binding.credential_scope is CredentialScope.DRILL
        and manifest.kind is not AdapterKind.CHAOS
        and mutating
    ):
        raise CredentialScopeError(
            f"a drill credential may not perform {capability!r} on a "
            f"{manifest.kind.value} adapter"
        )
    if mutating and manifest.mode is not AdapterMode.INVOKE:
        raise CredentialScopeError(
            f"adapter {manifest.id} is a {manifest.mode.value} adapter and cannot "
            f"perform {capability!r}"
        )


def direction(manifest: AdapterManifest) -> str:
    """What the integration health screen shows in its direction column."""
    return {
        AdapterMode.PULL: "inbound (SignalForge polls)",
        AdapterMode.PUSH: "inbound (the app posts)",
        AdapterMode.INVOKE: "outbound (SignalForge calls)",
    }[manifest.mode]
