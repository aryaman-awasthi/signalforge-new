"""The adapter manifest: what an integration declares about itself (REQ-030).

An adapter is a folder with a `manifest.yaml`. The schema is hardcoded here and
the content is declared by the adapter, which is the rule the whole plug-and-play
story rests on: SignalForge decides what a manifest may say, the adapter decides
what it does say, and nothing in the core knows the name of any application.

Everything in this module is a pure value object. Parsing happens in
infrastructure (it reads files); validation happens here, so a manifest that
would violate a floor cannot be loaded by any reader, including a test fixture
or a future operator-uploaded bundle.

The floors this file enforces, none of them configurable:

1. Capabilities must be real SPI methods of the declared kind. A manifest cannot
   promise something the SPI has no name for.
2. `mode` and capabilities must agree: an `invoke` adapter declares at least one
   mutating capability, and a `pull` or `push` adapter declares none. A manifest
   cannot smuggle a write behind a reader's mode.
3. An adapter with mutating capabilities may not offer the `read_only`
   credential scope, and a chaos adapter may offer only `drill`. This is the
   credential half of the policy floor: a scope that could never legitimately be
   used is not offered in the first place.
4. Kinds that carry free text out of the customer's estate must state a
   redaction policy explicitly, even if that policy redacts nothing.
5. Limits are bounded by the domain. A manifest may ask for less than the
   ceiling; it can never ask for more.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Any, Mapping, Optional, Sequence

import jsonschema

from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.errors import ManifestValidationError
from signalforge.domain.adapters.capabilities import (
    NORMALIZE_CAPABILITY,
    REDACTION_REQUIRED_KINDS,
    capabilities_for,
    mutating_for,
    unknown_capabilities,
)

ADAPTER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

MAX_TIMEOUT_S = 120.0
MAX_CALLS_PER_MINUTE = 6_000
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_ROWS = 50_000

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_CALLS_PER_MINUTE = 120
DEFAULT_RESULT_BYTES = 1024 * 1024
DEFAULT_ROWS = 1_000

REDACTED = "***"


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> "re.Pattern[str]":
    """Patterns are validated at manifest load, so compiling here cannot fail;
    the cache exists because a logs adapter applies the same handful of patterns
    to every record of every page."""
    return re.compile(pattern)


class AdapterMode(str, Enum):
    """Which way the data flows, from SignalForge's point of view."""

    PULL = "pull"      # SignalForge calls the integrated system on a schedule
    PUSH = "push"      # the integrated system calls SignalForge's ingest API
    INVOKE = "invoke"  # SignalForge asks the integrated system to change something


class CredentialScope(str, Enum):
    """Separate credentials for separate blast radii (architecture section 8).

    They are separate bindings, not settings on one binding: the read-only key
    and the remediation key are different secrets held for different reasons,
    and losing one must not imply the other.
    """

    READ_ONLY = "read_only"
    REMEDIATION = "remediation"
    DRILL = "drill"


class AuthMethod(str, Enum):
    NONE = "none"
    BEARER = "bearer"
    BASIC = "basic"
    API_KEY = "api_key"
    HMAC = "hmac"


class Availability(str, Enum):
    """Whether this deployment can actually run the adapter.

    BUILT_IN ships with a runtime. AVAILABLE means the interface exists and the
    registry shows it, but no runtime is installed here - which is the honest
    label for the enterprise connectors, and the reason the health loop reports
    such a binding UNKNOWN rather than pretending it is fine.
    """

    BUILT_IN = "built_in"
    AVAILABLE = "available"


@dataclass(frozen=True)
class AdapterAuth:
    method: AuthMethod = AuthMethod.NONE
    credential_fields: tuple[str, ...] = ()
    header: Optional[str] = None

    def __post_init__(self) -> None:
        if self.method is AuthMethod.NONE and self.credential_fields:
            raise ManifestValidationError(
                "auth.method 'none' cannot declare credential_fields"
            )
        if self.method is not AuthMethod.NONE and not self.credential_fields:
            raise ManifestValidationError(
                f"auth.method '{self.method.value}' must declare credential_fields"
            )
        for name in self.credential_fields:
            if not re.match(r"^[a-z][a-z0-9_]{0,39}$", name):
                raise ManifestValidationError(
                    f"auth.credential_fields: {name!r} is not a snake_case field name"
                )
        if len(set(self.credential_fields)) != len(self.credential_fields):
            raise ManifestValidationError("auth.credential_fields contains duplicates")


