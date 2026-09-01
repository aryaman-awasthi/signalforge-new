"""Topology versions, what changed between them, and the line between what is
confirmed and what is merely predicted (REQ-050).

Topology versions already exist and are already kept forever
(`domain/onboarding/ports.TopologyVersion`, table `topology_versions`). This
module is the reasoning on top of them, and it is pure: snapshots in, findings
out, no store and no clock.

The distinction the whole module exists to protect:

- **CONFIRMED** - this service is implicated by something measured. An anomaly
  on it, a probe failing against it, an alert naming it, a human confirming it.
- **PREDICTED** - this service depends on something confirmed, so it *may* be
  affected. Nothing has been measured about it at all.

They are computed separately, returned separately, rendered separately and
described separately, because a predicted downstream outage shown like an
observed one is how a war room pages four teams for an outage that never
happened - and how, once that has happened twice, people stop believing the
blast radius at all.

Propagation is walked with a **hop limit** and a decaying likelihood. A
dependency graph in a real estate is connected enough that an unbounded walk
reaches everything, and "everything is predicted to be affected" is the same
statement as "we have no idea".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from signalforge.contracts.canonical import TopologySnapshot
from signalforge.domain.brain.nodes import ConfirmationState

MAX_HOPS = 3
"""How far propagation is predicted. Three hops is where "this could plausibly
be affected" stops being a useful sentence in a war room."""

HOP_DECAY = 0.55
"""How much likelihood one hop costs. A direct dependent of a confirmed fault is
plausible; something three hops out, behind two caches and a retry policy,
mostly is not."""

MAX_PREDICTED = 40


@dataclass(frozen=True)
class ServiceImpact:
    """One service and why it is in the blast radius.

    `state` is the field every reader must respect, and `reason` is the sentence
    that justifies it. `path` is how the walk got here - the dependency chain -
    which is what makes a predicted impact arguable rather than an assertion.
    """

    service: str
    state: ConfirmationState
    reason: str
    hops: int = 0
    likelihood: float = 1.0
    path: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    region: str = ""

    @property
    def confirmed(self) -> bool:
        return self.state.observed


@dataclass(frozen=True)
class TopologyDelta:
    """What changed between two versions of the map.

    Asked at exactly one moment and it is a useful one: "has anything about the
    shape of this estate changed since the incident started?" A dependency added
    an hour before an incident is a change worth correlating, and a map that was
    silently replaced mid-incident explains a blast radius that stopped making
    sense.
    """

    from_version: str
    to_version: str
    added_nodes: tuple[str, ...] = ()
    removed_nodes: tuple[str, ...] = ()
    added_edges: tuple[tuple[str, str], ...] = ()
    removed_edges: tuple[tuple[str, str], ...] = ()
    changed_nodes: tuple[str, ...] = ()
    """Same id, different type, region or name. A node that moved region between
    versions is not the same node for blast-radius purposes."""

    @property
    def empty(self) -> bool:
        return not (
            self.added_nodes
            or self.removed_nodes
            or self.added_edges
            or self.removed_edges
            or self.changed_nodes
        )

    def summary(self) -> str:
        if self.empty:
            return f"no change between {self.from_version} and {self.to_version}"
        parts = []
        if self.added_nodes:
            parts.append(f"+{len(self.added_nodes)} service(s)")
        if self.removed_nodes:
            parts.append(f"-{len(self.removed_nodes)} service(s)")
        if self.added_edges:
            parts.append(f"+{len(self.added_edges)} dependency(ies)")
        if self.removed_edges:
            parts.append(f"-{len(self.removed_edges)} dependency(ies)")
        if self.changed_nodes:
            parts.append(f"{len(self.changed_nodes)} redefined")
        return f"{self.from_version} -> {self.to_version}: " + ", ".join(parts)


def diff(previous: Optional[TopologySnapshot], current: TopologySnapshot,
         *, from_version: str = "", to_version: str = "") -> TopologyDelta:
    """Two maps compared by node id and edge pair.

    Compared on ids rather than on names because a rename is not a change of
    shape, and a blast radius that reported a whole estate rebuilt every time
    somebody fixed a label would be ignored.
    """
    if previous is None:
        return TopologyDelta(
            from_version=from_version or "(none)",
            to_version=to_version or "current",
            added_nodes=tuple(sorted(node.node_id for node in current.nodes)),
            added_edges=tuple(
                sorted((edge.source, edge.target) for edge in current.edges)
            ),
        )
    before = {node.node_id: node for node in previous.nodes}
    after = {node.node_id: node for node in current.nodes}
    before_edges = {(edge.source, edge.target) for edge in previous.edges}
    after_edges = {(edge.source, edge.target) for edge in current.edges}
    changed = tuple(
        sorted(
            node_id
            for node_id, node in after.items()
            if node_id in before
            and (
                before[node_id].node_type != node.node_type
                or (before[node_id].region or "") != (node.region or "")
                or (before[node_id].name or "") != (node.name or "")
            )
        )
    )
    return TopologyDelta(
        from_version=from_version or "(previous)",
        to_version=to_version or "current",
        added_nodes=tuple(sorted(set(after) - set(before))),
        removed_nodes=tuple(sorted(set(before) - set(after))),
        added_edges=tuple(sorted(after_edges - before_edges)),
        removed_edges=tuple(sorted(before_edges - after_edges)),
        changed_nodes=changed,
    )


