"""The topology agent: what is confirmed, what is only predicted (REQ-050).

Deterministic, and it holds the line the whole sprint is built around. The
telemetry agent hands it a set of services something was *measured* on. This
agent walks the versioned map outward from those services and everything it adds
is `PREDICTED` - not by a convention somebody has to remember, but because
`domain/brain/topology.propagate` marks it that way and there is no code path
here that can produce a confirmed impact the caller did not supply.

It also answers "has the map changed?", which is a question nobody asks until it
matters. A dependency added an hour before an incident is a change worth
correlating, and a map silently replaced mid-incident explains a blast radius
that stopped making sense at minute twenty.
"""

from __future__ import annotations

from signalforge.application.brain.agents.base import (
    Agent,
    AgentOutcome,
    EdgeProposal,
    NodeProposal,
    observed,
)
from signalforge.application.brain.agents.telemetry import confirmed_services
from signalforge.application.brain.evidence import IncidentContext
from signalforge.domain.brain.nodes import (
    ConfirmationState,
    NodeType,
    Relation,
    derived_key,
)
from signalforge.domain.brain.topology import diff, propagate

MAX_IMPACT_NODES = 25


class TopologyAgent(Agent):
    name = "topology-agent"

    async def run(self, context: IncidentContext) -> AgentOutcome:
        if context.topology is None:
            return AgentOutcome(
                skipped="no topology has been imported for this application",
                nodes=(
                    NodeProposal(
                        node_type=NodeType.ASSUMPTION,
                        derived_key=derived_key(NodeType.ASSUMPTION, "no-topology"),
                        label="No dependency map is imported for this application",
                        content=(
                            "Nothing here says what is downstream of the fault, so "
                            "the blast radius is the severity and the regions a "
                            "person declared, not a computed one."
                        ),
                        provenance=observed(self.name),
                        tags=("degraded-input",),
                    ),
                ),
            )

        confirmed = confirmed_services(context)
        impacts = propagate(context.snapshot, confirmed=confirmed)

        nodes: list[NodeProposal] = []
        edges: list[EdgeProposal] = []

        version_key = derived_key(NodeType.FACT, "topology-version")
        nodes.append(
            NodeProposal(
                node_type=NodeType.FACT,
                derived_key=version_key,
                label=(
                    f"Dependency map version {context.topology.version} "
                    f"({context.topology.node_count} services, "
                    f"{context.topology.edge_count} dependencies)"
                ),
                content=(
                    f"Imported {context.topology.imported_at.isoformat()} from "
                    f"{context.topology.source}."
                ),
                provenance=observed(
                    "topology",
                    result_ref=f"topology:{context.app_id}:{context.topology.version}",
                ),
                confirmation=ConfirmationState.CONFIRMED,
                detail={
                    "version": context.topology.version,
                    "versions": list(context.topology_versions),
                },
            )
        )

        if context.previous_topology is not None:
            delta = diff(
                context.previous_topology.snapshot,
                context.topology.snapshot,
                from_version=context.previous_topology.version,
                to_version=context.topology.version,
            )
            if not delta.empty:
                nodes.append(
                    NodeProposal(
                        node_type=NodeType.EVIDENCE,
                        derived_key=derived_key(
                            NodeType.EVIDENCE,
                            "topology-delta",
                            context.previous_topology.version,
                            context.topology.version,
                        ),
                        label=f"The dependency map changed: {delta.summary()}",
                        content=(
                            "Added services: "
                            + (", ".join(delta.added_nodes[:8]) or "none")
                            + ". Removed: "
                            + (", ".join(delta.removed_nodes[:8]) or "none")
                            + ". New dependencies: "
                            + (
                                ", ".join(
                                    f"{source}->{target}"
                                    for source, target in delta.added_edges[:8]
                                )
                                or "none"
                            )
                            + "."
                        ),
                        provenance=observed(
                            "topology",
                            result_ref=f"topology:{context.app_id}:{context.topology.version}",
                        ),
                        confirmation=ConfirmationState.CONFIRMED,
                        detail={
                            "evidence_kind": "topology_path",
                            "strength": 0.8,
                            "from_version": delta.from_version,
                            "to_version": delta.to_version,
                        },
                    )
                )

        for impact in impacts[:MAX_IMPACT_NODES]:
            key = derived_key(NodeType.IMPACT, "service", impact.service)
            nodes.append(
                NodeProposal(
                    node_type=NodeType.IMPACT,
                    derived_key=key,
                    label=(
                        f"{impact.service}: "
                        + ("affected" if impact.confirmed else "may be affected")
                    ),
                    content=impact.reason,
                    provenance=observed(
                        self.name if not impact.confirmed else "detection",
                        result_ref=(
                            f"topology:{context.app_id}:{context.topology.version}"
                        ),
                    ),
                    # The only place this value is set, and it is set from the
                    # domain's own walk rather than from anything here.
                    confirmation=impact.state,
                    detail={
                        "hops": impact.hops,
                        "likelihood": impact.likelihood,
                        "path": list(impact.path),
                        "region": impact.region,
                        "evidence_kind": "topology_path",
                        "strength": impact.likelihood,
                        "subject": impact.service,
                    },
                    tags=(impact.state.value,),
                )
            )
            if len(impact.path) > 1:
                edges.append(
                    EdgeProposal(
                        source_key=derived_key(
                            NodeType.IMPACT, "service", impact.path[0]
                        ),
                        target_key=key,
                        relation=Relation.DERIVED_FROM,
                        weight=impact.likelihood,
                        note=" -> ".join(impact.path),
                    )
                )

        confirmed_count = sum(1 for impact in impacts if impact.confirmed)
        return AgentOutcome(
            nodes=tuple(nodes),
            edges=tuple(edges),
            notes=(
                f"{confirmed_count} service(s) confirmed by measurement, "
                f"{len(impacts) - confirmed_count} predicted from dependencies",
            ),
        )
