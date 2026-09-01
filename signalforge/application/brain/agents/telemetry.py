"""The telemetry agent: measurements into the graph, unchanged (REQ-052).

This agent contains **no model call at all**, and that is the point rather than
an omission. Detection already computed the anomalies, the SLO burn rates, the
health score, the corroboration verdict and the alert clusters, deterministically
and with tests. Asking a language model to restate them would introduce a
paraphrase where there was a number, cost tokens, and put an `INFERRED` node
where an `OBSERVED` one belongs.

So the agent's whole job is transport with provenance: take what the detector
measured, write it as `FACT` and `EVIDENCE` nodes carrying `Authority.OBSERVED`
and a `result_ref` back to the rollup, and let the hypothesis agent weigh them.
The explaining - "these three signals together look like pool exhaustion" - is
the hypothesis agent's narrative, and even there the number it explains was
computed here.

The one judgement it makes is what counts as **confirmed**: a service is
confirmed as affected when something was measured on it. That set is handed to
the topology agent, which may only *predict* from it.
"""

from __future__ import annotations

from typing import Any

from signalforge.application.brain.agents.base import (
    Agent,
    AgentOutcome,
    EdgeProposal,
    NodeProposal,
    observed,
)
from signalforge.application.brain.evidence import IncidentContext
from signalforge.domain.brain.nodes import (
    ConfirmationState,
    NodeType,
    Relation,
    derived_key,
)

MAX_SIGNAL_FACTS = 12
MAX_CLUSTER_FACTS = 5


