"""`system.llm.degraded` on the bus, and the wiring that puts it there (REQ-051).

The provider's unit tests prove it *tells* somebody when it degrades. These
prove the other half: that the composition root wires a listener which publishes
the frozen topic onto the event backbone, on the right retention plane, with a
payload an auditor can read and no credential in it - and that a running node
with no model key is a working node.
"""

from __future__ import annotations

import httpx
import pytest

from signalforge.contracts.envelope import Plane, Topics
from signalforge.domain.llm.entities import (
    CompletionRequest,
    DegradedNotice,
    DegradedReason,
    Message,
    ModelClass,
)
from signalforge.domain.observability.health import PLATFORM_TENANT_ID
from signalforge.infrastructure.llm.embeddings import GatewayEmbeddingProvider
from signalforge.tests.llm.conftest import FakeGatewayTransport, provider

pytestmark = pytest.mark.anyio

SECRET = "sk-a-real-looking-secret-value"


def notice(**overrides) -> DegradedNotice:
    fields = dict(
        reason=DegradedReason.TIMEOUT,
        purpose="join-brief",
        model_class=ModelClass.GENERAL,
        model="a-configured-model",
        provider="genailab",
        detail="gateway timed out after 20.0s",
    )
    fields.update(overrides)
    return DegradedNotice(**fields)


async def events_of(api, tenant_id: str):
    async with api.container.event_uow_factory() as uow:
        return list(await uow.events.read(tenant_id=tenant_id, limit=50))


@pytest.mark.req("REQ-051")
async def test_a_degradation_lands_on_the_tenants_timeline(api):
    owner = api.signup()
    tenant_id = owner["org"]["id"]

    await api.container.llm_degradation.on_degraded(
        notice(tenant_id=tenant_id, incident_id="inc-1")
    )

    published = [e for e in await events_of(api, tenant_id) if e.topic == Topics.LLM_DEGRADED]
    assert len(published) == 1
    event = published[0]
    assert event.incident_id == "inc-1"
    # Inside an incident it is part of that incident's record, so it is kept
    # and sealed with it rather than ageing out as signal.
    assert event.plane is Plane.INCIDENT
    assert event.payload["reason"] == "timeout"
    assert event.payload["model_class"] == "general"
    assert event.payload["deterministic_fallback"] is True
    assert event.actor.type.value == "system"


@pytest.mark.req("REQ-051")
async def test_a_degradation_outside_an_incident_is_platform_signal(api):
    await api.container.llm_degradation.on_degraded(notice())
    published = [
        e for e in await events_of(api, PLATFORM_TENANT_ID) if e.topic == Topics.LLM_DEGRADED
    ]
    assert len(published) == 1 and published[0].plane is Plane.SIGNAL


@pytest.mark.req("REQ-051")
async def test_a_continuing_outage_is_one_entry_not_a_flood(api):
    """An outage can degrade a hundred calls a minute. The timeline an incident
    commander is reading must not become a log of the thing that broke."""
    owner = api.signup()
    tenant_id = owner["org"]["id"]

    for _ in range(5):
        await api.container.llm_degradation.on_degraded(notice(tenant_id=tenant_id))
    assert len([e for e in await events_of(api, tenant_id) if e.topic == Topics.LLM_DEGRADED]) == 1

    # A different failure is a different fact and is announced immediately.
    await api.container.llm_degradation.on_degraded(
        notice(tenant_id=tenant_id, reason=DegradedReason.UPSTREAM_ERROR)
    )
    assert len([e for e in await events_of(api, tenant_id) if e.topic == Topics.LLM_DEGRADED]) == 2

    # ...and a still-broken gateway is re-announced once the window passes, so
    # a long outage stays visible.
    api.clock.advance(seconds=61)
    await api.container.llm_degradation.on_degraded(notice(tenant_id=tenant_id))
    assert len([e for e in await events_of(api, tenant_id) if e.topic == Topics.LLM_DEGRADED]) == 3


@pytest.mark.req("REQ-051", "REQ-015")
async def test_a_gateway_that_echoes_the_key_cannot_write_it_into_the_audit_chain(api):
    """The end-to-end secret claim, driven through the real path: a gateway
    error body containing the credential, a real provider, the container's real
    listener, and the event as it lands in the outbox."""
    owner = api.signup()
    tenant_id = owner["org"]["id"]
    gateway = FakeGatewayTransport(
        responses=[
            httpx.Response(
                401, json={"error": {"message": f"key {SECRET} is not authorised"}}
            )
        ]
    )
    subject = provider(
        gateway,
        listener=api.container.llm_degradation,
        env={"GENAILAB_API_KEY": SECRET},
    )

    await subject.complete(
        CompletionRequest(
            purpose="join-brief",
            messages=(Message(role="user", content="what happened"),),
            tenant_id=tenant_id,
        )
    )

    published = [e for e in await events_of(api, tenant_id) if e.topic == Topics.LLM_DEGRADED]
    assert len(published) == 1
    assert SECRET not in str(published[0].payload)
    assert "***" in published[0].payload["detail"]
    assert SECRET not in str(api.audit_rows(tenant_id))


@pytest.mark.req("REQ-051")
async def test_a_node_with_no_model_key_boots_and_degrades(api):
    """The end-to-end claim: nothing in the composition root needs a gateway."""
    container = api.container
    assert container.llm is not None and not container.llm.configured
    assert container.llm.status["endpoint"] == "https://genailab.tcs.in/v1"

    owner = api.signup()
    result = await container.llm.complete(
        CompletionRequest(
            purpose="join-brief",
            messages=(Message(role="user", content="what happened"),),
            fallback="assembled from the incident graph",
            tenant_id=owner["org"]["id"],
        )
    )
    assert result.degraded and result.reason is DegradedReason.UNCONFIGURED
    assert result.text == "assembled from the incident graph"

    published = [
        e for e in await events_of(api, owner["org"]["id"]) if e.topic == Topics.LLM_DEGRADED
    ]
    assert len(published) == 1 and published[0].payload["reason"] == "unconfigured"


@pytest.mark.req("REQ-051", "REQ-035")
async def test_kb_index_is_wired_to_the_same_gateway(api):
    """Chat and embeddings must not be able to drift apart: one provider, one
    endpoint, one degraded event."""
    embeddings = api.container.adapter_services.embeddings
    assert isinstance(embeddings, GatewayEmbeddingProvider)
    assert embeddings.model == api.container.llm.models[ModelClass.EMBEDDINGS]
    scoped = embeddings.for_tenant("tenant-a")
    assert isinstance(scoped, GatewayEmbeddingProvider) and scoped.model == embeddings.model
