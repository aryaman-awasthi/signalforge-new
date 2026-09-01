"""What every onboarding use case does identically.

Each wizard step is one commit of three things - the change, its audit entry and
its event - so that the timeline and the audit chain can never disagree with
what the platform is actually watching. That pattern is here once rather than in
five services, together with the tenant-scoped lookup that makes a cross-tenant
read answer the same 404 as a read of nothing.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from signalforge.application.events.bus import EventBus
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.contracts.envelope import (
    Actor,
    ActorType,
    Authority,
    EventEnvelope,
    Plane,
    Source,
)
from signalforge.domain.identity.ports import Clock
from signalforge.domain.onboarding.applications import Application
from signalforge.domain.onboarding.errors import ApplicationNotFoundError
from signalforge.domain.onboarding.profile import OnboardingProfile, empty_profile

SOURCE_SYSTEM = "onboarding"

APP_REGISTERED_TOPIC = "system.app.registered"
APP_UPDATED_TOPIC = "system.app.updated"
APP_REMOVED_TOPIC = "system.app.removed"
MAPPING_TOPIC = "system.app.mapping.updated"
ACTIONS_TOPIC = "system.app.actions.updated"
GOVERNANCE_TOPIC = "system.app.governance.updated"
POLICY_REFUSED_TOPIC = "system.app.policy.refused"
TOPOLOGY_TOPIC = "topology.updated"
BASELINE_TOPIC = "system.app.baseline.learned"
CONNECTION_TEST_TOPIC = "system.connector.tested"
DISCOVERY_TOPIC = "system.app.discovered"
READINESS_TOPIC = "system.app.tested"


async def require_application(
    uow: IdentityUnitOfWork, tenant_id: str, app_id: str
) -> Application:
    application = await uow.applications.get(tenant_id, app_id)
    if application is None:
        # Identical whether it belongs to another tenant or to nobody: a 404
        # must not confirm that an app_id exists elsewhere (REQ-013).
        raise ApplicationNotFoundError(f"application {app_id} not found")
    return application


async def load_profile(
    uow: IdentityUnitOfWork, tenant_id: str, app_id: str
) -> OnboardingProfile:
    """The profile, or the defaults.

    A profile row is written when the application is registered, so the fallback
    is only reached for an application registered by an older build - and it
    returns the same defaults that registration would have written, floor
    included, rather than a blank aggregate.
    """
    profile = await uow.onboarding.get(tenant_id, app_id)
    return profile if profile is not None else empty_profile(tenant_id, app_id)


async def record(
    uow: IdentityUnitOfWork,
    bus: EventBus,
    clock: Clock,
    *,
    principal: Principal,
    app_id: str,
    topic: str,
    payload: Mapping[str, Any],
    plane: Plane = Plane.SIGNAL,
    actor_type: ActorType = ActorType.HUMAN,
    incident_id: Optional[str] = None,
) -> None:
    """One audit entry and one event, inside the caller's transaction.

    Onboarding facts live on the signal plane: how an application was wired six
    months before an incident is configuration history, not part of what the
    incident was, and the incident plane is what gets sealed.
    """
    await uow.audit.append(
        tenant_id=principal.tenant_id,
        topic=topic,
        actor_type=actor_type.value,
        actor_id=principal.user_id,
        app_id=app_id,
        payload=dict(payload),
    )
    await bus.publish(
        uow,
        EventEnvelope(
            topic=topic,
            tenant_id=principal.tenant_id,
            app_id=app_id,
            incident_id=incident_id,
            plane=plane,
            occurred_at=clock.now(),
            actor=Actor(type=actor_type, id=principal.user_id),
            source=Source(
                system=SOURCE_SYSTEM,
                authority=Authority.AUTHORITATIVE,
            ),
            payload=dict(payload),
        ),
    )
