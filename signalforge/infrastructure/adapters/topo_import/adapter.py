"""topo-import: the topology adapter, both ways (REQ-031).

One adapter covers the two ways a map arrives - `fetch` pulls
`GET /observe/topology` from an application that publishes one, `normalize`
takes a document an operator uploads in the wizard - because they must produce
the *same* snapshot from the same content. A wizard upload and a scheduled pull
that disagreed about what version the topology is would make "has the map
changed since the incident started" unanswerable.

The import rules live in `domain/integrations/topology.py`: a document imports
whole or not at all, and its version is the document's own when it declares one
and a content fingerprint when it does not.
"""

from __future__ import annotations

from typing import Any

from signalforge.contracts.errors import AdapterError
from signalforge.contracts.observe import OBSERVE_PATHS
from signalforge.contracts.canonical import TopologySnapshot
from signalforge.contracts.spi import TopologyAdapter
from signalforge.domain.integrations.topology import (
    content_fingerprint,
    normalize_document,
)
from signalforge.infrastructure.adapters.base import GenericAdapter


class TopoImportAdapter(GenericAdapter, TopologyAdapter):
    async def ping(self) -> bool:
        return await self.transport().reachable(OBSERVE_PATHS["topology"])

    async def fetch(self) -> TopologySnapshot:
        payload = await self.transport().get_json(OBSERVE_PATHS["topology"])
        if not isinstance(payload, dict):
            raise AdapterError("/observe/topology must return a topology object")
        return self._bounded(normalize_document(payload))

    def normalize(self, raw: dict[str, Any]) -> TopologySnapshot:
        return self._bounded(normalize_document(raw))

    def _bounded(self, snapshot: TopologySnapshot) -> TopologySnapshot:
        """The manifest's max_rows is the graph's size ceiling. Refused rather
        than truncated: half a map understates blast radius while looking
        complete, which is the one failure mode this adapter must not have."""
        size = len(snapshot.nodes) + len(snapshot.edges)
        ceiling = self.spec.limits.max_rows
        if size > ceiling:
            raise AdapterError(
                f"topology has {len(snapshot.nodes)} nodes and {len(snapshot.edges)} "
                f"edges, over this adapter's limit of {ceiling} combined; a partial "
                "import would understate blast radius, so none is performed"
            )
        return snapshot

    def fingerprint(self, snapshot: TopologySnapshot) -> str:
        """The content hash, exposed so a caller can compare two snapshots
        without diffing their graphs."""
        return content_fingerprint(snapshot)
