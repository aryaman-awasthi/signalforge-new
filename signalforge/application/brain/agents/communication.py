"""The communication agent: the join brief, written from the graph (REQ-052).

Sprint 4 shipped a deterministic join brief assembled from the incident's own
facts, and this replaces the *writer* behind that abstraction rather than
replacing the brief. The template is not deprecated and is not a fallback of last
resort - it is the floor, it runs first, and the model's output is measured
against it.

The order is:

1. Build the deterministic brief. Always. It is complete and usable on its own.
2. Give the model the incident graph - facts, hypotheses with their computed
   confidences, impacts, changes, open work - plus the template's own text as
   the ground truth, and ask for a better-written version *of that material*.
3. Validate. Every section must be present, nothing may be empty that was
   populated, and the caveats are **unioned** with the template's rather than
   replaced, so a model cannot produce a confident brief by dropping the
   sentence that says the evidence is thin.
4. On any failure - degraded, off-schema, empty - return the template with
   `BriefSource.DEGRADED`, which is what puts the badge on the screen.

Confidence numbers are passed *in* and are never asked for. The brief states
what the platform computed; a model restating "roughly 60% confident" in prose
would be a second, unaudited number beside the first.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Mapping, Optional, Sequence

from signalforge.domain.brain.nodes import (
    ConfirmationState,
    GraphNode,
    NodeType,
)
from signalforge.domain.brain.untrusted import SYSTEM_FLOOR, UntrustedItem, fence
from signalforge.domain.llm.entities import (
    CompletionRequest,
    Message,
    ModelClass,
)
from signalforge.domain.llm.ports import LlmProvider
from signalforge.domain.warroom.brief import TEMPLATE_PROVENANCE
from signalforge.domain.warroom.entities import BriefSource, JoinBrief

logger = logging.getLogger("signalforge.brain")

PURPOSE = "join-brief"
MAX_LINES = 8
MAX_LINE_CHARS = 300

SECTIONS = (
    "what_is_happening",
    "impact",
    "what_we_know",
    "what_has_been_done",
    "change_context",
    "what_we_need",
    "caveats",
)

BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", *SECTIONS],
    "properties": {
        "headline": {"type": "string"},
        **{
            section: {"type": "array", "items": {"type": "string"}}
            for section in SECTIONS
        },
    },
}
"""The *shape* of a brief, and only the shape.

`MAX_LINES` and `MAX_LINE_CHARS` are deliberately not in here. They are
presentational bounds, and the provider treats a schema failure as no answer at
all - correctly, because a half-parsed object arriving with the model's
authority is worse than the deterministic fallback. But a nine-line section is
not a half-parsed object: it is a good brief that is one line too long, and
throwing it away puts a DEGRADED badge on a screen that had nothing wrong with
it. Structured-output modes do not enforce `maxItems` or `maxLength` either, so
in practice this failed most of the time rather than rarely.