@dataclass(frozen=True)
class AdapterLimits:
    """Per-call ceilings. Bounded by the domain: config tunes inside them."""

    timeout_s: float = DEFAULT_TIMEOUT_S
    max_calls_per_minute: int = DEFAULT_CALLS_PER_MINUTE
    max_result_bytes: int = DEFAULT_RESULT_BYTES
    max_rows: int = DEFAULT_ROWS

    def __post_init__(self) -> None:
        self._bounded("timeout_s", self.timeout_s, 0, MAX_TIMEOUT_S)
        self._bounded("max_calls_per_minute", self.max_calls_per_minute, 0, MAX_CALLS_PER_MINUTE)
        self._bounded("max_result_bytes", self.max_result_bytes, 0, MAX_RESULT_BYTES)
        self._bounded("max_rows", self.max_rows, 0, MAX_ROWS)

    @staticmethod
    def _bounded(name: str, value: float, low: float, high: float) -> None:
        if not (low < value <= high):
            raise ManifestValidationError(
                f"limits.{name} must be greater than {low} and at most {high}, got {value}"
            )


@dataclass(frozen=True)
class AdapterRedaction:
    """What the adapter promises to strip before anything it returns is stored,
    shown, or put in front of a model. `policy_id` names the promise so an
    evidence record can cite it."""

    policy_id: Optional[str] = None
    fields: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()

    @property
    def declared(self) -> bool:
        return bool(self.policy_id)

    def apply(self, text: str) -> str:
        """Mask the declared patterns in free text.

        This is where the promise in the manifest becomes an actual removal.
        Adapters for the kinds that carry customer text - logs, traces,
        collaboration, knowledge, itsm - run it on everything they return, so
        redaction happens once, at the boundary, before the text can reach an
        event payload, an evidence record or a prompt.
        """
        if not text or not self.patterns:
            return text
        masked = text
        for pattern in self.patterns:
            masked = _compiled(pattern).sub(REDACTED, masked)
        return masked

    def __post_init__(self) -> None:
        if (self.fields or self.patterns) and not self.policy_id:
            raise ManifestValidationError(
                "redaction rules must carry a policy_id so evidence can cite them"
            )
        for pattern in self.patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ManifestValidationError(
                    f"redaction.patterns: {pattern!r} is not a valid regex ({exc})"
                ) from exc


