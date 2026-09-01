"""Keeping an ITSM ticket in step with an incident (REQ-043, REQ-033).

The happy path runs against the shipped itsm-lite queue through the real
`POST /connectors` binding and the real ConnectorInvoker, so what is under test
is the credential-scope floor, the manifest limits and the ticket state machine
rather than a mock.

The failure paths need an ITSM that misbehaves on demand, so they install one:
a manifest written into a temp adapters tree and a runtime registered against it.
That is exactly how a deployment installs an adapter, and it is the only way to
exercise the state this design exists for - `uncertain`, where a call timed out
and the ticket may or may not be there.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest
import yaml

from signalforge.contracts.canonical import Ticket, TicketStatus
from signalforge.contracts.errors import AdapterError, AdapterTimeoutError
from signalforge.contracts.spi import ItsmAdapter
from signalforge.tests.adapters.conftest import connect
from signalforge.tests.incidents.conftest import (
    APP_ID,
    close_incident,
    commander,
    declare,
    onboard,
    owner,
    read_incident,
    resync,
    set_severity,
    set_state,
    topics,
)

pytestmark = pytest.mark.anyio

PROBE_ITSM = {
    "id": "probe-itsm",
    "name": "Probe ITSM",
    "version": "1.0.0",
    "kind": "itsm",
    "mode": "invoke",
    "capabilities": ["create", "update", "get"],
    "credential_scopes": ["remediation"],
    "redaction": {"policy_id": "probe-itsm-default", "patterns": []},
}


class StubItsmAdapter(ItsmAdapter):
    """An ITSM whose behaviour a test drives.

    `tickets` is class state on purpose: the invoker builds a *new* adapter for
    every call - nothing caches an adapter, because nothing may cache a
    credential - so a queue that forgot between calls could not model a queue.
    """

    tickets: dict[str, Ticket] = {}
    behaviour: dict[str, Any] = {}

    def __init__(self, context):
        self.context = context

    @classmethod
    def reset(cls) -> None:
        cls.tickets = {}
        cls.behaviour = {}

    async def ping(self) -> bool:
        return True

    def _act(self, capability: str) -> None:
        staged = self.behaviour.get(capability)
        if staged is not None:
            raise staged

    async def create(self, ticket: Ticket) -> Ticket:
        self._act("create")
        stored = ticket.model_copy(update={"external_ref": f"PRB-{len(self.tickets) + 1:04d}"})
        self.tickets[ticket.ticket_id] = stored
        return stored

    async def update(self, ticket_id: str, fields: dict[str, Any]) -> Ticket:
        self._act("update")
        existing = self.tickets.get(ticket_id)
        if existing is None:
            raise AdapterError(f"ticket {ticket_id} does not exist")
        updates = dict(fields)
        if "status" in updates:
            updates["status"] = TicketStatus(updates["status"])
        stored = existing.model_copy(update=updates)
        self.tickets[ticket_id] = stored
        return stored

    async def get(self, ticket_id: str) -> Ticket:
        self._act("get")
        existing = self.tickets.get(ticket_id)
        if existing is None:
            raise AdapterError(f"ticket {ticket_id} does not exist")
        return existing


@pytest.fixture
def itsm_api(tmp_path):
    """The API with an adapters tree holding the stub ITSM.

    A temp tree rather than a write into the shipped one: installing an adapter
    is pointing `SIGNALFORGE_ADAPTERS_DIR` at a folder, which is the same knob a
    deployment turns, and it leaves the registry the rest of the suite reads
    untouched.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from signalforge.api import deps
    from signalforge.api.main import app
    from signalforge.infrastructure.persistence.models import Base
    from signalforge.tests.helpers import ApiCtx, FakeClock, make_test_settings

    tree = tmp_path / "adapters" / "probe-itsm"
    tree.mkdir(parents=True)
    (tree / "manifest.yaml").write_text(
        yaml.safe_dump(PROBE_ITSM, sort_keys=False), encoding="utf-8"
    )

    db_path = tmp_path / "itsm.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    settings = make_test_settings(
        f"sqlite+aiosqlite:///{db_path}", adapters_dir=str(tmp_path / "adapters")
    )
    clock = FakeClock()
    engine = create_async_engine(settings.db_url, poolclass=NullPool)
    container = deps.Container(settings=settings, engine=engine, clock=clock)
    StubItsmAdapter.reset()
    container.adapter_runtimes.register("probe-itsm", StubItsmAdapter)
    app.dependency_overrides[deps.get_container] = lambda: container
    try:
        with TestClient(app) as client:
            yield ApiCtx(client=client, container=container, clock=clock, db_path=db_path)
    finally:
        app.dependency_overrides.clear()
        StubItsmAdapter.reset()


# --- the shipped queue --------------------------------------------------------


@pytest.mark.req("REQ-043", "REQ-033")
async def test_declaring_raises_a_ticket_and_closing_closes_it(api):
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    connect(api, owner_token, "itsm-lite", app_id=APP_ID)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]

    incident = declare(api, token, severity="P1", summary="payments are failing")
    ticket = incident["ticket"]
    assert ticket["state"] == "synced"
    assert ticket["status"] == "in_progress"
    assert ticket["reference"].startswith("LITE-")
    assert ticket["adapter_id"] == "itsm-lite"

    # A re-grade reaches the ticket without moving its status.
    set_severity(api, token, incident["id"], "P0", reason="worse")
    assert read_incident(api, token, incident["id"])["ticket"]["status"] == "in_progress"

    # Recovering is not resolution: the ticket stays in progress while the
    # mitigation is being validated.
    set_state(api, token, incident["id"], "recovering", reason="rolled back")
    assert read_incident(api, token, incident["id"])["ticket"]["status"] == "in_progress"

    closed = close_incident(api, token, incident["id"], reason="validated")
    assert closed["ticket"]["status"] == "closed", (
        "in_progress -> closed skips a state the ITSM requires, so the sync "
        "resolves it first"
    )
    assert closed["ticket"]["state"] == "synced"

    synced = topics(api, owner_token, "incident.itsm.synced")
    assert synced, "raising and closing a ticket is part of what the incident was"
    assert all(event["plane"] == "incident" for event in synced)
    assert all(event["actor"]["type"] == "system" for event in synced)


