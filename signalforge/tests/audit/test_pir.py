"""The post-incident review, and the gate on its lessons (REQ-072).

The claim under test is narrow and important: a lesson becomes part of this
organisation's institutional memory only when every one of its citations
resolves to a record *this incident* produced. Everything else is a proposal,
and it is labelled as one.

That gate is what stops the most attractive failure mode an AI incident platform
has - a plausible sentence about what went wrong, promoted to a fact because it
was well written.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from signalforge.domain.pir.review import (
    Citation,
    Lesson,
    LessonSource,
    LessonStatus,
    PostIncidentReview,
    Section,
)
from signalforge.tests.audit.conftest import (  # noqa: F401  (fixtures)
    APP_ID,
    act,
    audit_seqs,
    governed,
    observe,
    request_execution,
    run_validation,
    tamper,
)
from signalforge.tests.audit.test_audit_replay import _an_incident_with_a_history

pytestmark = pytest.mark.anyio

AT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _pir(api, token: str, incident_id: str, *, expect: int = 201) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/pir", json={}, headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


# --- the gate, in the domain, with no infrastructure at all ---------------------


@pytest.mark.req("REQ-072")
def test_a_lesson_cannot_be_constructed_verified_without_citations():
    with pytest.raises(ValueError) as raised:
        Lesson(id="l1", text="always roll back faster", status=LessonStatus.VERIFIED)
    assert "cites" in str(raised.value)


@pytest.mark.req("REQ-072")
def test_a_lesson_whose_citation_does_not_resolve_stays_proposed():
    """The model's favourite failure: a well-written sentence about nothing."""
    invented = Lesson(
        id="l2",
        text="the connection pool has always been the cause of these outages",
        citations=(Citation(ref="execution:does-not-exist", kind="execution"),),
        source=LessonSource.MODEL,
    )
    gated = invented.verify(["execution:real-one", "audit:4"], at=AT)
    assert gated.status is LessonStatus.PROPOSED
    assert gated.unresolved == ("execution:does-not-exist",)
    assert gated.verified_at is None


@pytest.mark.req("REQ-072")
def test_a_lesson_with_no_citation_at_all_stays_proposed():
    bare = Lesson(id="l3", text="we should test more", source=LessonSource.MODEL)
    gated = bare.verify(["audit:1"], at=AT)
    assert gated.status is LessonStatus.PROPOSED
    assert gated.unresolved == ("<no citation>",)


@pytest.mark.req("REQ-072")
def test_a_lesson_is_verified_only_when_every_citation_resolves():
    partly = Lesson(
        id="l4",
        text="the rollback timed out and landed late",
        citations=(
            Citation(ref="execution:e1", kind="execution"),
            Citation(ref="validation:v9", kind="validation"),
        ),
    )
    assert partly.verify(["execution:e1"], at=AT).status is LessonStatus.PROPOSED
    fully = partly.verify(["execution:e1", "validation:v9"], at=AT)
    assert fully.status is LessonStatus.VERIFIED
    assert fully.verified_at == AT


@pytest.mark.req("REQ-072")
def test_a_review_separates_verified_from_proposed():
    review = PostIncidentReview(
        incident_id="i1",
        tenant_id="t1",
        reference="INC-000001",
        title="Checkout failing",
        app_id="checkout",
        generated_at=AT,
        sections=(Section(key="summary", title="Incident summary", lines=("x",)),),
        lessons=(
            Lesson(
                id="a",
                text="verified thing",
                citations=(Citation(ref="audit:1", kind="audit"),),
                status=LessonStatus.VERIFIED,
            ),
            Lesson(id="b", text="proposed thing", source=LessonSource.MODEL),
        ),
    )
    assert [lesson.id for lesson in review.verified_lessons] == ["a"]
    assert [lesson.id for lesson in review.proposed_lessons] == ["b"]
    markdown = review.as_markdown()
    assert "**[VERIFIED]** verified thing" in markdown
    assert "**[PROPOSED]** proposed thing" in markdown


# --- the whole thing, over the real API ----------------------------------------


