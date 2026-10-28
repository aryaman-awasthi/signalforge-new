"""Versioned topology import (REQ-031).

The rule under test is atomicity. A topology is what blast radius is computed
from, and a graph that imported "most of" a document is worse than no graph: it
looks complete and understates impact. So a document with a dangling edge is
refused whole, with the offending edges named.
"""

from __future__ import annotations

import pytest

from signalforge.contracts.errors import AdapterError
from signalforge.tests.adapters.conftest import connect

pytestmark = pytest.mark.anyio

GOOD = {
    "captured_at": "2026-09-01T12:00:00+00:00",
    "nodes": [
        {"node_id": "a", "name": "A", "node_type": "service", "region": "eu-west-1"},
        {"node_id": "b", "name": "B", "node_type": "database"},
    ],
    "edges": [{"source": "a", "target": "b", "edge_type": "depends_on"}],
}


@pytest.fixture
def topo(api, observe):
    signup = api.signup()
    token = signup["tokens"]["access_token"]
    tenant_id = signup["org"]["id"]
    binding = connect(api, token, "topo-import")

    class Topo:
        state = observe.state

        async def invoke(self, capability, *args, **kwargs):
            return await api.container.invoker.invoke(
                tenant_id, binding["id"], capability, *args, **kwargs
            )

        def manifest(self):
            return api.container.adapter_registry.get("topo-import")

    return Topo()


@pytest.mark.req("REQ-031")
async def test_a_pulled_topology_keeps_the_version_the_application_declared(topo):
    snapshot = await topo.invoke("fetch")
    assert snapshot.version == "2026-09-01.1"
    assert {node.node_id for node in snapshot.nodes} == {
        "checkout", "billing", "ledger-db"
    }
    assert len(snapshot.edges) == 2


@pytest.mark.req("REQ-031")
async def test_an_undeclared_version_becomes_a_content_fingerprint(topo):
    snapshot = await topo.invoke("normalize", GOOD)
    assert snapshot.version.startswith("sha256:")

    again = await topo.invoke("normalize", dict(GOOD))
    assert again.version == snapshot.version, (
        "re-importing an unchanged map must not look like a change"
    )


@pytest.mark.req("REQ-031")
async def test_the_fingerprint_ignores_capture_time_and_node_order(topo):
    baseline = await topo.invoke("normalize", GOOD)
    reordered = dict(GOOD)
    reordered["nodes"] = list(reversed(GOOD["nodes"]))
    reordered["captured_at"] = "2026-09-02T09:00:00+00:00"
    assert (await topo.invoke("normalize", reordered)).version == baseline.version


@pytest.mark.req("REQ-031")
async def test_a_changed_graph_changes_the_fingerprint(topo):
    baseline = await topo.invoke("normalize", GOOD)
    changed = dict(GOOD)
    changed["nodes"] = GOOD["nodes"] + [
        {"node_id": "c", "name": "C", "node_type": "cache"}
    ]
    assert (await topo.invoke("normalize", changed)).version != baseline.version


@pytest.mark.req("REQ-031")
async def test_a_dangling_edge_rejects_the_whole_document(topo):
    broken = dict(GOOD)
    broken["edges"] = GOOD["edges"] + [{"source": "a", "target": "ghost"}]
    with pytest.raises(AdapterError) as refused:
        await topo.invoke("normalize", broken)
    assert "a->ghost" in str(refused.value)
    assert "whole or not at all" in str(refused.value)


@pytest.mark.req("REQ-031")
async def test_duplicate_node_ids_and_self_loops_are_refused(topo):
    duplicated = dict(GOOD)
    duplicated["nodes"] = GOOD["nodes"] + [
        {"node_id": "a", "name": "A again", "node_type": "service"}
    ]
    with pytest.raises(AdapterError) as duped:
        await topo.invoke("normalize", duplicated)
    assert "duplicate node ids" in str(duped.value)

    looped = dict(GOOD)
    looped["edges"] = [{"source": "a", "target": "a"}]
    with pytest.raises(AdapterError) as looping:
        await topo.invoke("normalize", looped)
    assert "self-referencing" in str(looping.value)


@pytest.mark.req("REQ-031")
async def test_a_document_that_is_not_a_topology_is_refused_clearly(topo):
    with pytest.raises(AdapterError) as refused:
        await topo.invoke("normalize", {"nodes": "not a list"})
    assert "not valid" in str(refused.value)


@pytest.mark.req("REQ-030", "REQ-031")
async def test_a_graph_over_the_manifest_limit_is_refused_not_truncated(topo):
    topo.manifest().limits.__dict__["max_rows"] = 2
    with pytest.raises(AdapterError) as refused:
        await topo.invoke("normalize", GOOD)
    assert "understate blast radius" in str(refused.value)


@pytest.mark.req("REQ-031")
async def test_an_application_serving_a_broken_topology_does_not_corrupt_the_map(topo):
    """The pull path enforces the same rule as the upload path, from the same
    code: a bad document leaves whatever was there untouched."""
    topo.state.topology = {
        "version": "bad.1",
        "captured_at": "2026-09-01T12:00:00+00:00",
        "nodes": [{"node_id": "only", "name": "Only", "node_type": "service"}],
        "edges": [{"source": "only", "target": "missing"}],
    }
    with pytest.raises(AdapterError) as refused:
        await topo.invoke("fetch")
    assert "only->missing" in str(refused.value)