def propagate(
    snapshot: Optional[TopologySnapshot],
    *,
    confirmed: Mapping[str, str],
    max_hops: int = MAX_HOPS,
    max_predicted: int = MAX_PREDICTED,
    evidence_refs: Optional[Mapping[str, Sequence[str]]] = None,
) -> tuple[ServiceImpact, ...]:
    """Confirmed services first, then what depends on them, with the hop count.

    `confirmed` maps a node id or service name to the reason it is confirmed -
    the caller's measurement, never this function's inference. Everything this
    function adds is PREDICTED, without exception, which is what makes the
    separation structural rather than a convention somebody has to remember.

    Edge direction is read the way the canonical model declares it, `source
    depends on target`, so dependents are found by walking edges *backwards*
    from a broken node to the things that named it - the same reading
    `domain/warroom/blast.downstream_services` uses, deliberately, so the two
    cannot drift.
    """
    refs = {key: tuple(value) for key, value in (evidence_refs or {}).items()}
    if snapshot is None or not snapshot.nodes:
        return tuple(
            ServiceImpact(
                service=name,
                state=ConfirmationState.CONFIRMED,
                reason=reason,
                evidence_refs=refs.get(name, ()),
            )
            for name, reason in sorted(confirmed.items())
        )

    names = {node.node_id: node.name or node.node_id for node in snapshot.nodes}
    regions = {node.node_id: node.region or "" for node in snapshot.nodes}
    by_name: dict[str, str] = {}
    for node_id, name in names.items():
        by_name.setdefault(name.lower(), node_id)
        by_name.setdefault(node_id.lower(), node_id)

    dependents: dict[str, set[str]] = {}
    for edge in snapshot.edges:
        dependents.setdefault(edge.target, set()).add(edge.source)

    impacts: list[ServiceImpact] = []
    seeds: dict[str, str] = {}
    for key, reason in confirmed.items():
        node_id = by_name.get(str(key).strip().lower())
        if node_id is None:
            # Confirmed by measurement but absent from the map. Still confirmed -
            # a fault does not stop being observed because the topology document
            # is out of date - and reported without a path.
            impacts.append(
                ServiceImpact(
                    service=str(key),
                    state=ConfirmationState.CONFIRMED,
                    reason=reason,
                    evidence_refs=refs.get(str(key), ()),
                )
            )
            continue
        seeds[node_id] = reason

    for node_id, reason in sorted(seeds.items()):
        impacts.append(
            ServiceImpact(
                service=names[node_id],
                state=ConfirmationState.CONFIRMED,
                reason=reason,
                region=regions.get(node_id, ""),
                path=(names[node_id],),
                evidence_refs=refs.get(names[node_id], refs.get(node_id, ())),
            )
        )

    seen = set(seeds)
    frontier: list[tuple[str, int, tuple[str, ...]]] = [
        (node_id, 0, (names[node_id],)) for node_id in sorted(seeds)
    ]
    predicted: list[ServiceImpact] = []
    while frontier:
        node_id, hops, path = frontier.pop(0)
        if hops >= max_hops:
            continue
        for dependent in sorted(dependents.get(node_id, ())):
            if dependent in seen:
                continue
            seen.add(dependent)
            step = hops + 1
            predicted.append(
                ServiceImpact(
                    service=names.get(dependent, dependent),
                    state=ConfirmationState.PREDICTED,
                    reason=(
                        f"depends on {path[-1]}, {step} hop(s) from a confirmed "
                        "fault; nothing has been measured on it"
                    ),
                    hops=step,
                    likelihood=round(HOP_DECAY**step, 3),
                    path=path + (names.get(dependent, dependent),),
                    region=regions.get(dependent, ""),
                )
            )
            frontier.append((dependent, step, path + (names.get(dependent, dependent),)))

    predicted.sort(key=lambda impact: (impact.hops, impact.service))
    return tuple(impacts) + tuple(predicted[:max_predicted])


def split(impacts: Sequence[ServiceImpact]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(confirmed, predicted) service names. The two lists a screen renders under
    two different legends, never merged into one."""
    return (
        tuple(impact.service for impact in impacts if impact.confirmed),
        tuple(impact.service for impact in impacts if not impact.confirmed),
    )


@dataclass(frozen=True)
class DependencyPath:
    """One route from a confirmed fault to something that depends on it.

    What the topology agent offers as evidence for a hypothesis: not "these
    services are affected" but "checkout reaches the database through the
    connection pool, and the pool is the confirmed fault".
    """

    hops: tuple[str, ...]
    likelihood: float = 1.0
    note: str = ""

    def __str__(self) -> str:
        return " -> ".join(self.hops)


def paths_to(
    impacts: Sequence[ServiceImpact], *, limit: int = 8
) -> tuple[DependencyPath, ...]:
    return tuple(
        DependencyPath(
            hops=impact.path,
            likelihood=impact.likelihood,
            note=impact.reason,
        )
        for impact in impacts
        if len(impact.path) > 1
    )[:limit]


@dataclass(frozen=True)
class TopologyView:
    """Everything S13 renders: the map, which version it is, what changed since
    the incident opened, and the two impact lists kept apart."""

    app_id: str
    version: str
    imported_at: str = ""
    node_count: int = 0
    edge_count: int = 0
    versions: tuple[str, ...] = ()
    delta: Optional[TopologyDelta] = None
    impacts: tuple[ServiceImpact, ...] = ()
    nodes: tuple[Mapping[str, str], ...] = ()
    edges: tuple[Mapping[str, str], ...] = ()
    detail: Mapping[str, str] = field(default_factory=dict)

    @property
    def confirmed(self) -> tuple[str, ...]:
        return split(self.impacts)[0]

    @property
    def predicted(self) -> tuple[str, ...]:
        return split(self.impacts)[1]
