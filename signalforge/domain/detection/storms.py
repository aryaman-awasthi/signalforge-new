"""Alert-storm clustering by time and topology (REQ-042).

One fault produces many alerts. A database losing its connection pool fires on
the database, on every service that calls it, on the queue that backs up behind
them and on the synthetic checks in front of all of it - and a responder handed
forty rows spends the first ten minutes working out that they are one row.

Clustering here is deliberately mechanical and explainable, because a responder
has to be able to disagree with it:

- **Time.** Alerts more than `STORM_WINDOW_S` apart are not the same event. Two
  minutes is wide enough to span a propagation delay and narrow enough that the
  next unrelated fault starts its own cluster.
- **Topology.** Two alerts join only if the nodes they name are within
  `STORM_TOPOLOGY_HOPS` of each other in the application's own map. Adjacency is
  read undirected because a fault propagates the way the dependency points and
  the alert arrives the way the call goes.
- **Neither of those is a guess.** An alert whose labels name no node in the map
  clusters only with alerts of the *same name* inside the window. It would be
  easy to sweep the unmapped ones into whatever cluster is nearby; that is how a
  second, unrelated fault gets suppressed during an incident.

The **root** is the alert on the most depended-upon node in the cluster - the
one the largest number of other members' nodes point at. `A depends_on B` means
a failure of B explains A, so the node with the most incoming edges from its
cluster-mates is the one worth reading first. Ties break by earliest start, then
by alert id, so the same storm always names the same root.

Children are **suppressed, with a visible count**. Nothing is deleted: the
cluster carries every member id, and `suppressed_count` is how many rows the
screen is folding away.
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from signalforge.contracts.canonical import (
    Alert,
    AlertSeverity,
    AlertStatus,
    TopologySnapshot,
)

STORM_WINDOW_S = 120
STORM_TOPOLOGY_HOPS = 2
MAX_CLUSTERED_ALERTS = 500
"""A bound on the pairwise pass. Five hundred firing alerts inside two minutes
is already a storm by any definition; clustering the newest five hundred and
saying so beats an unbounded quadratic loop during an incident."""

NODE_LABELS: tuple[str, ...] = (
    "node_id",
    "service",
    "component",
    "instance",
    "target",
    "job",
)
"""The label names an alert may name a topology node with, in the order they are
tried. Matched against node ids first and node names second, both
case-insensitively - a monitoring system labels by whatever its operator typed."""

_SEVERITY_RANK = {
    AlertSeverity.CRITICAL: 0,
    AlertSeverity.WARNING: 1,
    AlertSeverity.INFO: 2,
}


@dataclass(frozen=True)
class AlertCluster:
    cluster_id: str
    root_alert_id: str
    root_node_id: Optional[str]
    alert_ids: tuple[str, ...]
    node_ids: tuple[str, ...]
    severity: AlertSeverity
    started_at: datetime
    latest_at: datetime
    names: tuple[str, ...]
    detail: str = ""

    @property
    def size(self) -> int:
        return len(self.alert_ids)

    @property
    def suppressed_count(self) -> int:
        """How many rows the cluster folds away. Never negative, never a lie: it
        is exactly `size - 1`, and every id is still listed."""
        return max(0, self.size - 1)

    @property
    def evidence_ref(self) -> str:
        return f"alert-cluster:{self.cluster_id}"


@dataclass(frozen=True)
class StormSummary:
    clusters: tuple[AlertCluster, ...]
    alerts: int
    suppressed: int
    window_s: int

    @property
    def storming(self) -> bool:
        """A storm is a cluster that suppressed something. One alert on its own
        is an alert."""
        return self.suppressed > 0


def _node_index(snapshot: Optional[TopologySnapshot]) -> dict[str, str]:
    index: dict[str, str] = {}
    if snapshot is None:
        return index
    for node in snapshot.nodes:
        index[node.node_id.strip().lower()] = node.node_id
        if node.name:
            index.setdefault(node.name.strip().lower(), node.node_id)
    return index


def _adjacency(snapshot: Optional[TopologySnapshot]) -> dict[str, set[str]]:
    edges: dict[str, set[str]] = {}
    if snapshot is None:
        return edges
    for edge in snapshot.edges:
        edges.setdefault(edge.source, set()).add(edge.target)
        edges.setdefault(edge.target, set()).add(edge.source)
    return edges


def resolve_node(alert: Alert, index: dict[str, str]) -> Optional[str]:
    for label in NODE_LABELS:
        value = (alert.labels.get(label) or "").strip().lower()
        if value and value in index:
            return index[value]
    return None


def _within_hops(
    adjacency: dict[str, set[str]], source: str, target: str, hops: int
) -> bool:
    if source == target:
        return True
    seen = {source}
    queue: deque[tuple[str, int]] = deque([(source, 0)])
    while queue:
        node, depth = queue.popleft()
        if depth >= hops:
            continue
        for neighbour in adjacency.get(node, ()):
            if neighbour == target:
                return True
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append((neighbour, depth + 1))
    return False


def _cluster_id(alert_ids: Sequence[str]) -> str:
    digest = hashlib.sha256("\n".join(sorted(alert_ids)).encode("utf-8")).hexdigest()
    return digest[:16]


def cluster_alerts(
    alerts: Sequence[Alert],
    snapshot: Optional[TopologySnapshot] = None,
    *,
    window_s: int = STORM_WINDOW_S,
    hops: int = STORM_TOPOLOGY_HOPS,
) -> StormSummary:
    """Group firing alerts into clusters. Pure, and stable for a given input.

    Resolved alerts are excluded: a storm is about what is on fire now, and a
    resolution arriving inside the window is the opposite of evidence.
    """
    firing = sorted(
        (alert for alert in alerts if alert.status is AlertStatus.FIRING),
        key=lambda alert: (alert.starts_at, alert.alert_id),
    )[-MAX_CLUSTERED_ALERTS:]
    index = _node_index(snapshot)
    adjacency = _adjacency(snapshot)
    nodes = {alert.alert_id: resolve_node(alert, index) for alert in firing}

    parent: dict[str, str] = {alert.alert_id: alert.alert_id for alert in firing}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            # Lowest id wins so the disjoint sets are deterministic.
            parent[max(a, b)] = min(a, b)

    for i, left in enumerate(firing):
        for right in firing[i + 1:]:
            gap = abs((right.starts_at - left.starts_at).total_seconds())
            if gap > window_s:
                # Sorted by time, so nothing further right can be closer.
                break
            left_node, right_node = nodes[left.alert_id], nodes[right.alert_id]
            if left_node and right_node:
                related = _within_hops(adjacency, left_node, right_node, hops)
            else:
                related = left.name == right.name
            if related:
                union(left.alert_id, right.alert_id)

    groups: dict[str, list[Alert]] = {}
    for alert in firing:
        groups.setdefault(find(alert.alert_id), []).append(alert)

    clusters = tuple(
        sorted(
            (_build(members, nodes, adjacency) for members in groups.values()),
            key=lambda cluster: (cluster.started_at, cluster.root_alert_id),
        )
    )
    return StormSummary(
        clusters=clusters,
        alerts=len(firing),
        suppressed=sum(cluster.suppressed_count for cluster in clusters),
        window_s=window_s,
    )


def _build(
    members: Sequence[Alert],
    nodes: dict[str, Optional[str]],
    adjacency: dict[str, set[str]],
) -> AlertCluster:
    member_nodes = {
        alert.alert_id: nodes[alert.alert_id]
        for alert in members
        if nodes[alert.alert_id]
    }
    node_set = set(member_nodes.values())

    def depended_on_by(alert: Alert) -> int:
        node = nodes[alert.alert_id]
        if node is None:
            return -1
        return len(adjacency.get(node, set()) & (node_set - {node}))

    root = max(
        members,
        key=lambda alert: (
            depended_on_by(alert),
            -alert.starts_at.timestamp(),
            _negated(alert.alert_id),
        ),
    )
    severity = min(members, key=lambda alert: _SEVERITY_RANK[alert.severity]).severity
    alert_ids = tuple(sorted(alert.alert_id for alert in members))
    suppressed = len(alert_ids) - 1
    return AlertCluster(
        cluster_id=_cluster_id(alert_ids),
        root_alert_id=root.alert_id,
        root_node_id=nodes[root.alert_id],
        alert_ids=alert_ids,
        node_ids=tuple(sorted(node_set)),
        severity=severity,
        started_at=min(alert.starts_at for alert in members),
        latest_at=max(alert.starts_at for alert in members),
        names=tuple(sorted({alert.name for alert in members})),
        detail=(
            f"{len(alert_ids)} alert(s) within the window across "
            f"{len(node_set) or 'no mapped'} node(s); {suppressed} suppressed under "
            f"{root.name!r}"
        ),
    )


class _negated:
    """Reverse string ordering inside a max() key, so ties break on the *lowest*
    alert id without sorting the whole list twice."""

    __slots__ = ("value",)

    def __init__(self, value: str):
        self.value = value

    def __lt__(self, other: "_negated") -> bool:
        return self.value > other.value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _negated) and self.value == other.value
