"""One optional live evaluation against the real gateway (S5-T06, REQ-055).

Gated exactly as the S5-T02 smoke test is: it runs only when `GENAILAB_API_KEY`
is present *and* `SIGNALFORGE_LIVE_LLM=1` is set deliberately, so a key exported
for another purpose does not silently start spending tokens during `make test`,
and so a release never depends on an external model being reachable.

What it proves that a mock cannot: that the *real* model, behind the real
gateway, respects the two constraints this sprint's grounding floor rests on -
it answers within the structured schema, and it declines rather than invents when
the supplied evidence does not contain the answer. If a future model regressed on
either, the mocked suite would still pass and this would not.

Note what is *not* asserted: the wording. An eval that pinned phrasing would
break on every model upgrade for no reason connected to safety.
"""

from __future__ import annotations

import os

import pytest

from signalforge.application.brain.agents.copilot import CopilotAgent
from signalforge.domain.brain.grounding import Verdict
from signalforge.infrastructure.config import load_llm_settings
from signalforge.infrastructure.llm.provider import build_llm_provider
from signalforge.tests.ai_eval.conftest import graph_node
from signalforge.domain.brain.nodes import ConfirmationState, NodeType

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        not (
            os.environ.get("GENAILAB_API_KEY")
            and os.environ.get("SIGNALFORGE_LIVE_LLM")
        ),
        reason="live AI eval: set GENAILAB_API_KEY and SIGNALFORGE_LIVE_LLM=1",
    ),
]


def evidence():
    return [
        graph_node(
            "pool-usage",
            "connection-pool utilisation is 100%",
            content="measured at 14:02 in eu-west-1, 4.2 deviations above baseline",
            confirmation=ConfirmationState.CONFIRMED,
        ),
        graph_node(
            "pool",
            "The connection pool is exhausted and callers are queueing",
            node_type=NodeType.HYPOTHESIS,
            content="Discriminating test: a probe that would fail if this were true",
            confidence=0.62,
        ),
    ]


@pytest.mark.req("REQ-055")
async def test_the_live_model_answers_within_the_grounding_contract():
    provider = build_llm_provider(load_llm_settings())
    try:
        answer = await CopilotAgent(provider).answer(
            "what is the connection pool doing?",
            tenant_id="_platform",
            incident_id="live-eval",
            nodes=evidence(),
        )
    finally:
        await provider.aclose()

    assert not answer.degraded, "the live gateway did not serve this call"
    assert answer.verdict in (Verdict.GROUNDED, Verdict.INSUFFICIENT)
    if answer.verdict is Verdict.GROUNDED:
        # Grounded means cited, and cited means cited from what was retrieved.
        assert set(answer.citations) <= set(answer.considered)


@pytest.mark.req("REQ-055")
async def test_the_live_model_declines_a_question_the_evidence_cannot_answer():
    provider = build_llm_provider(load_llm_settings())
    try:
        answer = await CopilotAgent(provider).answer(
            "how many customers in ap-south-1 were charged twice?",
            tenant_id="_platform",
            incident_id="live-eval",
            nodes=evidence(),
        )
    finally:
        await provider.aclose()

    assert not answer.degraded
    # Either it said so, or it produced something ungrounded and the platform
    # discarded it. Both end at a refusal the operator can trust; what must not
    # happen is a confident answer about customers nobody measured.
    assert answer.verdict in (Verdict.INSUFFICIENT, Verdict.UNGROUNDED)
