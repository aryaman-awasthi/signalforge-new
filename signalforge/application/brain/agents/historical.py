"""Historical learning: what this looked like last time (REQ-052).

The most useful and the most dangerous agent in the roster, and the design is
shaped entirely by the dangerous half. "We saw this three weeks ago and it was
the connection pool" saves twenty minutes when it is right and costs an hour when
it is wrong, and the difference between the two is whether anybody said it was a
guess.

So, in order:

- **It never returns a cause.** Every output is `EVIDENCE` weighted as
  `HISTORICAL_SIMILARITY` - the lowest base weight in the confidence policy, low
  enough that history alone cannot carry a hypothesis past even odds - plus a
  `RISK` node for a mistake warning. There is no code path here that writes a
  fact about the current incident.
- **Every row carries its caveat text**, generated from the domain's own
  `SimilarIncident.caveat()`, so a screen cannot render the score without it.
- **Relevance decays**, and the decayed number is the one shown. An operator
  comparing "82% similar" between a two-year-old incident and last week's is
  comparing the wrong numbers.
- **Embeddings are a minority term and are optional.** They come from the same
  TCS gateway as everything else (`LlmProvider.embed`, S5-T02), and when it
  degrades the structural score stands alone at full weight rather than being
  penalised for a missing term - a degraded gateway must not look like a lack of
  similarity.

The mistake warnings are drawn from what a prior incident *ruled out* rather than
from what it concluded. "We spent forty minutes on the pool last time and it was
not the pool" invites the room to test something; "it was the pool last time"
invites the room to adopt a conclusion.
"""

from __future__ import annotations

import logging
from typing import Optional

from signalforge.application.brain.agents.base import (
    AgentOutcome,
    ModelBackedAgent,
    NodeProposal,
    observed,
)
from signalforge.application.brain.common import GUIDANCE_TOPIC
from signalforge.application.brain.evidence import IncidentContext, fingerprint_of
from signalforge.domain.brain.nodes import NodeType, derived_key
from signalforge.domain.brain.similarity import (
    IncidentFingerprint,
    cosine,
    mistake_warnings,
    rank,
)

logger = logging.getLogger("signalforge.brain")

MAX_EMBEDDED = 12
"""How many prior incidents are embedded in one pass. The structural score is
computed for all of them and the embedding is only ever a tiebreaker, so this cap
costs precision on the tail and nothing on the head."""


class HistoricalLearningAgent(ModelBackedAgent):
    name = "historical-agent"

    async def run(self, context: IncidentContext) -> AgentOutcome:
        if not context.priors:
            return AgentOutcome(
                skipped="this tenant has no closed incidents to learn from"
            )

        current = fingerprint_of(context.incident)
        semantic, degraded, reason, model = await self._semantic(context, current)
        similar = rank(current, context.priors, now=context.now, semantic=semantic)
        if not similar:
            return AgentOutcome(
                skipped=(
                    f"none of {len(context.priors)} prior incident(s) is above the "
                    "relevance floor"
                ),
                degraded=degraded,
                degraded_reason=reason,
            )

        nodes: list[NodeProposal] = []
        events: list[tuple[str, dict]] = []

        for item in similar:
            nodes.append(
                NodeProposal(
                    node_type=NodeType.EVIDENCE,
                    derived_key=derived_key(
                        NodeType.EVIDENCE, "prior", item.fingerprint.incident_id
                    ),
                    label=(
                        f"{item.fingerprint.reference} resembles this incident "
                        f"({item.relevance:.0%} relevance)"
                    ),
                    content=(
                        f"{item.fingerprint.title}. Outcome: {item.outcome}. "
                        f"Raw similarity {item.similarity:.0%}, aged by "
                        f"{item.decay_factor:.0%}. "
                        + item.caveat()
                    ),
                    provenance=observed(
                        self.name,
                        result_ref=f"incident:{item.fingerprint.incident_id}",
                        detail={
                            "components": dict(item.components),
                            "semantic": item.semantic,
                            "embeddings_degraded": degraded,
                        },
                    ),
                    detail={
                        "evidence_kind": "historical_similarity",
                        # Strength is the *relevance*, so an old incident that
                        # matches perfectly contributes less than a recent one
                        # that matches well - which is the belief the decay
                        # curve encodes, applied where it changes a number.
                        "strength": max(0.2, min(1.5, item.relevance * 1.5)),
                        "subject": item.fingerprint.confirmed_cause
                        or item.fingerprint.title,
                        "relevance": item.relevance,
                        "similarity": item.similarity,
                        "movement": item.movement,
                        "source_incident": item.fingerprint.incident_id,
                        "source_reference": item.fingerprint.reference,
                        "advisory": True,
                    },
                    tags=("history", "advisory"),
                )
            )

        for warning in mistake_warnings(similar):
            nodes.append(
                NodeProposal(
                    node_type=NodeType.RISK,
                    derived_key=derived_key(
                        NodeType.RISK,
                        "mistake",
                        warning.source_incident_id,
                        warning.hypothesis_hint,
                    ),
                    label=f"Warning from {warning.source_reference}: {warning.detail}",
                    content=(
                        f"In {warning.source_reference}, “{warning.hypothesis_hint}” "
                        "was pursued and ruled out. This is guidance from a prior "
                        "incident, not a finding about this one: nothing here has "
                        "been measured on the system that is broken now."
                    ),
                    provenance=observed(
                        self.name,
                        result_ref=f"incident:{warning.source_incident_id}",
                    ),
                    detail={
                        "hypothesis_hint": warning.hypothesis_hint,
                        "relevance": warning.relevance,
                        "source_incident": warning.source_incident_id,
                        "source_reference": warning.source_reference,
                        "advisory": True,
                    },
                    tags=("mistake-warning", "advisory"),
                )
            )
            events.append(
                (
                    GUIDANCE_TOPIC,
                    {
                        "incident_id": context.incident_id,
                        "kind": "mistake_warning",
                        "source_incident_id": warning.source_incident_id,
                        "source_reference": warning.source_reference,
                        "detail": warning.detail,
                        "relevance": warning.relevance,
                        "advisory": True,
                    },
                )
            )

        return AgentOutcome(
            nodes=tuple(nodes),
            events=tuple(events),
            degraded=degraded,
            degraded_reason=reason,
            model=model,
            notes=(
                f"{len(similar)} prior incident(s) above the relevance floor"
                + ("; ranked structurally only" if degraded else ""),
            ),
        )

    async def _semantic(
        self, context: IncidentContext, current: IncidentFingerprint
    ) -> tuple[Optional[dict[str, float]], bool, str, str]:
        """Embedding cosines against the prior incidents, or nothing.

        Returns `None` for the map when embeddings are unavailable, which is a
        different thing from returning zeros: `rank` then weights the structural
        score at full strength instead of penalising every candidate for a term
        nobody could compute.
        """
        priors = [prior for prior in context.priors if prior.text][:MAX_EMBEDDED]
        if not priors or not current.text:
            return None, False, "", ""
        result = await self.llm.embed(
            [current.text] + [prior.text for prior in priors],
            tenant_id=context.tenant_id,
            incident_id=context.incident_id,
            purpose="incident-similarity",
        )
        if result.degraded or len(result.vectors) != len(priors) + 1:
            return (
                None,
                True,
                result.reason.value if result.reason else "embeddings unavailable",
                result.model,
            )
        head, rest = result.vectors[0], result.vectors[1:]
        return (
            {
                prior.incident_id: cosine(head, vector)
                for prior, vector in zip(priors, rest)
            },
            False,
            "",
            result.model,
        )
