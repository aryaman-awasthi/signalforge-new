"""SignalForge public contracts. Frozen (CLAUDE.md rule 3): referenced, never
redefined; changes require an explicit human instruction quoted in the commit."""

from signalforge.contracts.envelope import (
    Actor,
    ActorType,
    AdapterKind,
    Authority,
    EventEnvelope,
    Plane,
    Redaction,
    Source,
    Topics,
    canonical_payload_hash,
)
from signalforge.contracts.errors import (
    AdapterAuthError,
    AdapterError,
    AdapterTimeoutError,
    AdapterUnavailableError,
    ManifestValidationError,
    NotSupportedError,
)
from signalforge.contracts.manifests import (
    validate_action_manifest,
    validate_chaos_manifest,
)
from signalforge.contracts.roles import ROLE_PERMISSIONS, Permission, Role, role_has
from signalforge.contracts.spi import ADAPTER_SPI_BY_KIND

__all__ = [
    "Actor",
    "ActorType",
    "AdapterKind",
    "Authority",
    "EventEnvelope",
    "Plane",
    "Redaction",
    "Source",
    "Topics",
    "canonical_payload_hash",
    "AdapterError",
    "AdapterAuthError",
    "AdapterTimeoutError",
    "AdapterUnavailableError",
    "NotSupportedError",
    "ManifestValidationError",
    "validate_action_manifest",
    "validate_chaos_manifest",
    "Role",
    "Permission",
    "ROLE_PERMISSIONS",
    "role_has",
    "ADAPTER_SPI_BY_KIND",
]
