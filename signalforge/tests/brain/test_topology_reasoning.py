"""Topology versions, deltas, and the CONFIRMED/PREDICTED line (S5-T01, REQ-050).

The most important assertion in this file is that `propagate` cannot be made to
return a confirmed impact the caller did not supply. Everything the walk adds is
predicted, structurally, rather than by a convention somebody remembers.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from signalforge.contracts.canonical import (
    TopologyEdge,
    TopologyEdgeType,
    TopologyNode,
    TopologyNodeType,
    TopologySnapshot,
)
from signalforge.domain.brain.nodes import ConfirmationState
from signalforge.domain.brain.topology import (
    MAX_HOPS,
    diff,
    propagate,
    split,
)


def snapshot(edges, nodes=None, regions=None, version="v1") -> TopologySnapshot:
    names = sorted({name for pair in edges for name in pair} | set(nodes or ()))
    return TopologySnapshot(
        version=version,
        captured_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        nodes=[
            TopologyNode(
                node_id=name,
                name=name,
                node_type=TopologyNodeType.SERVICE,
                region=(regions or {}).get(name, ""),
            )
            for name in names
        ],
        edges=[
            TopologyEdge(
                source=source, target=target, edge_type=TopologyEdgeType.DEPENDS_ON
            )
            for source, target in edges
        ],
    )


# checkout -> pool -> db : checkout depends on pool, pool depends on db
CHAIN = snapshot([("checkout", "pool"), ("pool", "db"), ("mobile", "checkout")])


@pytest.mark.req("REQ-050")
def test_only_the_caller_s_measurements_are_confirmed():
    impacts = propagate(CHAIN, confirmed={"db": "connectivity probe failing"})
    confirmed, predicted = split(impacts)
    assert confirmed == ("db",)
    # Everything the walk reached is predicted, and nothing else is confirmed.
    assert set(predicted) == {"pool", "checkout", "mobile"}
    assert all(
        impact.state is ConfirmationState.PREDICTED
        for impact in impacts
        if impact.service != "db"
    )


@pytest.mark.req("REQ-050")
def test_a_predicted_impact_carries_its_path_and_a_decaying_likelihood():
    impacts = propagate(CHAIN, confirmed={"db": "measured"})
    by_service = {impact.service: impact for impact in impacts}
    assert by_service["pool"].hops == 1
    assert by_service["checkout"].hops == 2
    assert by_service["mobile"].hops == 3
    assert (
        by_service["pool"].likelihood
        > by_service["checkout"].likelihood
        > by_service["mobile"].likelihood
    )
    assert by_service["checkout"].path == ("db", "pool", "checkout")
    assert "nothing has been measured on it" in by_service["checkout"].reason


@pytest.mark.req("REQ-050")
def test_propagation_stops_at_the_hop_limit():
    """A dependency graph in a real estate is connected enough that an unbounded
    walk reaches everything, and "everything is affected" is the same statement
    as "we have no idea"."""
    impacts = propagate(CHAIN, confirmed={"db": "measured"}, max_hops=1)
    _confirmed, predicted = split(impacts)
    assert predicted == ("pool",)
    assert MAX_HOPS >= 1


@pytest.mark.req("REQ-050")
def test_a_service_measured_but_absent_from_the_map_is_still_confirmed():
    """A fault does not stop being observed because the topology document is out
    of date."""
    impacts = propagate(CHAIN, confirmed={"ghost-service": "alerts name it"})
    confirmed, predicted = split(impacts)
    assert "ghost-service" in confirmed
    assert predicted == ()


@pytest.mark.req("REQ-050")
def test_with_no_map_at_all_only_the_measured_services_are_reported():
    impacts = propagate(None, confirmed={"db": "measured"})
    assert split(impacts) == (("db",), ())


@pytest.mark.req("REQ-050")
def test_the_delta_reports_added_and_removed_shape():
    before = snapshot([("checkout", "pool")])
    after = snapshot([("checkout", "pool"), ("pool", "db")])
    delta = diff(before, after, from_version="v1", to_version="v2")
    assert not delta.empty
    assert delta.added_nodes == ("db",)
    assert delta.added_edges == (("pool", "db"),)
    assert delta.removed_nodes == ()
    assert "v1 -> v2" in delta.summary()


@pytest.mark.req("REQ-050")
def test_an_unchanged_map_produces_an_empty_delta():
    """A blast radius that reported the estate rebuilt every time somebody fixed
    a label would be ignored."""
    delta = diff(CHAIN, CHAIN, from_version="v1", to_version="v1")
    assert delta.empty
    assert "no change" in delta.summary()


@pytest.mark.req("REQ-050")
def test_a_node_that_moved_region_is_reported_as_changed():
    before = snapshot([("checkout", "pool")], regions={"pool": "eu"})
    after = snapshot([("checkout", "pool")], regions={"pool": "us"})
    delta = diff(before, after)
    assert delta.changed_nodes == ("pool",)


@pytest.mark.req("REQ-050")
def test_the_first_import_is_reported_as_everything_added():
    delta = diff(None, CHAIN, to_version="v1")
    assert delta.from_version == "(none)"
    assert set(delta.added_nodes) == {"checkout", "pool", "db", "mobile"}
    assert not delta.empty
