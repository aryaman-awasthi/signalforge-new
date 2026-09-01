"""Wire shapes for the adapter registry and connector bindings (REQ-030).

Two rules hold for everything in this module: a credential value never appears
in an output model (only the *names* of the fields do), and config on the way
out passes through the manifest's declared redaction first.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from signalforge.application.adapters.connector_service import ConnectorView
from signalforge.application.adapters.registry_service import RegistryEntry
from signalforge.domain.adapters.bindings import direction
from signalforge.domain.adapters.manifest import AdapterManifest, CredentialScope
from signalforge.domain.adapters.matrix import CapabilityMatrix
from signalforge.domain.adapters.ports import ManifestLoadError


class AdapterAuthOut(BaseModel):
    method: str
    credential_fields: list[str]
    header: Optional[str] = None


class AdapterLimitsOut(BaseModel):
    timeout_s: float
    max_calls_per_minute: int
    max_result_bytes: int
    max_rows: int


class AdapterRedactionOut(BaseModel):
    policy_id: Optional[str] = None
    fields: list[str] = []
    patterns: list[str] = []


class AdapterOut(BaseModel):
    id: str
    name: str
    version: str
    kind: str
    mode: str
    direction: str
    vendor: str
    description: str
    availability: str
    status: str
    runnable: bool
    simulated: bool
    capabilities: list[str]
    unsupported: list[str]
    mutating_capabilities: list[str]
    credential_scopes: list[str]
    auth: AdapterAuthOut
    limits: AdapterLimitsOut
    redaction: AdapterRedactionOut
    config_schema: dict[str, Any]


class ManifestErrorOut(BaseModel):
    path: str
    error: str


class RegistryOut(BaseModel):
    adapters: list[AdapterOut]
    errors: list[ManifestErrorOut] = Field(
        default=[],
        description="Manifests that failed to load; the rest of the registry still works",
    )


class CapabilityCellOut(BaseModel):
    capability: str
    supported: bool
    mutating: bool


class CapabilityRowOut(BaseModel):
    adapter_id: str
    name: str
    kind: str
    mode: str
    availability: str
    simulated: bool
    credential_scopes: list[str]
    cells: list[CapabilityCellOut]


class KindCoverageOut(BaseModel):
    kind: str
    capabilities: list[str]
    adapters: list[str]
    covered: list[str]
    uncovered: list[str]
    complete: bool


class CapabilityMatrixOut(BaseModel):
    rows: list[CapabilityRowOut]
    coverage: list[KindCoverageOut]


class ConnectorIn(BaseModel):
    app_id: str = Field(min_length=1, max_length=100)
    adapter_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    credential_scope: CredentialScope
    config: dict[str, Any] = {}
    credentials: dict[str, str] = Field(
        default={},
        description="Credential values; encrypted at rest and never returned",
    )
    enabled: bool = True


class ConnectorPatchIn(BaseModel):
    config: Optional[dict[str, Any]] = None
    credentials: Optional[dict[str, str]] = None
    enabled: Optional[bool] = None


class ConnectorOut(BaseModel):
    id: str
    app_id: str
    adapter_id: str
    adapter_name: Optional[str] = None
    name: str
    kind: Optional[str] = None
    mode: Optional[str] = None
    direction: Optional[str] = None
    credential_scope: str
    config: dict[str, Any]
    credential_fields: list[str]
    enabled: bool
    simulated: bool
    installed: bool = Field(
        description="False when the adapter folder is gone but the binding remains"
    )
    status: str
    status_detail: Optional[str] = None
    last_checked_at: Optional[datetime] = None
    last_ok_at: Optional[datetime] = None
    last_error: Optional[str] = None
    consecutive_failures: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ConnectorHealthOut(ConnectorOut):
    """One row of the S23 integration health screen.

    `freshness_s` is the *pull* side - seconds since SignalForge last read the
    integrated system successfully. Everything prefixed `ingest_`, plus
    `last_event_at` and `event_freshness_s`, is the *push* side, read off the
    live ingest keys that name this binding. A push binding has no meaningful
    pull freshness and a pull binding has no ingest side; the screen shows
    whichever one its direction makes true.
    """

    freshness_s: Optional[float] = None
    last_event_at: Optional[datetime] = None
    event_freshness_s: Optional[float] = Field(
        default=None,
        description="Seconds since anything actually arrived on this binding's "
        "ingest keys; null when nothing ever has. A push integration cannot be "
        "measured by pinging - SignalForge is the server.",
    )
    ingest_keys: int = 0
    ingest_paths: list[str] = []
    ingest_accepted: int = 0
    ingest_duplicates: int = 0
    ingest_rejected: int = 0
    ingest_last_error: Optional[str] = None


def adapter_out(entry: RegistryEntry) -> AdapterOut:
    manifest: AdapterManifest = entry.manifest
    return AdapterOut(
        id=manifest.id,
        name=manifest.name,
        version=manifest.version,
        kind=manifest.kind.value,
        mode=manifest.mode.value,
        direction=direction(manifest),
        vendor=manifest.vendor,
        description=manifest.description,
        availability=manifest.availability.value,
        status=entry.status,
        runnable=entry.runnable,
        simulated=manifest.simulated,
        capabilities=sorted(manifest.capabilities),
        unsupported=sorted(manifest.catalogue - manifest.capabilities),
        mutating_capabilities=sorted(manifest.mutating_capabilities),
        credential_scopes=[scope.value for scope in manifest.credential_scopes],
        auth=AdapterAuthOut(
            method=manifest.auth.method.value,
            credential_fields=list(manifest.auth.credential_fields),
            header=manifest.auth.header,
        ),
        limits=AdapterLimitsOut(
            timeout_s=manifest.limits.timeout_s,
            max_calls_per_minute=manifest.limits.max_calls_per_minute,
            max_result_bytes=manifest.limits.max_result_bytes,
            max_rows=manifest.limits.max_rows,
        ),
        redaction=AdapterRedactionOut(
            policy_id=manifest.redaction.policy_id,
            fields=list(manifest.redaction.fields),
            patterns=list(manifest.redaction.patterns),
        ),
        config_schema=dict(manifest.config_schema),
    )


def manifest_error_out(error: ManifestLoadError) -> ManifestErrorOut:
    return ManifestErrorOut(path=error.path, error=error.error)


def matrix_out(matrix: CapabilityMatrix) -> CapabilityMatrixOut:
    return CapabilityMatrixOut(
        rows=[
            CapabilityRowOut(
                adapter_id=row.adapter_id,
                name=row.name,
                kind=row.kind.value,
                mode=row.mode,
                availability=row.availability.value,
                simulated=row.simulated,
                credential_scopes=list(row.credential_scopes),
                cells=[
                    CapabilityCellOut(
                        capability=cell.capability,
                        supported=cell.supported,
                        mutating=cell.mutating,
                    )
                    for cell in row.cells
                ],
            )
            for row in matrix.rows
        ],
        coverage=[
            KindCoverageOut(
                kind=coverage.kind.value,
                capabilities=list(coverage.capabilities),
                adapters=list(coverage.adapters),
                covered=list(coverage.covered),
                uncovered=list(coverage.uncovered),
                complete=coverage.complete,
            )
            for coverage in matrix.coverage
        ],
    )


def connector_out(view: ConnectorView) -> ConnectorOut:
    binding = view.binding
    manifest = view.manifest
    return ConnectorOut(
        id=binding.id,
        app_id=binding.app_id,
        adapter_id=binding.adapter_id,
        adapter_name=manifest.name if manifest else None,
        name=binding.name,
        kind=manifest.kind.value if manifest else None,
        mode=manifest.mode.value if manifest else None,
        direction=direction(manifest) if manifest else None,
        credential_scope=binding.credential_scope.value,
        config=view.config,
        credential_fields=list(binding.credential_fields),
        enabled=binding.enabled,
        simulated=bool(manifest and manifest.simulated),
        installed=manifest is not None,
        status=binding.status.value,
        status_detail=binding.status_detail,
        last_checked_at=binding.last_checked_at,
        last_ok_at=binding.last_ok_at,
        last_error=binding.last_error,
        consecutive_failures=binding.consecutive_failures,
        created_at=binding.created_at,
        updated_at=binding.updated_at,
    )


def connector_health_out(view: ConnectorView, now: datetime) -> ConnectorHealthOut:
    last_event_at = view.last_event_at
    return ConnectorHealthOut(
        **connector_out(view).model_dump(),
        freshness_s=view.binding.freshness_s(now),
        last_event_at=last_event_at,
        event_freshness_s=(
            None
            if last_event_at is None
            else max((now - last_event_at).total_seconds(), 0.0)
        ),
        ingest_keys=len(view.keys),
        ingest_paths=view.ingest_paths,
        ingest_accepted=view.ingest_accepted,
        ingest_duplicates=view.ingest_duplicates,
        ingest_rejected=view.ingest_rejected,
        ingest_last_error=view.ingest_last_error,
    )
