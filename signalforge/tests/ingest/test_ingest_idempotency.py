"""Ingest idempotency and tenant isolation (REQ-032, REQ-013).

Written failing-first, because these are the two properties that cannot be
retro-fitted. `event_id` is the idempotency unit named by the frozen ingest
contract: a sender that timed out waiting for our 202 has no way to know whether
we recorded its batch, so it will send it again, and the *only* safe answer is
to acknowledge the repeat without applying it twice. Everything else about the
ingest API is a detail; this is the contract.

The second half is the tenant boundary. Two tenants may pick the same
`event_id` - they are producer-assigned - and neither may be able to tell that
the other did, nor block it.
"""

from __future__ import annotations

import uuid

import pytest

from signalforge.tests.ingest.conftest import (
    Sender,
    batch,
    change,
    events_of,
    issue_key,
)


@pytest.mark.req("REQ-032")
def test_same_event_id_twice_is_one_event(changes_sender):
    event_id = str(uuid.uuid4())
    body = batch(change("deploy-7", _event_id=event_id))

    first = changes_sender.post(body)
    assert first.status_code == 202, first.text
    assert first.json()["accepted"] == 1
    assert first.json()["duplicates"] == 0

    second = changes_sender.post(body)
    assert second.status_code == 202, second.text
    assert second.json() | {"request_id": None} == {
        "accepted": 0,
        "duplicates": 1,
        "rejected": [],
        "request_id": None,
    }


@pytest.mark.req("REQ-032")
def test_a_duplicate_publishes_no_second_event(api, owner, changes_sender):
    event_id = str(uuid.uuid4())
    body = batch(change("deploy-8", _event_id=event_id))
    changes_sender.post(body)
    changes_sender.post(body)
    changes_sender.post(body)

    published = events_of(api, owner["token"], ("deployment.started",))
    assert len(published) == 1, "three posts of one event_id must be one event"
    assert published[0]["payload"]["change_id"] == "deploy-8"
    assert published[0]["idempotency_key"] == f"ingest:changes:{event_id}"


@pytest.mark.req("REQ-032")
def test_a_duplicate_is_audited(api, owner, changes_sender):
    body = batch(change("deploy-9"))
    changes_sender.post(body)
    changes_sender.post(body)

    duplicates = [
        row
        for row in api.audit_rows(owner["tenant_id"])
        if row["topic"] == "audit.ingest.duplicate"
    ]
    assert len(duplicates) == 1, "the duplicate must be evidenced, not just counted"
    assert duplicates[0]["payload"]["count"] == 1
    assert duplicates[0]["payload"]["stream"] == "changes"


@pytest.mark.req("REQ-032")
def test_a_repeated_event_id_inside_one_batch_is_one_event(api, owner, changes_sender):
    event_id = str(uuid.uuid4())
    body = batch(
        change("deploy-10", _event_id=event_id),
        change("deploy-10", _event_id=event_id),
    )
    ack = changes_sender.post(body).json()
    assert (ack["accepted"], ack["duplicates"]) == (1, 1)
    assert len(events_of(api, owner["token"], ("deployment.started",))) == 1


@pytest.mark.req("REQ-032")
def test_a_partly_invalid_batch_keeps_its_valid_items(api, owner, changes_sender):
    body = batch(change("deploy-11"), change("deploy-12"))
    body["items"][1]["payload"].pop("service")

    ack = changes_sender.post(body).json()
    assert ack["accepted"] == 1
    assert [r["index"] for r in ack["rejected"]] == [1]
    assert "service" in ack["rejected"][0]["reason"]

    published = events_of(api, owner["token"], ("deployment.started",))
    assert [e["payload"]["change_id"] for e in published] == ["deploy-11"]


@pytest.mark.req("REQ-032")
def test_a_rejected_item_can_be_resent_after_it_is_fixed(changes_sender):
    """A rejection must not consume the event_id: the sender fixes the payload
    and retries with the same id, and that is an acceptance, not a duplicate."""
    event_id = str(uuid.uuid4())
    broken = batch(change("deploy-13", _event_id=event_id))
    broken["items"][0]["payload"].pop("service")
    assert changes_sender.post(broken).json()["accepted"] == 0

    fixed = batch(change("deploy-13", _event_id=event_id))
    ack = changes_sender.post(fixed).json()
    assert (ack["accepted"], ack["duplicates"]) == (1, 0)


@pytest.mark.req("REQ-032", "REQ-013")
def test_two_tenants_may_use_the_same_event_id(api):
    """event_id is producer-assigned, so a collision across tenants is expected
    and must be invisible: neither tenant may block or observe the other."""
    first = api.signup(org="One", email="one@acme.io")
    second = api.signup(org="Two", email="two@beta.io")
    shared = str(uuid.uuid4())

    senders = {}
    for label, signup in (("one", first), ("two", second)):
        token = signup["tokens"]["access_token"]
        senders[label] = (
            token,
            Sender(api=api, key=issue_key(api, token, stream="changes")),
        )

    for label, (token, sender) in senders.items():
        ack = sender.post(batch(change(f"deploy-{label}", _event_id=shared))).json()
        assert ack["accepted"] == 1, f"{label} was blocked by the other tenant's id"

    for label, (token, _sender) in senders.items():
        published = events_of(api, token, ("deployment.started",))
        assert [e["payload"]["change_id"] for e in published] == [f"deploy-{label}"]


@pytest.mark.req("REQ-032", "REQ-013")
def test_a_key_cannot_be_used_against_another_tenant(api):
    """The key carries its tenant. Everything it writes lands there and nowhere
    else, so a stolen key is bounded by the tenant it was issued in."""
    first = api.signup(org="One", email="one@acme.io")
    second = api.signup(org="Two", email="two@beta.io")
    sender = Sender(
        api=api,
        key=issue_key(api, first["tokens"]["access_token"], stream="changes"),
    )
    sender.post(batch(change("deploy-1")))

    assert events_of(api, second["tokens"]["access_token"], ("deployment.started",)) == []
    assert len(events_of(api, first["tokens"]["access_token"], ("deployment.started",))) == 1