@dataclass(frozen=True)
class AdapterManifest:
    id: str
    name: str
    version: str
    kind: AdapterKind
    mode: AdapterMode
    capabilities: frozenset[str]
    credential_scopes: tuple[CredentialScope, ...]
    auth: AdapterAuth = field(default_factory=AdapterAuth)
    config_schema: Mapping[str, Any] = field(default_factory=dict)
    limits: AdapterLimits = field(default_factory=AdapterLimits)
    redaction: AdapterRedaction = field(default_factory=AdapterRedaction)
    availability: Availability = Availability.BUILT_IN
    simulated: bool = False
    vendor: str = "generic"
    description: str = ""

    def __post_init__(self) -> None:
        self._validate_identity()
        self._validate_capabilities()
        self._validate_mode()
        self._validate_scopes()
        self._validate_redaction()
        self._validate_config_schema()

    # -- derived -----------------------------------------------------------

    @property
    def mutating_capabilities(self) -> frozenset[str]:
        return frozenset(self.capabilities & mutating_for(self.kind))

    @property
    def is_mutating(self) -> bool:
        return bool(self.mutating_capabilities)

    @property
    def catalogue(self) -> frozenset[str]:
        """Every capability this kind could have, supported or not - the columns
        of the capability matrix."""
        return capabilities_for(self.kind)

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def allows_scope(self, scope: CredentialScope) -> bool:
        return scope in self.credential_scopes

    def redact(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Mask the fields this manifest promised to redact. Used for anything
        that leaves the process: API responses, log lines, event payloads."""
        masked = dict(payload)
        for name in self.redaction.fields:
            if name in masked:
                masked[name] = REDACTED
        return masked

    def redact_text(self, text: str) -> str:
        """Mask this manifest's declared patterns in a free-text value."""
        return self.redaction.apply(text)

    # -- invariants --------------------------------------------------------

    def _validate_identity(self) -> None:
        if not ADAPTER_ID_PATTERN.match(self.id):
            raise ManifestValidationError(
                f"adapter id {self.id!r} must be lowercase kebab-case, 3-64 characters"
            )
        if not VERSION_PATTERN.match(self.version):
            raise ManifestValidationError(
                f"adapter {self.id}: version {self.version!r} must be MAJOR.MINOR.PATCH"
            )
        if not self.name.strip():
            raise ManifestValidationError(f"adapter {self.id}: name is required")

    def _validate_capabilities(self) -> None:
        if not self.capabilities:
            raise ManifestValidationError(
                f"adapter {self.id}: declare at least one capability"
            )
        unknown = unknown_capabilities(self.kind, self.capabilities)
        if unknown:
            raise ManifestValidationError(
                f"adapter {self.id}: {list(unknown)} are not methods of the "
                f"{self.kind.value} SPI; supported names are "
                f"{sorted(capabilities_for(self.kind))}"
            )

    def _validate_mode(self) -> None:
        mutating = self.mutating_capabilities
        if self.mode is AdapterMode.INVOKE and not mutating:
            raise ManifestValidationError(
                f"adapter {self.id}: mode 'invoke' must declare a mutating "
                f"capability, one of {sorted(mutating_for(self.kind)) or '[]'}"
            )
        if self.mode is not AdapterMode.INVOKE and mutating:
            raise ManifestValidationError(
                f"adapter {self.id}: mode {self.mode.value!r} cannot declare the "
                f"mutating capabilities {sorted(mutating)}; use mode 'invoke'"
            )
        if self.mode is AdapterMode.PUSH and NORMALIZE_CAPABILITY not in self.capabilities:
            raise ManifestValidationError(
                f"adapter {self.id}: a push adapter must declare "
                f"{NORMALIZE_CAPABILITY!r} - that is what turns an inbound payload "
                "into a canonical model"
            )
        if self.mode is AdapterMode.PULL and self.capabilities == {NORMALIZE_CAPABILITY}:
            raise ManifestValidationError(
                f"adapter {self.id}: a pull adapter must declare a fetching "
                "capability, not only 'normalize'"
            )

    def _validate_scopes(self) -> None:
        if not self.credential_scopes:
            raise ManifestValidationError(
                f"adapter {self.id}: declare at least one credential_scope"
            )
        if len(set(self.credential_scopes)) != len(self.credential_scopes):
            raise ManifestValidationError(
                f"adapter {self.id}: credential_scopes contains duplicates"
            )
        if self.is_mutating and CredentialScope.READ_ONLY in self.credential_scopes:
            raise ManifestValidationError(
                f"adapter {self.id}: an adapter that can "
                f"{sorted(self.mutating_capabilities)} may not offer the "
                "'read_only' credential scope"
            )
        if self.kind is AdapterKind.CHAOS and set(self.credential_scopes) != {
            CredentialScope.DRILL
        }:
            raise ManifestValidationError(
                f"adapter {self.id}: chaos adapters run on a drill credential and "
                "nothing else; credential_scopes must be exactly ['drill']"
            )

    def _validate_redaction(self) -> None:
        if self.kind in REDACTION_REQUIRED_KINDS and not self.redaction.declared:
            raise ManifestValidationError(
                f"adapter {self.id}: {self.kind.value} adapters carry free text out "
                "of the integrated system, so redaction.policy_id is required"
            )

    def _validate_config_schema(self) -> None:
        if not self.config_schema:
            return
        try:
            jsonschema.Draft202012Validator.check_schema(dict(self.config_schema))
        except jsonschema.SchemaError as exc:
            raise ManifestValidationError(
                f"adapter {self.id}: config_schema is not a valid JSON schema ({exc.message})"
            ) from exc


def _enum(value: Any, enum_cls, field_name: str, adapter_id: str):
    try:
        return enum_cls(value)
    except ValueError as exc:
        allowed = [member.value for member in enum_cls]
        raise ManifestValidationError(
            f"adapter {adapter_id}: {field_name} {value!r} is not one of {allowed}"
        ) from exc


def _str_tuple(value: Any, field_name: str, adapter_id: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ManifestValidationError(
            f"adapter {adapter_id}: {field_name} must be a list of strings"
        )
    return tuple(str(item) for item in value)


def _mapping(value: Any, field_name: str, adapter_id: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ManifestValidationError(
            f"adapter {adapter_id}: {field_name} must be a mapping"
        )
    return dict(value)


def manifest_from_mapping(document: Mapping[str, Any]) -> AdapterManifest:
    """Build a manifest from a parsed YAML/JSON document.

    Every failure is a ManifestValidationError naming the adapter and the field,
    because the person reading it is writing a manifest, not debugging
    SignalForge.
    """
    if not isinstance(document, Mapping):
        raise ManifestValidationError("manifest must be a mapping at the top level")

    adapter_id = str(document.get("id", "<missing id>"))
    required = ["id", "name", "version", "kind", "mode", "capabilities", "credential_scopes"]
    missing = [key for key in required if document.get(key) in (None, "", [], {})]
    if missing:
        raise ManifestValidationError(
            f"adapter {adapter_id}: missing required fields {missing}"
        )

    unknown_keys = set(document) - {
        "id", "name", "version", "kind", "mode", "capabilities", "credential_scopes",
        "auth", "config_schema", "limits", "redaction", "availability", "simulated",
        "vendor", "description",
    }
    if unknown_keys:
        raise ManifestValidationError(
            f"adapter {adapter_id}: unknown manifest fields {sorted(unknown_keys)}"
        )

    auth_doc = _mapping(document.get("auth"), "auth", adapter_id)
    limits_doc = _mapping(document.get("limits"), "limits", adapter_id)
    redaction_doc = _mapping(document.get("redaction"), "redaction", adapter_id)

    return AdapterManifest(
        id=adapter_id,
        name=str(document["name"]),
        version=str(document["version"]),
        kind=_enum(document["kind"], AdapterKind, "kind", adapter_id),
        mode=_enum(document["mode"], AdapterMode, "mode", adapter_id),
        capabilities=frozenset(
            _str_tuple(document["capabilities"], "capabilities", adapter_id)
        ),
        credential_scopes=tuple(
            _enum(scope, CredentialScope, "credential_scopes", adapter_id)
            for scope in _str_tuple(
                document["credential_scopes"], "credential_scopes", adapter_id
            )
        ),
        auth=AdapterAuth(
            method=_enum(
                auth_doc.get("method", AuthMethod.NONE.value),
                AuthMethod,
                "auth.method",
                adapter_id,
            ),
            credential_fields=_str_tuple(
                auth_doc.get("credential_fields"), "auth.credential_fields", adapter_id
            ),
            header=auth_doc.get("header"),
        ),
        config_schema=_mapping(document.get("config_schema"), "config_schema", adapter_id),
        limits=AdapterLimits(
            timeout_s=float(limits_doc.get("timeout_s", DEFAULT_TIMEOUT_S)),
            max_calls_per_minute=int(
                limits_doc.get("max_calls_per_minute", DEFAULT_CALLS_PER_MINUTE)
            ),
            max_result_bytes=int(limits_doc.get("max_result_bytes", DEFAULT_RESULT_BYTES)),
            max_rows=int(limits_doc.get("max_rows", DEFAULT_ROWS)),
        ),
        redaction=AdapterRedaction(
            policy_id=redaction_doc.get("policy_id"),
            fields=_str_tuple(redaction_doc.get("fields"), "redaction.fields", adapter_id),
            patterns=_str_tuple(
                redaction_doc.get("patterns"), "redaction.patterns", adapter_id
            ),
        ),
        availability=_enum(
            document.get("availability", Availability.BUILT_IN.value),
            Availability,
            "availability",
            adapter_id,
        ),
        simulated=bool(document.get("simulated", False)),
        vendor=str(document.get("vendor", "generic")),
        description=str(document.get("description", "")),
    )
