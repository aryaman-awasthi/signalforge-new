"""Alert-storm clustering and the severity proposal (REQ-042, REQ-046).

Pure domain, because both are rules a responder has to be able to disagree with:
a cluster that cannot be explained is a cluster that gets ignored, and a severity
nobody can trace back to an observation is a number nobody trusts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from signalforge.contracts.canonical import (
    Alert,
    AlertSeverity,
    AlertStatus,
    TopologyEdge,
    TopologyNode,
    TopologySnapshot,
)
from signalforge.domain.detection.anomaly import SignalState, SignalStatus
from signalforge.domain.detection.corroboration import corroborate
from signalforge.domain.detection.probes import JourneyHealth
from signalforge.domain.detection.severity import assess_impact, propose_severity
from signalforge.domain.detection.storms import STORM_WINDOW_S, cluster_alerts
from signalforge.domain.onboarding.profile import (
    GoldenSignal,
    Journey,
    Severity,
    SignalDirection,
    default_severity_matrix,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

TOPOLOGY = TopologySnapshot(
    version="v1",
    captured_at=NOW,
    nodes=[
        TopologyNode(node_id="gateway", name="Gateway", node_type="service"),
        TopologyNode(node_id="checkout", name="Checkout", node_type="service"),
        TopologyNode(node_id="billing", name="Billing", node_type="service"),
        TopologyNode(node_id="ledger-db", name="Ledger", node_type="database"),
        TopologyNode(node_id="reporting", name="Reporting", node_type="service"),
    ],
    edges=[
        TopologyEdge(source="gateway", target="checkout", edge_type="calls"),
        TopologyEdge(source="checkout", target="billing", edge_type="calls"),
        TopologyEdge(source="billing", target="ledger-db", edge_type="depends_on"),
    ],
)


def alert(
    alert_id: str,
    node: str | None,
    *,
    offset_s: float = 0.0,
    name: str = "HighErrorRate",
    severity: AlertSeverity = AlertSeverity.WARNING,
    status: AlertStatus = AlertStatus.FIRING,
) -> Alert:
    return Alert(
        alert_id=alert_id,
        name=name,
        severity=severity,
        status=status,
        starts_at=NOW + timedelta(seconds=offset_s),
        labels={"service": node} if node else {},
    )


# --- storms -------------------------------------------------------------------


@pytest.mark.req("REQ-042")
def test_one_fault_across_adjacent_services_is_one_cluster_with_a_suppressed_count():
    summary = cluster_alerts(
        [
            alert("a1", "ledger-db", offset_s=0, severity=AlertSeverity.CRITICAL),
            alert("a2", "billing", offset_s=10),
            alert("a3", "checkout", offset_s=20),
        ],
        TOPOLOGY,
    )
    assert len(summary.clusters) == 1
    cluster = summary.clusters[0]
    assert cluster.size == 3
    assert cluster.suppressed_count == 2
    assert summary.suppressed == 2 and summary.storming
    assert set(cluster.alert_ids) == {"a1", "a2", "a3"}, "nothing is deleted"
    assert cluster.severity is AlertSeverity.CRITICAL, "the worst member speaks"


@pytest.mark.req("REQ-042")
def test_the_root_is_the_most_depended_on_node_in_the_cluster():
    """`A depends_on B` means a failure of B explains A, so the node the most
    cluster-mates point at is the one worth reading first."""
    summary = cluster_alerts(
        [
            alert("a-checkout", "checkout", offset_s=0),
            alert("a-billing", "billing", offset_s=5),
            alert("a-ledger", "ledger-db", offset_s=10),
        ],
        TOPOLOGY,
    )
    cluster = summary.clusters[0]
    assert cluster.root_node_id == "billing"
    assert cluster.root_alert_id == "a-billing"


@pytest.mark.req("REQ-042")
def test_a_node_more_than_two_hops_away_is_a_different_incident():
    summary = cluster_alerts(
        [alert("a1", "ledger-db"), alert("a2", "gateway", offset_s=5)],
        TOPOLOGY,
    )
    assert len(summary.clusters) == 2, "three hops apart is not one fault"
    assert summary.suppressed == 0 and not summary.storming


@pytest.mark.req("REQ-042")
def test_alerts_outside_the_time_window_are_separate_clusters():
    summary = cluster_alerts(
        [
            alert("a1", "billing", offset_s=0),
            alert("a2", "checkout", offset_s=STORM_WINDOW_S + 30),
        ],
        TOPOLOGY,
    )
    assert len(summary.clusters) == 2


@pytest.mark.req("REQ-042")
def test_an_alert_naming_no_mapped_node_clusters_only_by_alert_name():
    """It would be easy to sweep the unmapped ones into whatever cluster is
    nearby. That is how a second, unrelated fault gets suppressed during an
    incident. The only justification an unmapped alert has is that it is the
    *same alert rule* firing inside the window."""
    summary = cluster_alerts(
        [
            alert("known", "billing", offset_s=0, name="HighErrorRate"),
            alert("same-name", None, offset_s=5, name="HighErrorRate"),
            alert("other-name", None, offset_s=6, name="DiskFilling"),
        ],
        TOPOLOGY,
    )
    grouped = {
        frozenset(cluster.alert_ids): cluster.size for cluster in summary.clusters
    }
    assert grouped == {
        frozenset({"known", "same-name"}): 2,
        frozenset({"other-name"}): 1,
    }, "a differently named alert on no known node joins nothing"

    paired = cluster_alerts(
        [
            alert("u1", None, offset_s=0, name="DiskFilling"),
            alert("u2", None, offset_s=5, name="DiskFilling"),
        ],
        TOPOLOGY,
    )
    assert len(paired.clusters) == 1 and paired.suppressed == 1


@pytest.mark.req("REQ-042")
def test_a_resolved_alert_is_not_part_of_a_storm():
    summary = cluster_alerts(
        [
            alert("a1", "billing"),
            alert("a2", "checkout", offset_s=5, status=AlertStatus.RESOLVED),
        ],
        TOPOLOGY,
    )
    assert summary.alerts == 1
    assert summary.clusters[0].alert_ids == ("a1",)


@pytest.mark.req("REQ-042")
def test_clustering_is_stable_for_the_same_input():
    """The same storm always names the same root and the same cluster id, or a
    screen that re-renders every five seconds flickers between explanations."""
    alerts = [
        alert("a1", "ledger-db"),
        alert("a2", "billing", offset_s=5),
        alert("a3", "checkout", offset_s=6),
    ]
    first = cluster_alerts(alerts, TOPOLOGY)
    second = cluster_alerts(list(reversed(alerts)), TOPOLOGY)
    assert [c.cluster_id for c in first.clusters] == [
        c.cluster_id for c in second.clusters
    ]
    assert first.clusters[0].root_alert_id == second.clusters[0].root_alert_id


# --- severity -----------------------------------------------------------------


def _state(
    signal_key: str, family: GoldenSignal, region: str, value: float
) -> SignalState:
    return SignalState(
        tenant_id="t",
        app_id="payments",
        signal_key=signal_key,
        region=region,
        family=family,
        status=SignalStatus.ANOMALOUS,
        streak=3,
        value=value,
        threshold=0.9,
        baseline_mean=0.99,
        deviation=4.0,
        last_value_at=NOW,
        evaluated_at=NOW,
    )


JOURNEYS = (Journey(journey_id="payments", name="Payments", tier=1),)
UNITS = {"payments-success": ("ratio", SignalDirection.LOWER_IS_WORSE)}


def _propose(states, journeys, regions=("region-a", "region-b")):
    impact = assess_impact(
        states,
        journeys,
        [],
        declared_journeys=JOURNEYS,
        declared_regions=regions,
        signal_units=UNITS,
    )
    return impact, propose_severity(
        default_severity_matrix(), impact, corroborate(states, [], journeys, at=NOW), at=NOW
    )


@pytest.mark.req("REQ-046")
def test_a_tier_one_journey_failing_badly_in_every_region_proposes_p0():
    states = [
        _state("payments-success", GoldenSignal.BUSINESS, "region-a", 0.4),
        _state("payments-success", GoldenSignal.BUSINESS, "region-b", 0.5),
        _state("errors", GoldenSignal.ERRORS, "region-a", 0.6),
    ]
    journeys = [
        JourneyHealth(
            journey_id="payments", region=region, passed=0, failed=3, unknown=0, tier=1,
            last_at=NOW, last_detail="503",
        )
        for region in ("region-a", "region-b")
    ]
    impact, proposal = _propose(states, journeys)

    assert impact.journey_tier == 1
    assert impact.regions_affected == 2 and impact.regions_total == 2
    assert impact.success_rate_pct == pytest.approx(0.0)
    assert impact.customer_impacting
    assert proposal.severity is Severity.P0
    assert "tier-1" in proposal.rule
    assert proposal.corroborated


@pytest.mark.req("REQ-046")
def test_one_region_below_target_proposes_p1():
    states = [_state("payments-success", GoldenSignal.BUSINESS, "region-b", 0.90)]
    journeys = [
        JourneyHealth(
            journey_id="payments", region="region-b", passed=9, failed=1, unknown=0,
            tier=1, last_at=NOW, last_detail="one 503",
        )
    ]
    _impact, proposal = _propose(states, journeys)
    assert proposal.severity is Severity.P1


@pytest.mark.req("REQ-046")
def test_no_customer_evidence_proposes_p2():
    states = [_state("cpu", GoldenSignal.SATURATION, "region-a", 0.99)]
    _impact, proposal = _propose(states, [])
    assert proposal.severity is Severity.P2
    assert not proposal.corroborated
    assert "no customer-facing evidence" in proposal.impact.detail


@pytest.mark.req("REQ-046", "REQ-043")
def test_a_proposal_declares_nothing():
    """There is no state transition in the severity module and no way to make
    one. Declaring is a human act (REQ-043)."""
    states = [_state("payments-success", GoldenSignal.BUSINESS, "region-a", 0.4)]
    _impact, proposal = _propose(states, [])
    assert proposal.requires_human is True
    with pytest.raises(ValueError, match="requires a human"):
        type(proposal)(
            severity=proposal.severity,
            impact=proposal.impact,
            rule=proposal.rule,
            corroborated=proposal.corroborated,
            proposed_at=NOW,
            requires_human=False,
        )


@pytest.mark.req("REQ-046")
def test_an_unrun_probe_never_counts_toward_impact():
    """'We could not check' and 'the journey is broken' lead to opposite next
    actions."""
    journeys = [
        JourneyHealth(
            journey_id="payments", region="region-b", passed=0, failed=0, unknown=6,
            tier=1, last_at=NOW, last_detail="no health connector",
        )
    ]
    impact, proposal = _propose([], journeys)
    assert impact.customer_impacting is False
    assert impact.success_rate_pct is None
    assert proposal.severity is Severity.P2


@pytest.mark.req("REQ-042")
def test_the_root_is_chosen_by_who_depends_on_it_not_by_how_many_edges_it_has():
    """Written failing-first. The root was picked by undirected degree, so a
    gateway that *calls* three services - which nothing depends on - outranked
    the database two of them sit on. `source depends_on target`: the root is
    the node the most cluster-mates point at."""
    hub = TopologySnapshot(
        version="v1",
        captured_at=NOW,
        nodes=[
            TopologyNode(node_id="gateway", name="Gateway", node_type="service"),
            TopologyNode(node_id="checkout", name="Checkout", node_type="service"),
            TopologyNode(node_id="billing", name="Billing", node_type="service"),
            TopologyNode(node_id="search", name="Search", node_type="service"),
            TopologyNode(node_id="ledger-db", name="Ledger", node_type="database"),
        ],
        edges=[
            TopologyEdge(source="gateway", target="checkout", edge_type="calls"),
            TopologyEdge(source="gateway", target="billing", edge_type="calls"),
            TopologyEdge(source="gateway", target="search", edge_type="calls"),
            TopologyEdge(source="checkout", target="ledger-db", edge_type="depends_on"),
            TopologyEdge(source="billing", target="ledger-db", edge_type="depends_on"),
        ],
    )
    summary = cluster_alerts(
        [
            alert("a-gateway", "gateway", offset_s=0),
            alert("a-checkout", "checkout", offset_s=1),
            alert("a-billing", "billing", offset_s=2),
            alert("a-search", "search", offset_s=3),
            alert("a-ledger", "ledger-db", offset_s=4),
        ],
        hub,
    )
    (cluster,) = summary.clusters
    assert cluster.root_node_id == "ledger-db", (
        "the gateway has the most edges and explains nothing; the database two "
        "members depend on explains them"
    )