@pytest.mark.req("REQ-043")
async def test_an_incident_is_declared_even_with_no_itsm_bound(api):
    """Partial completion. The declaration commits before the ticket is
    attempted, so an application with no ticket queue still gets an incident -
    and the row says exactly why there is no ticket."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]

    incident = declare(api, token, severity="P2")
    assert incident["state"] == "active"
    assert incident["ticket"]["state"] == "not_configured"
    assert "no ITSM connector is bound" in incident["ticket"]["detail"]


# --- the failure paths --------------------------------------------------------


@pytest.mark.req("REQ-043", "REQ-062")
async def test_a_create_that_timed_out_is_uncertain_and_the_retry_reconciles(itsm_api):
    """Uncertain external state, and reconcile-before-retry.

    A create whose answer was lost may or may not have made a ticket. Retrying
    it blindly is how an incident ends up with two; treating it as done is how it
    ends up with none. So the link is `uncertain`, and the next attempt asks the
    queue what it holds before it creates anything.
    """
    api = itsm_api
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    connect(api, owner_token, "probe-itsm", app_id=APP_ID)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]

    # The create lands in the queue and the answer is lost on the way back.
    real_create = StubItsmAdapter.create

    async def create_then_lose_the_answer(self, ticket):
        await real_create(self, ticket)
        raise AdapterTimeoutError("probe-itsm.create did not answer within 10.0s")

    StubItsmAdapter.create = create_then_lose_the_answer
    try:
        incident = declare(api, token, severity="P0")
    finally:
        StubItsmAdapter.create = real_create

    assert incident["state"] == "active", "the incident is declared regardless"
    assert incident["ticket"]["state"] == "uncertain"
    assert "may or may not exist" in incident["ticket"]["detail"]
    assert len(StubItsmAdapter.tickets) == 1

    uncertain = topics(api, owner_token, "incident.itsm.sync.uncertain")
    assert len(uncertain) == 1

    # The retry sweep reconciles: it finds the ticket that was already there and
    # adopts it rather than creating a second one.
    sweep = await api.container.incident_tickets.retry_pending()
    assert sweep.synced == 1
    assert len(StubItsmAdapter.tickets) == 1, "reconcile before retry, not two tickets"
    settled = read_incident(api, token, incident["id"])["ticket"]
    assert settled["state"] == "synced"
    assert settled["reference"] == "PRB-0001"


@pytest.mark.req("REQ-043")
async def test_a_refused_ticket_is_a_failed_link_that_the_sweep_recovers(itsm_api):
    """Model/adapter failure. The ITSM said no, with a reason; the incident is
    unaffected, the reason is on the row, and the sweep picks it up once the
    queue is working again."""
    api = itsm_api
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    connect(api, owner_token, "probe-itsm", app_id=APP_ID)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]

    StubItsmAdapter.behaviour["create"] = AdapterError("ticket queue is read-only today")
    incident = declare(api, token, severity="P1")
    assert incident["state"] == "active"
    assert incident["ticket"]["state"] == "failed"
    assert "read-only today" in incident["ticket"]["detail"]
    assert topics(api, owner_token, "incident.itsm.sync.failed")

    StubItsmAdapter.behaviour.pop("create")
    sweep = await api.container.incident_tickets.retry_pending()
    assert sweep.synced == 1
    assert read_incident(api, token, incident["id"])["ticket"]["state"] == "synced"


@pytest.mark.req("REQ-043", "REQ-012")
async def test_a_person_can_force_the_sync_and_it_is_their_own_permission(itsm_api):
    """Human override of the retry cadence."""
    api = itsm_api
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    connect(api, owner_token, "probe-itsm", app_id=APP_ID)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]

    StubItsmAdapter.behaviour["create"] = AdapterError("down for maintenance")
    incident = declare(api, token, severity="P1")
    assert incident["ticket"]["state"] == "failed"

    sme = api.invite_and_accept(
        owner_token, email="sme@acme.io", roles=["sme"], display_name="Sam"
    )
    resync(api, sme["tokens"]["access_token"], incident["id"], expect=403)

    StubItsmAdapter.behaviour.pop("create")
    assert resync(api, token, incident["id"])["ticket"]["state"] == "synced"


@pytest.mark.req("REQ-043", "REQ-012")
async def test_a_connector_disabled_mid_incident_leaves_the_incident_intact(itsm_api):
    """Permission change mid-flow, in its integration form: the binding is
    disabled between two lifecycle moves. The move still happens."""
    api = itsm_api
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    binding = connect(api, owner_token, "probe-itsm", app_id=APP_ID)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]

    incident = declare(api, token, severity="P0")
    assert incident["ticket"]["state"] == "synced"

    disabled = api.client.patch(
        f"/connectors/{binding['id']}",
        json={"enabled": False},
        headers=api.auth_header(owner_token),
    )
    assert disabled.status_code == 200, disabled.text

    closed = close_incident(api, token, incident["id"], reason="done")
    assert closed["state"] == "closed", "the incident closes whatever the ITSM does"
    assert closed["ticket"]["state"] == "not_configured"