class TelemetryAgent(Agent):
    name = "telemetry-agent"

    async def run(self, context: IncidentContext) -> AgentOutcome:
        verdict = context.verdict
        if verdict is None:
            # An honest empty result rather than nothing: an incident whose
            # detector is down is exactly the one where somebody needs to know
            # that the brain is reasoning without telemetry.
            return AgentOutcome(
                skipped=(
                    context.verdict_error
                    or "no detection verdict was available for this application"
                ),
                nodes=(
                    NodeProposal(
                        node_type=NodeType.ASSUMPTION,
                        derived_key=derived_key(NodeType.ASSUMPTION, "no-telemetry"),
                        label="No telemetry is being read for this incident",
                        content=(
                            "The detector could not be evaluated"
                            + (f" ({context.verdict_error})" if context.verdict_error else "")
                            + ". Every conclusion below rests on the declaration, "
                            "on changes, and on what people have said."
                        ),
                        provenance=observed(self.name),
                        tags=("degraded-input",),
                    ),
                ),
            )

        nodes: list[NodeProposal] = []
        edges: list[EdgeProposal] = []

        health = verdict.health
        health_key = derived_key(NodeType.FACT, "health-score")
        nodes.append(
            NodeProposal(
                node_type=NodeType.FACT,
                derived_key=health_key,
                label=(
                    f"Health score {health.score:.0f} ({health.band.value})"
                    if health.score is not None
                    else f"Health score unavailable ({health.band.value})"
                ),
                content=(
                    f"Worst region {health.worst_region or 'unknown'}; "
                    f"{health.covered_weight}% of the mapped families were "
                    f"measurable. {health.detail}"
                ).strip(),
                provenance=observed(
                    "detection", result_ref=f"health:{context.app_id}"
                ),
                confirmation=ConfirmationState.CONFIRMED,
                detail={
                    "score": health.score,
                    "band": health.band.value,
                    "worst_region": health.worst_region or "",
                    # The health score is evidence, not only a headline number.
                    # Without a kind it never reaches the confidence policy, and
                    # the strongest thing the detector says about an application
                    # would count for nothing.
                    "evidence_kind": "health_score",
                    "strength": _health_strength(health.score),
                    "subject": health.worst_region or "",
                },
            )
        )

        corroboration = verdict.corroboration
        nodes.append(
            NodeProposal(
                node_type=NodeType.FACT,
                derived_key=derived_key(NodeType.FACT, "corroboration"),
                label=(
                    "Corroborated across "
                    + ", ".join(corroboration.families)
                    if corroboration.corroborated
                    else "Not corroborated: "
                    + (", ".join(corroboration.families) or "no family is anomalous")
                ),
                content=corroboration.reason,
                provenance=observed(
                    "detection", result_ref=f"corroboration:{context.app_id}"
                ),
                confirmation=ConfirmationState.CONFIRMED,
                detail={
                    "corroborated": corroboration.corroborated,
                    "families": list(corroboration.families),
                    "regions": list(corroboration.regions),
                    # Corroboration across independent families is the strongest
                    # statement this platform makes on its own, and a *lack* of
                    # it argues against the hypotheses that need it. Both
                    # directions are read by the confidence policy.
                    "evidence_kind": "corroboration",
                    "strength": 1.0 if corroboration.corroborated else 0.5,
                },
            )
        )

        # The worst signal per family. Fifty rows saying the same thing is one
        # fact in a large font, and a ledger nobody can read is a ledger nobody
        # reads.
        worst: dict[str, Any] = {}
        for state in verdict.anomalous:
            held = worst.get(state.family.value)
            if held is None or (state.deviation or 0.0) > (held.deviation or 0.0):
                worst[state.family.value] = state

        for family, state in sorted(worst.items())[:MAX_SIGNAL_FACTS]:
            key = derived_key(
                NodeType.EVIDENCE, "signal", state.signal_key, state.region or "all"
            )
            nodes.append(
                NodeProposal(
                    node_type=NodeType.EVIDENCE,
                    derived_key=key,
                    label=(
                        f"{family} anomaly on {state.signal_key}"
                        + (f" [{state.region}]" if state.region else "")
                    ),
                    content=(
                        f"value {state.value} against threshold {state.threshold}, "
                        f"{state.deviation:.1f} deviations, anomalous for "
                        f"{state.streak} consecutive evaluation(s) since "
                        f"{state.anomalous_since.isoformat() if state.anomalous_since else 'unknown'}"
                    ),
                    provenance=observed(
                        "detection", result_ref=f"rollup:{state.signal_key}"
                    ),
                    confirmation=ConfirmationState.CONFIRMED,
                    detail={
                        "family": family,
                        "region": state.region,
                        "evidence_kind": "telemetry_anomaly",
                        "strength": min(2.0, max(0.4, abs(state.deviation) / 3.0)),
                        "subject": state.signal_key,
                    },
                )
            )
            edges.append(
                EdgeProposal(
                    source_key=key,
                    target_key=health_key,
                    relation=Relation.ABOUT,
                    note="contributes to the health score",
                )
            )

        for burn in verdict.burns:
            if not burn.opens_watch:
                continue
            nodes.append(
                NodeProposal(
                    node_type=NodeType.EVIDENCE,
                    derived_key=derived_key(NodeType.EVIDENCE, "slo", burn.slo_id),
                    label=f"SLO {burn.name} is burning ({burn.verdict.value})",
                    content=burn.detail,
                    provenance=observed("detection", result_ref=f"slo:{burn.slo_id}"),
                    confirmation=ConfirmationState.CONFIRMED,
                    detail={
                        "evidence_kind": "slo_burn",
                        "strength": 1.2 if burn.proposes else 0.7,
                        "subject": burn.indicator,
                    },
                )
            )

        for cluster in verdict.storm.clusters[:MAX_CLUSTER_FACTS]:
            nodes.append(
                NodeProposal(
                    node_type=NodeType.EVIDENCE,
                    derived_key=derived_key(
                        NodeType.EVIDENCE, "alerts", cluster.cluster_id
                    ),
                    label=(
                        f"{cluster.size} alert(s) clustered on "
                        f"{cluster.root_node_id or cluster.root_alert_id}"
                    ),
                    content=(
                        f"{cluster.detail} Suppressed {cluster.suppressed_count} "
                        f"duplicate row(s); every id is still listed."
                    ),
                    provenance=observed(
                        "detection", result_ref=f"alert:{cluster.root_alert_id}"
                    ),
                    confirmation=ConfirmationState.CONFIRMED,
                    detail={
                        "evidence_kind": "alert_cluster",
                        "strength": min(2.0, 0.5 + cluster.size / 10.0),
                        "subject": cluster.root_node_id or "",
                        "alert_ids": list(cluster.alert_ids[:20]),
                    },
                )
            )

        journeys = [j for j in verdict.journeys if getattr(j, "customer_facing", False)]
        if journeys:
            nodes.append(
                NodeProposal(
                    node_type=NodeType.IMPACT,
                    derived_key=derived_key(NodeType.IMPACT, "journeys"),
                    label=f"{len(journeys)} customer journey(s) measured as degraded",
                    content=", ".join(
                        getattr(journey, "journey_id", "") or "" for journey in journeys
                    )[:400],
                    provenance=observed("detection", result_ref=f"journeys:{context.app_id}"),
                    # Measured, so confirmed. Anything the topology agent adds
                    # downstream of this is predicted, and says so.
                    confirmation=ConfirmationState.CONFIRMED,
                )
            )

        return AgentOutcome(nodes=tuple(nodes), edges=tuple(edges))


def _health_strength(score) -> float:
    """How hard a health score argues. A score of 100 says nothing is wrong and
    is weak evidence for any hypothesis; a score of 20 is a loud one."""
    if score is None:
        return 0.3
    return round(max(0.2, min(1.5, (100.0 - float(score)) / 60.0)), 3)


def confirmed_services(context: IncidentContext) -> dict[str, str]:
    """Services something was actually measured on, and the measurement.

    The seed set for topology propagation, and the reason CONFIRMED and
    PREDICTED cannot be conflated by accident: everything in this dictionary
    came from a measurement, and everything the topology agent adds to it is
    marked predicted by construction.
    """
    found: dict[str, str] = {}
    verdict = context.verdict
    if verdict is not None:
        for state in verdict.anomalous:
            subject = (state.signal_key or "").strip()
            if subject:
                found.setdefault(
                    subject,
                    f"{state.family.value} anomaly measured on it "
                    f"({state.deviation:.1f} deviations)",
                )
        for cluster in verdict.storm.clusters:
            if cluster.root_node_id:
                found.setdefault(
                    cluster.root_node_id,
                    f"{cluster.size} alert(s) name it",
                )
    if not found:
        # Nothing measured names a service. The application itself is still
        # confirmed - a person declared an incident about it - and that is a
        # different and weaker statement, which the reason says.
        found[context.app_id] = "an incident was declared against this application"
    return found
