"""The Basics step: registering an application (REQ-036).

Registration writes four things in one transaction - the application row, its
onboarding profile with the policy floor already in place, the audit entry and
the event. The profile is written *now*, not lazily at the first wizard step,
because the policy defaults are part of the floor: an application that existed
for ten minutes with no policy row is an application for which "high-risk cannot
auto-execute" was a promise rather than a stored fact.

Removal is refused while integrations still point at the application. A binding
whose app_id names nothing is an orphan the health loop keeps polling and no
screen can explain, so the operator is told which connectors to remove first.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Sequence

from signalforge.application.events.bus import EventBus
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.application.onboarding.common import (
    APP_REGISTERED_TOPIC,
    APP_REMOVED_TOPIC,
    APP_UPDATED_TOPIC,
    load_profile,
    record,
    require_application,
)
from signalforge.domain.identity.errors import PermissionDeniedError
from signalforge.domain.identity.ports import Clock
from signalforge.domain.identity.rbac import RBACDecisionService
from signalforge.domain.onboarding.applications import (
    Application,
    AppTier,
    Environment,
    validate_app_id,
)
from signalforge.domain.onboarding.errors import DuplicateApplicationError
from signalforge.domain.onboarding.profile import OnboardingProfile, empty_profile
from signalforge.domain.shared.errors import ConflictError


@dataclass(frozen=True)
class ApplicationView:
    application: Application
    profile: OnboardingProfile


class ApplicationService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        bus: EventBus,
        clock: Clock,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        # Set by the composition root (S4-T01). Removing an application has to
        # remove what the detector believes about it too, or the next sweep
        # keeps evaluating signals for something that is gone - but onboarding
        # must not import detection to know that, so the hook is a callback the
        # container wires rather than a dependency this module holds.
        self.on_removed: list[Callable[[str, str], Awaitable[Any]]] = []

    # -- reads -------------------------------------------------------------
    #
    # Reads are scoped by *membership*, not by permission (REQ-012). The frozen
    # matrix gives the SME nothing and the Incident Commander no `manage_apps`,
    # and those are exactly the people an invite names for an application: a
    # list they could not open would hide the one thing they were invited to
    # look at. An org-wide binding sees the whole estate; an app-scoped binding
    # sees the applications it names. Writes still carry `manage_apps` on the
    # route, so nothing here lets a reader change anything.

    async def list(self, principal: Principal) -> Sequence[ApplicationView]:
        visible = RBACDecisionService.visible_apps(principal.bindings)
        async with self._uow_factory() as uow:
            applications = await uow.applications.list(principal.tenant_id)
            return [
                ApplicationView(
                    application=application,
                    profile=await load_profile(
                        uow, principal.tenant_id, application.app_id
                    ),
                )
                for application in applications
                if visible is None or application.app_id in visible
            ]

    async def get(self, principal: Principal, app_id: str) -> ApplicationView:
        visible = RBACDecisionService.visible_apps(principal.bindings)
        if visible is not None and app_id not in visible:
            # The same tenant, so saying why is safe (REQ-013 is about other
            # tenants) and a refusal that names the fix is the useful one.
            raise PermissionDeniedError(
                f"your roles are bound to {', '.join(sorted(visible)) or 'no application'}, "
                f"not to {app_id}; ask the organisation owner to widen the binding"
            )
        async with self._uow_factory() as uow:
            application = await require_application(uow, principal.tenant_id, app_id)
            profile = await load_profile(uow, principal.tenant_id, app_id)
        return ApplicationView(application=application, profile=profile)

    # -- writes ------------------------------------------------------------

    async def register(
        self,
        principal: Principal,
        *,
        app_id: str,
        name: str,
        tier: AppTier = AppTier.TIER_2,
        environment: Environment = Environment.PRODUCTION,
        base_url: Optional[str] = None,
        description: str = "",
        owner_team: str = "",
    ) -> ApplicationView:
        now = self._clock.now()
        application = Application(
            id=str(uuid.uuid4()),
            tenant_id=principal.tenant_id,
            app_id=validate_app_id(app_id),
            name=name,
            tier=tier,
            environment=environment,
            base_url=base_url,
            description=description,
            owner_team=owner_team,
            created_at=now,
            created_by=principal.user_id,
            updated_at=now,
        )
        profile = empty_profile(principal.tenant_id, application.app_id)
        profile = profile.with_governance(at=now)

        async with self._uow_factory() as uow:
            existing = await uow.applications.get(
                principal.tenant_id, application.app_id
            )
            if existing is not None:
                raise DuplicateApplicationError(
                    f"application {application.app_id} is already registered"
                )
            await uow.applications.add(application)
            await uow.onboarding.put(profile)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=application.app_id,
                topic=APP_REGISTERED_TOPIC,
                payload=_payload(application),
            )
            await uow.commit()
        return ApplicationView(application=application, profile=profile)

    async def update(
        self,
        principal: Principal,
        app_id: str,
        *,
        name: Optional[str] = None,
        tier: Optional[AppTier] = None,
        environment: Optional[Environment] = None,
        base_url: Optional[str] = None,
        description: Optional[str] = None,
        owner_team: Optional[str] = None,
        clear_base_url: bool = False,
    ) -> ApplicationView:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            application = await require_application(uow, principal.tenant_id, app_id)
            before = application.base_url
            edited = application.edited(
                at=now,
                name=name,
                tier=tier,
                environment=environment,
                base_url="" if clear_base_url else base_url,
                description=description,
                owner_team=owner_team,
            )
            await uow.applications.update(edited)
            payload = _payload(edited) | {
                # The base URL is what discovery walks and what pre-fills every
                # adapter's config, so a change to it is worth naming on the
                # timeline rather than being buried in "updated".
                "base_url_changed": before != edited.base_url,
                "previous_base_url": before,
            }
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=edited.app_id,
                topic=APP_UPDATED_TOPIC,
                payload=payload,
            )
            profile = await load_profile(uow, principal.tenant_id, app_id)
            await uow.commit()
        return ApplicationView(application=edited, profile=profile)

    async def remove(self, principal: Principal, app_id: str) -> None:
        async with self._uow_factory() as uow:
            application = await require_application(uow, principal.tenant_id, app_id)
            bindings = await uow.connectors.list(principal.tenant_id, app_id=app_id)
            keys = [
                key
                for key in await uow.ingest_keys.list(principal.tenant_id, app_id=app_id)
                if key.active
            ]
            if bindings or keys:
                raise ConflictError(
                    f"application {app_id} still has {len(bindings)} connector(s) and "
                    f"{len(keys)} active ingest key(s); remove them first so nothing "
                    "is left polling or posting into an application that is gone"
                )
            removed_versions = await uow.topologies.delete_all(
                principal.tenant_id, app_id
            )
            await uow.onboarding.delete(principal.tenant_id, app_id)
            await uow.applications.delete(principal.tenant_id, app_id)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=app_id,
                topic=APP_REMOVED_TOPIC,
                payload=_payload(application)
                | {"topology_versions_removed": removed_versions},
            )
            await uow.commit()
        # After the commit, never inside it: these hooks write to a different
        # database (rollups.db), and a derived store that failed to clean up
        # must not roll back the removal the operator asked for.
        for hook in self.on_removed:
            await hook(principal.tenant_id, app_id)


def _payload(application: Application) -> dict[str, Any]:
    return {
        "app_id": application.app_id,
        "name": application.name,
        "tier": application.tier.value,
        "environment": application.environment.value,
        "base_url": application.base_url,
        "owner_team": application.owner_team,
    }
