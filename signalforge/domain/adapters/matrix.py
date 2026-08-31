"""The capability matrix (REQ-030).

One table answering the question an onboarding operator actually has: for this
kind of signal, which installed adapters can give it to me, and what does each
one leave out. It is computed from the manifests and the SPI, never authored, so
it cannot claim a capability nobody implemented.

Rows are adapters, columns are the capabilities of their kind, and the same data
is also grouped by kind so the wizard can ask "who can do metrics here" without
scanning every row.

This is a view of what the installed adapter *set* declares, which is why
coverage is computed from `availability` and not from whether a runtime happens
to be registered on the node answering. Interface-only entries (Availability.
AVAILABLE) are listed but never counted as coverage - claiming an integration
because its interface exists is exactly the lie this table is meant to prevent.
A `built_in` manifest whose runtime is missing is a packaging fault rather than
a coverage gap, and the registry listing shows it as "runtime missing".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from signalforge.contracts.envelope import AdapterKind
from signalforge.domain.adapters.capabilities import capabilities_for, is_mutating
from signalforge.domain.adapters.manifest import AdapterManifest, Availability


@dataclass(frozen=True)
class CapabilityCell:
    capability: str
    supported: bool
    mutating: bool


@dataclass(frozen=True)
class CapabilityRow:
    adapter_id: str
    name: str
    kind: AdapterKind
    mode: str
    availability: Availability
    simulated: bool
    credential_scopes: tuple[str, ...]
    cells: tuple[CapabilityCell, ...]

    @property
    def supported(self) -> tuple[str, ...]:
        return tuple(cell.capability for cell in self.cells if cell.supported)

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(cell.capability for cell in self.cells if not cell.supported)


@dataclass(frozen=True)
class KindCoverage:
    kind: AdapterKind
    capabilities: tuple[str, ...]
    adapters: tuple[str, ...]
    covered: tuple[str, ...]
    uncovered: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """True when some installed adapter covers every capability of the kind
        - not necessarily the same one."""
        return not self.uncovered


@dataclass(frozen=True)
class CapabilityMatrix:
    rows: tuple[CapabilityRow, ...]
    coverage: tuple[KindCoverage, ...]

    def row(self, adapter_id: str) -> Optional[CapabilityRow]:
        return next((r for r in self.rows if r.adapter_id == adapter_id), None)

    def for_kind(self, kind: AdapterKind) -> Optional[KindCoverage]:
        return next((c for c in self.coverage if c.kind is kind), None)


def _row(manifest: AdapterManifest) -> CapabilityRow:
    return CapabilityRow(
        adapter_id=manifest.id,
        name=manifest.name,
        kind=manifest.kind,
        mode=manifest.mode.value,
        availability=manifest.availability,
        simulated=manifest.simulated,
        credential_scopes=tuple(s.value for s in manifest.credential_scopes),
        cells=tuple(
            CapabilityCell(
                capability=capability,
                supported=manifest.supports(capability),
                mutating=is_mutating(manifest.kind, capability),
            )
            for capability in sorted(capabilities_for(manifest.kind))
        ),
    )


def capability_matrix(manifests: Sequence[AdapterManifest]) -> CapabilityMatrix:
    by_kind: dict[AdapterKind, list[AdapterManifest]] = {}
    for manifest in manifests:
        by_kind.setdefault(manifest.kind, []).append(manifest)

    coverage: list[KindCoverage] = []
    for kind in AdapterKind:
        capabilities = tuple(sorted(capabilities_for(kind)))
        installed = by_kind.get(kind, [])
        # A capability counts as covered only by an adapter this deployment can
        # actually run: an interface-only entry is visible in the registry, but
        # it is not coverage.
        runnable = [m for m in installed if m.availability is Availability.BUILT_IN]
        covered = tuple(
            capability
            for capability in capabilities
            if any(m.supports(capability) for m in runnable)
        )
        coverage.append(
            KindCoverage(
                kind=kind,
                capabilities=capabilities,
                adapters=tuple(sorted(m.id for m in installed)),
                covered=covered,
                uncovered=tuple(c for c in capabilities if c not in covered),
            )
        )

    return CapabilityMatrix(
        rows=tuple(_row(m) for m in sorted(manifests, key=lambda m: (m.kind.value, m.id))),
        coverage=tuple(coverage),
    )