The bounds are stated to the model in the prompt, where it can act on them, and
enforced in `_validate`, which clamps rather than rejects. Shape is a
correctness question and belongs to the schema; length is a layout question and
belongs to the reader."""


class JoinBriefWriter:
    """The model-backed writer, with the template as its floor.

    Held by `WarRoomService` as an optional collaborator, so a node with no
    writer wired keeps exactly Sprint 4's behaviour and a node with one degrades
    to it. The war room does not import the brain: the composition root passes
    this in, and the arrow keeps pointing inward.
    """

    name = "communication-agent"

    def __init__(self, llm: LlmProvider):
        self._llm = llm

    async def write(
        self,
        template: JoinBrief,
        *,
        tenant_id: str,
        nodes: Sequence[GraphNode] = (),
        for_role_reason: str = "",
    ) -> JoinBrief:
        """A brief grounded in the graph, or the template with a DEGRADED badge."""
        grounding = _grounding(nodes)
        result = await self._llm.complete(
            CompletionRequest(
                purpose=PURPOSE,
                messages=(
                    Message(role="system", content=SYSTEM_FLOOR),
                    Message(role="user", content=_prompt(template, grounding, for_role_reason)),
                ),
                model_class=ModelClass.GENERAL,
                schema=BRIEF_SCHEMA,
                fallback=None,
                tenant_id=tenant_id,
                incident_id=template.incident_id,
                max_tokens=1_100,
            )
        )
        if result.degraded or not isinstance(result.data, dict):
            # The deterministic brief, unchanged, wearing the badge that says a
            # model was tried and could not be used. The reader can calibrate.
            return replace(template, source=BriefSource.DEGRADED)

        written = _validate(result.data, template)
        if written is None:
            logger.info(
                "the model's join brief was rejected; serving the template",
                extra={"incident_id": template.incident_id},
            )
            return replace(template, source=BriefSource.DEGRADED)
        return written


def _prompt(
    template: JoinBrief, grounding: Sequence[UntrustedItem], for_role_reason: str
) -> str:
    block = (
        fence(grounding, salt=f"{template.incident_id}:brief")
        if grounding
        else "(the incident graph is empty)"
    )
    audience = (
        f"\nThe reader was paged because: {for_role_reason}\n" if for_role_reason else ""
    )
    return (
        "Rewrite an incident join brief for somebody walking into a war room at "
        "minute forty, reading it on a phone with one hand.\n\n"
        "Rules:\n"
        "- Use only the material below. Add nothing.\n"
        "- Do not state any confidence percentage. Confidences are computed and "
        "are shown beside your text.\n"
        "- Keep every caveat. If the evidence is thin, the brief says so.\n"
        "- Say what is confirmed and what is only predicted, in those words.\n"
        "- No preamble, no sign-off.\n"
        "- Do not say who or what wrote this brief. The platform states that "
        "itself, beneath your text, and it is the one thing you cannot know.\n"
        f"- At most {MAX_LINES} lines per section, each under "
        f"{MAX_LINE_CHARS} characters. It is read on a phone.\n"
        f"{audience}\n"
        "THE DETERMINISTIC BRIEF (ground truth, assembled from stored facts):\n"
        f"{template.as_text()}\n\n"
        "THE INCIDENT GRAPH:\n"
        f"{block}"
    )


def _grounding(nodes: Sequence[GraphNode]) -> tuple[UntrustedItem, ...]:
    """The graph, as fenced items the brief may draw on.

    Fenced even though most of it is the platform's own writing, because some of
    it is not: a statement node carries a bridge message verbatim, and one fence
    over everything is a floor rather than a per-node decision somebody has to
    get right.
    """
    wanted = (
        NodeType.FACT,
        NodeType.HYPOTHESIS,
        NodeType.IMPACT,
        NodeType.EVIDENCE,
        NodeType.RISK,
        NodeType.ACTION,
    )
    items: list[UntrustedItem] = []
    for node in nodes:
        if node.node_type not in wanted or not node.current:
            continue
        marker = ""
        if node.confirmation is ConfirmationState.PREDICTED:
            marker = " [PREDICTED, not observed]"
        elif node.confirmation is ConfirmationState.CONFIRMED:
            marker = " [CONFIRMED by measurement]"
        confidence = (
            f" [computed confidence {node.confidence:.0%}]"
            if node.confidence is not None
            else ""
        )
        items.append(
            UntrustedItem(
                ref=node.id,
                kind=node.node_type.value,
                text=f"{node.label}{marker}{confidence}. {node.content}".strip(),
            )
        )
    return tuple(items[:40])


def _validate(data: Mapping[str, Any], template: JoinBrief) -> Optional[JoinBrief]:
    """Accept the model's brief only if it is at least as honest as the template.

    The caveats are unioned rather than taken, which is the load-bearing line in
    this file: a model that produced a confident brief by dropping "the evidence
    is not corroborated" would be strictly worse than no model, and this makes
    that impossible rather than unlikely.
    """
    headline = str(data.get("headline") or "").strip()
    if not headline:
        return None
    sections: dict[str, tuple[str, ...]] = {}
    for section in SECTIONS:
        lines = [
            str(line).strip()[:MAX_LINE_CHARS]
            for line in (data.get(section) or [])
            if str(line).strip()
        ]
        held = getattr(template, section)
        if held and not lines:
            # A section the template filled and the model emptied. That is not a
            # rewrite, it is a loss.
            return None
        sections[section] = tuple(lines[:MAX_LINES])

    caveats = list(sections["caveats"])
    for caveat in template.caveats:
        # Every evidence caveat is kept whether the model wanted it or not. The
        # template's provenance line is the one exception: it says a template
        # wrote this, and a template did not.
        if caveat != TEMPLATE_PROVENANCE and caveat not in caveats:
            caveats.append(caveat)
    caveats = [line for line in caveats if line != TEMPLATE_PROVENANCE]
    caveats.append("Written by a model from the incident's stored facts.")
    sections["caveats"] = tuple(caveats[: MAX_LINES + 4])

    return JoinBrief(
        incident_id=template.incident_id,
        generated_at=template.generated_at,
        source=BriefSource.MODEL,
        headline=headline[:300],
        **sections,
    )
