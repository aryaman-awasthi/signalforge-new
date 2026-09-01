"""Topology import rules (REQ-031).

A topology document is the map SignalForge reasons about blast radius with, so
two rules apply to importing one and both are absolute:

1. **It imports whole or not at all.** A graph with half its edges is worse than
   no graph: it looks complete and understates impact. A document naming an edge
   endpoint that is not a node is rejected with the offending edges named, rather
   than silently dropping them.
2. **Every import is versioned by its content.** The version an operator sees is
   the document's own when it declares one, and a content hash when it does not.
   Re-importing an unchanged document therefore produces the same version, which
   is what makes "has the topology changed since the incident started" answerable
   without diffing the whole graph.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from signalforge.contracts.canonical import TopologySnapshot
from signalforge.contracts.errors import AdapterError

CONTENT_VERSION_PREFIX = "sha256:"


def content_fingerprint(snapshot: TopologySnapshot) -> str:
    """Stable hash of the graph itself - nodes and edges, order-independent,
    excluding `version` and `captured_at` so that re-capturing an unchanged
    estate does not look like a change."""
    nodes = sorted(
        json.dumps(
            {
                "node_id": node.node_id,
                "name": node.name,
                "node_type": node.node_type.value,
                "region": node.region,
                "labels": dict(sorted(node.labels.items())),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        for node in snapshot.nodes
    )
    edges = sorted(
        json.dumps(
            {
                "source": edge.source,
                "target": edge.target,
                "edge_type": edge.edge_type.value,
                "labels": dict(sorted(edge.labels.items())),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        for edge in snapshot.edges
    )
    digest = hashlib.sha256(
        json.dumps({"nodes": nodes, "edges": edges}, separators=(",", ":")).encode()
    ).hexdigest()
    return CONTENT_VERSION_PREFIX + digest[:16]


def assert_consistent(snapshot: TopologySnapshot) -> None:
    """Refuse a graph that cannot be walked. Raised before anything is stored,
    so a bad document leaves the previous topology untouched."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for node in snapshot.nodes:
        if node.node_id in seen:
            duplicates.add(node.node_id)
        seen.add(node.node_id)
    if duplicates:
        raise AdapterError(f"topology declares duplicate node ids {sorted(duplicates)}")
    if not snapshot.nodes and snapshot.edges:
        raise AdapterError("topology declares edges but no nodes")
    dangling = sorted(
        {
            f"{edge.source}->{edge.target}"
            for edge in snapshot.edges
            if edge.source not in seen or edge.target not in seen
        }
    )
    if dangling:
        raise AdapterError(
            "topology edges name nodes that are not declared: "
            f"{dangling[:5]}{'...' if len(dangling) > 5 else ''}; "
            "the document is imported whole or not at all"
        )
    self_loops = sorted(
        {edge.source for edge in snapshot.edges if edge.source == edge.target}
    )
    if self_loops:
        raise AdapterError(f"topology declares self-referencing edges on {self_loops}")


def normalize_document(raw: Mapping[str, Any]) -> TopologySnapshot:
    """Parse, validate and version an uploaded or fetched topology document.

    Pure: the same document always yields the same snapshot, including its
    version, which is what the wizard's upload step and the pull adapter both
    rely on to recognise "this is what we already have".
    """
    if not isinstance(raw, Mapping):
        raise AdapterError("a topology document must be an object")
    document = dict(raw)
    declared_version = str(document.get("version") or "").strip()
    document["version"] = declared_version or "pending"
    try:
        snapshot = TopologySnapshot.model_validate(document)
    except Exception as exc:  # pydantic validation, translated to the taxonomy
        raise AdapterError(f"topology document is not valid: {exc}") from exc
    assert_consistent(snapshot)
    if not declared_version:
        snapshot = snapshot.model_copy(
            update={"version": content_fingerprint(snapshot)}
        )
    return snapshot