@pytest.mark.req("REQ-072", "REQ-016")
async def test_a_pir_is_generated_from_the_incidents_own_records(governed):
    incident_id = await _an_incident_with_a_history(governed)
    review = _pir(governed.api, governed.owner_token, incident_id)

    assert review["chain_valid"] is True
    assert review["degraded"] is True, "the deterministic path is the floor"
    assert review["model"] == "deterministic"

    keys = [section["key"] for section in review["sections"]]
    for required in (
        "summary",
        "impact",
        "timeline",
        "detection",
        "changes",
        "hypotheses",
        "evidence",
        "actions",
        "approvals",
        "uncertain",
        "validation",
        "communications",
        "worked",
        "failed",
        "lessons",
        "followups",
    ):
        assert required in keys, f"a review without a {required} section is not a review"

    sections = {section["key"]: section for section in review["sections"]}
    assert sections["timeline"]["lines"], "the timeline is the review"
    assert any(
        "rollback" in line for line in sections["actions"]["lines"]
    ), sections["actions"]["lines"]
    assert any(
        "completed late" in line or "completed_late" in line
        for line in sections["uncertain"]["lines"]
    ), sections["uncertain"]["lines"]
    assert any(
        "floor" in line or "without a person" in line
        for line in sections["worked"]["lines"]
    ), sections["worked"]["lines"]

    # Every timeline line is backed by an audit seq that can be opened.
    assert sections["timeline"]["citations"]
    assert all(
        citation["ref"].startswith("audit:")
        for citation in sections["timeline"]["citations"]
    )
    assert review["markdown"].startswith("# Post-incident review:")


@pytest.mark.req("REQ-072")
async def test_the_reviews_verified_lessons_all_cite_this_incident(governed):
    incident_id = await _an_incident_with_a_history(governed)
    review = _pir(governed.api, governed.owner_token, incident_id)

    assert review["verified_lesson_count"] >= 1, "this incident produced lessons"
    refs_in_review = {
        citation["ref"]
        for section in review["sections"]
        for citation in section["citations"]
    }
    for lesson in review["lessons"]:
        if lesson["status"] != "verified":
            continue
        assert lesson["citations"], "a verified lesson cites something"
        assert not lesson["unresolved"]
        for citation in lesson["citations"]:
            assert citation["ref"].split(":", 1)[0] in {
                "audit",
                "execution",
                "validation",
                "communication",
                "node",
            }
            # Audit citations are the only kind the sections enumerate wholesale;
            # the others resolve against the incident's own ledger rows.
            if citation["ref"].startswith("audit:"):
                assert citation["ref"] in refs_in_review


@pytest.mark.req("REQ-072")
async def test_two_generations_of_one_review_are_byte_identical(governed):
    """A PIR is derived, not stored. Regenerating it is the guarantee."""
    incident_id = await _an_incident_with_a_history(governed)
    first = _pir(governed.api, governed.owner_token, incident_id)
    second = _pir(governed.api, governed.owner_token, incident_id)
    assert first["markdown"] == second["markdown"]


@pytest.mark.req("REQ-072", "REQ-016")
async def test_a_pir_is_refused_outright_when_the_chain_does_not_verify(governed):
    """No banner, no caveat, no document. A PIR is quoted."""
    incident_id = await _an_incident_with_a_history(governed)
    target = audit_seqs(governed.api.db_path, governed.tenant_id, "execution.blocked")[0]
    tamper(governed.api.db_path, seq=target)

    response = governed.api.client.post(
        f"/incidents/{incident_id}/pir",
        json={},
        headers=governed.api.auth_header(governed.owner_token),
    )
    assert response.status_code == 409, response.text
    assert "not evidence" in response.json()["detail"].lower()


@pytest.mark.req("REQ-072", "REQ-013")
async def test_another_tenant_cannot_generate_a_pir_for_this_incident(governed):
    incident_id = await _an_incident_with_a_history(governed)
    intruder = governed.api.signup(
        org="Rival Ltd", email="owner@rival.test", display_name="Rival Owner"
    )
    response = governed.api.client.post(
        f"/incidents/{incident_id}/pir",
        json={},
        headers=governed.api.auth_header(intruder["tokens"]["access_token"]),
    )
    assert response.status_code == 404, response.text
