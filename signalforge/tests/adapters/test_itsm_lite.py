"""itsm-lite, the built-in simulated ticket queue (REQ-033).

Two behaviours are worth more than the storage and both are failure design:
creating the same ticket twice is one ticket (ITSM calls are retried by
everything, and a duplicate incident ticket costs a human real time), and a
closed ticket cannot be reopened by a retrying machine.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from signalforge.contracts.canonical import Ticket, TicketStatus
from signalforge.contracts.errors import AdapterError
from signalforge.tests.adapters.conftest import connect

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def ticket(ticket_id="INC-1", **kwargs) -> Ticket:
    return Ticket(
        ticket_id=ticket_id,
        title=kwargs.pop("title", "Checkout error rate above SLO"),
        status=kwargs.pop("status", TicketStatus.OPEN),
        created_at=kwargs.pop("created_at", NOW),
        **kwargs,
    )


@pytest.fixture
def itsm(api):
    signup = api.signup()
    token = signup["tokens"]["access_token"]
    tenant_id = signup["org"]["id"]
    binding = connect(api, token, "itsm-lite", credentials={})

    class Itsm:
        tenant = tenant_id
        api_token = token
        binding_id = binding["id"]

        async def invoke(self, capability, *args, **kwargs):
            return await api.container.invoker.invoke(
                tenant_id, binding["id"], capability, *args, **kwargs
            )

    return Itsm()


@pytest.mark.req("REQ-033")
async def test_creating_a_ticket_mints_a_reference(itsm):
    created = await itsm.invoke("create", ticket())
    assert created.external_ref == "LITE-000001"
    assert created.status is TicketStatus.OPEN
    assert created.updated_at is not None

    second = await itsm.invoke("create", ticket("INC-2"))
    assert second.external_ref == "LITE-000002"


@pytest.mark.req("REQ-033")
async def test_creating_the_same_ticket_twice_is_one_ticket(itsm):
    first = await itsm.invoke("create", ticket())
    again = await itsm.invoke("create", ticket(title="a different title"))
    assert again.external_ref == first.external_ref
    assert again.title == first.title, "the retry did not overwrite the original"


@pytest.mark.req("REQ-033")
async def test_a_ticket_survives_and_is_readable(itsm):
    await itsm.invoke("create", ticket(severity="sev2", assignee="ada"))
    fetched = await itsm.invoke("get", "INC-1")
    assert fetched.severity == "sev2"
    assert fetched.assignee == "ada"


@pytest.mark.req("REQ-033")
async def test_updates_walk_the_status_machine(itsm):
    await itsm.invoke("create", ticket())
    working = await itsm.invoke("update", "INC-1", {"status": "in_progress",
                                                    "assignee": "ada"})
    assert working.status is TicketStatus.IN_PROGRESS
    resolved = await itsm.invoke("update", "INC-1", {"status": "resolved"})
    assert resolved.status is TicketStatus.RESOLVED
    reopened = await itsm.invoke("update", "INC-1", {"status": "open"})
    assert reopened.status is TicketStatus.OPEN, "incidents regress; reopening is legal"


@pytest.mark.req("REQ-033")
async def test_a_closed_ticket_cannot_be_reopened_by_a_machine(itsm):
    """Closure records that a human signed off. A retrying workflow must not
    quietly undo it."""
    await itsm.invoke("create", ticket())
    await itsm.invoke("update", "INC-1", {"status": "resolved"})
    await itsm.invoke("update", "INC-1", {"status": "closed"})
    with pytest.raises(AdapterError) as refused:
        await itsm.invoke("update", "INC-1", {"status": "open"})
    assert "cannot become open" in str(refused.value)


@pytest.mark.req("REQ-033")
async def test_an_unknown_status_or_field_is_refused(itsm):
    await itsm.invoke("create", ticket())
    with pytest.raises(AdapterError) as bad_status:
        await itsm.invoke("update", "INC-1", {"status": "wibble"})
    assert "not a ticket status" in str(bad_status.value)

    with pytest.raises(AdapterError) as bad_field:
        await itsm.invoke("update", "INC-1", {"created_at": "2020-01-01"})
    assert "cannot be updated" in str(bad_field.value)


@pytest.mark.req("REQ-033")
async def test_updating_or_reading_a_ticket_that_does_not_exist(itsm):
    with pytest.raises(AdapterError):
        await itsm.invoke("get", "nope")
    with pytest.raises(AdapterError):
        await itsm.invoke("update", "nope", {"status": "resolved"})


@pytest.mark.req("REQ-033", "REQ-018")
async def test_the_description_is_redacted_before_it_is_stored(itsm):
    """A ticket description reaches briefs and prompts. Redacting at write time
    means a later reader of the row cannot bypass it."""
    await itsm.invoke(
        "create",
        ticket(description="raised by ada@example.com, card 4111 1111 1111 1111"),
    )
    stored = await itsm.invoke("get", "INC-1")
    assert "ada@example.com" not in stored.description
    assert "4111" not in stored.description
    assert stored.description.count("***") == 2


@pytest.mark.req("REQ-033", "REQ-013")
async def test_one_tenants_tickets_are_invisible_to_another(api, itsm):
    await itsm.invoke("create", ticket())

    other = api.signup(org="Other Corp", email="owner@other.io")
    other_token = other["tokens"]["access_token"]
    other_binding = connect(api, other_token, "itsm-lite", credentials={})
    with pytest.raises(AdapterError):
        await api.container.invoker.invoke(
            other["org"]["id"], other_binding["id"], "get", "INC-1"
        )

    mine = await itsm.invoke("get", "INC-1")
    assert mine.ticket_id == "INC-1"


@pytest.mark.req("REQ-033")
async def test_the_reference_prefix_is_configurable(api):
    signup = api.signup(org="Prefixed", email="p@acme.io")
    token = signup["tokens"]["access_token"]
    binding = connect(
        api, token, "itsm-lite", credentials={}, config={"reference_prefix": "OPS"}
    )
    created = await api.container.invoker.invoke(
        signup["org"]["id"], binding["id"], "create", ticket()
    )
    assert created.external_ref == "OPS-000001"


@pytest.mark.req("REQ-033")
async def test_itsm_lite_is_labelled_simulated_end_to_end(api, itsm):
    listing = api.client.get(
        "/connectors", headers=api.auth_header(itsm.api_token)
    ).json()
    row = next(entry for entry in listing if entry["adapter_id"] == "itsm-lite")
    assert row["simulated"] is True
