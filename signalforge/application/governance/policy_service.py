"""The policy editor's use cases (REQ-060).

Four things happen here and each one is a decision this platform has to be able
to defend six months later:

1. **Reading** the policy in force, plus what it *means* for every action the
   application published - because a policy screen that shows settings without
   showing their consequences is a screen nobody can check.
2. **Dry-running** a candidate document. Same engine, no write. The point is that
   "what would this change do" and "what does this do" are literally the same
   code path, so they cannot diverge.
3. **Editing**, which writes a new version rather than mutating one. The author,
   the time and the note are columns, and the previous version stays readable.
4. **Refusing**, loudly. A document that contradicts the platform floor never
   becomes a version - and the refusal is written to the audit chain as
   `policy.floor.rejected` before the error is raised, because "somebody tried to
   make rollbacks auto-execute on the 14th" is exactly the sentence a security
   review is looking for.

## The mid-incident rule

Editing a policy while an incident is open on that application is refused unless
the editor confirms. Changing the rules under a running response is sometimes
right - an incident is exactly when you discover the approver list is wrong - and
it must never be accidental. The confirmation is a field on the request, the
confirmed edit is audited as `policy.edit.mid_incident`, and the incidents it
affects are named in the payload.

Separately, and more importantly: an *already-approved* high-risk action is not
re-interpreted by the new policy. The approval carries the version it was granted
under and the gateway refuses it if the version has moved (`Approval
.assert_valid_now`). A policy edit therefore invalidates outstanding approvals
rather than silently re-authorising them under different rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from signalforge.application.events.bus import EventBus
from signalforge.application.governance.common import (
    POLICY_CREATED_TOPIC,
    POLICY_DRY_RUN_TOPIC,
    POLICY_FLOOR_REJECTED_TOPIC,
    POLICY_MID_INCIDENT_TOPIC,
    publish_system,
    record,
)
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.domain.governance.errors import (
    ActiveIncidentConfirmationRequired,
    PolicyFloorViolation,
    PolicyNotFoundError,
)
from signalforge.domain.governance.policy import (
    FLOOR_VERSION,
    MAX_APPROVAL_TTL_S,
    MIN_APPROVAL_TTL_S,
    APPROVE_HIGH_RISK_ROLES,
    PolicyDecision,
    PolicyDocument,
    PolicyEngine,
    RiskClass,
    classify_action,
    default_policy,
)
from signalforge.domain.governance.ports import PolicyCodec
from signalforge.domain.identity.ports import Clock
from signalforge.domain.incidents.lifecycle import LifecycleState
from signalforge.domain.onboarding.errors import ApplicationNotFoundError
from signalforge.domain.onboarding.profile import MappedAction, OnboardingProfile


@dataclass(frozen=True)
class ActionPolicyView:
    """One action, its classification and what the engine says about it now.

    `locked` is what the editor renders as an uneditable row. It is computed
    here from the domain classification rather than sent as a UI hint, so the
    browser and the backend cannot disagree about which rows are the floor's.
    """

    action: MappedAction
    decision: PolicyDecision
    locked: bool
    locked_reason: str


@dataclass(frozen=True)
class PolicyView:
    policy: PolicyDocument
    source: str
    actions: tuple[ActionPolicyView, ...]
    versions: tuple[PolicyDocument, ...]
    floor: Mapping[str, Any]
    open_incidents: tuple[str, ...]


@dataclass(frozen=True)
class DryRunResult:
    """What a candidate would do, beside what the current policy does."""

    candidate: Optional[PolicyDocument]
    accepted: bool
    error: str
    floor_rule: str
    before: tuple[ActionPolicyView, ...]
    after: tuple[ActionPolicyView, ...]
    changes: tuple[Mapping[str, Any], ...]


FLOOR_DESCRIPTION: Mapping[str, Any] = {
    "floor_version": FLOOR_VERSION,
    "max_approval_ttl_s": MAX_APPROVAL_TTL_S,
    "min_approval_ttl_s": MIN_APPROVAL_TTL_S,
    "approve_high_risk_roles": sorted(r.value for r in APPROVE_HIGH_RISK_ROLES),
    "rules": [
        {
            "rule_id": "FLOOR-HIGH-RISK-NO-AUTO",
            "statement": "rollback, restart, failover, traffic shift, config "
            "change and customer-impacting actions can never auto-execute",
        },
        {
            "rule_id": "FLOOR-HIGH-RISK-APPROVER",
            "statement": "their approvers must hold approve_high_risk in the "
            "frozen roles matrix",
        },
        {
            "rule_id": "FLOOR-READ-ONLY-NO-MUTATE",
            "statement": "nothing that mutates can be classified read-only",
        },
        {
            "rule_id": "FLOOR-RISK-NEVER-LOWERED",
            "statement": "an override may raise an action's risk, never lower it",
        },
        {
            "rule_id": "FLOOR-APPROVAL-TTL-CAP",
            "statement": f"an approval expires within {MAX_APPROVAL_TTL_S} seconds",
        },
        {
            "rule_id": "FLOOR-AUDIT-IMMUTABLE",
            "statement": "audit cannot be disabled, so it is not a setting",
        },
    ],
}
"""Published to the editor so the locked rows can state *why* they are locked in
the floor's own words, and to the API document so an integrator reads the same
list. Derived from the constants rather than retyped."""


class PolicyService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        bus: EventBus,
        clock: Clock,
        codec: PolicyCodec,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        self._codec = codec
        self._metrics = metrics

    # -- reads ---------------------------------------------------------------

    async def current(self, tenant_id: str, app_id: str) -> PolicyDocument:
        """The policy in force, seeding the default if nobody has edited one.

        The default is *returned*, not stored: an application that never had its
        policy touched should not accumulate a version-1 row that says nothing,
        and the default is a pure function so reading it twice gives the same
        answer.
        """
        async with self._uow_factory() as uow:
            stored = await uow.policies.latest(tenant_id, app_id)
        if stored is not None:
            return stored
        return default_policy(
            tenant_id, app_id, at=self._clock.now(), author_id="platform"
        )

    async def view(self, principal: Principal, app_id: str) -> PolicyView:
        async with self._uow_factory() as uow:
            await self._require_app(uow, principal.tenant_id, app_id)
            stored = await uow.policies.latest(principal.tenant_id, app_id)
            versions = tuple(
                await uow.policies.history(principal.tenant_id, app_id, limit=25)
            )
            profile = await uow.onboarding.get(principal.tenant_id, app_id)
            open_incidents = await self._open_incidents(uow, principal.tenant_id, app_id)
        policy = stored or default_policy(
            principal.tenant_id, app_id, at=self._clock.now(), author_id="platform"
        )
        return PolicyView(
            policy=policy,
            source=self._codec.dump(policy),
            actions=_evaluate(profile, policy),
            versions=versions,
            floor=FLOOR_DESCRIPTION,
            open_incidents=open_incidents,
        )

    async def history(
        self, principal: Principal, app_id: str, *, limit: int = 50
    ) -> Sequence[PolicyDocument]:
        async with self._uow_factory() as uow:
            await self._require_app(uow, principal.tenant_id, app_id)
            return list(
                await uow.policies.history(principal.tenant_id, app_id, limit=limit)
            )

    # -- dry run -------------------------------------------------------------

    async def dry_run(
        self, principal: Principal, app_id: str, *, source: str
    ) -> DryRunResult:
        """Evaluate a candidate without writing anything.

        A rejected candidate is still a result rather than an exception: the
        editor has to show the operator *which* floor rule refused it, beside the
        current decisions, and a 422 with a string would lose the comparison.
        The refusal is audited exactly as a real edit's refusal is - an attempt
        to weaken the floor is interesting whether or not it was a rehearsal.
        """
        async with self._uow_factory() as uow:
            await self._require_app(uow, principal.tenant_id, app_id)
            profile = await uow.onboarding.get(principal.tenant_id, app_id)
            stored = await uow.policies.latest(principal.tenant_id, app_id)
        current = stored or default_policy(
            principal.tenant_id, app_id, at=self._clock.now(), author_id="platform"
        )
        before = _evaluate(profile, current)
        try:
            candidate = self._parse(
                principal, app_id, source=source, version=current.version + 1
            )
            _assert_against_profile(candidate, profile)
        except PolicyFloorViolation as exc:
            await self._audit_floor_rejection(
                principal, app_id, source=source, error=str(exc), dry_run=True
            )
            return DryRunResult(
                candidate=None,
                accepted=False,
                error=str(exc),
                floor_rule=_floor_rule_of(str(exc)),
                before=before,
                after=(),
                changes=(),
            )
        after = _evaluate(profile, candidate)
        async with self._uow_factory() as uow:
            await publish_system(
                uow,
                self._bus,
                self._clock,
                tenant_id=principal.tenant_id,
                app_id=app_id,
                incident_id=None,
                topic=POLICY_DRY_RUN_TOPIC,
                payload={
                    "app_id": app_id,
                    "by": principal.user_id,
                    "candidate_checksum": candidate.checksum,
                    "current_version": current.version,
                    "changes": len(_changes(before, after)),
                },
                actor_id=principal.user_id,
            )
            await uow.commit()
        return DryRunResult(
            candidate=candidate,
            accepted=True,
            error="",
            floor_rule="",
            before=before,
            after=after,
            changes=_changes(before, after),
        )

    # -- edit ----------------------------------------------------------------

    async def edit(
        self,
        principal: Principal,
        app_id: str,
        *,
        source: str,
        note: str = "",
        confirm_active_incident: bool = False,
    ) -> PolicyDocument:
        """Write version n+1, or refuse and say why."""
        async with self._uow_factory() as uow:
            await self._require_app(uow, principal.tenant_id, app_id)
            profile = await uow.onboarding.get(principal.tenant_id, app_id)
            stored = await uow.policies.latest(principal.tenant_id, app_id)
            open_incidents = await self._open_incidents(uow, principal.tenant_id, app_id)
        current = stored or default_policy(
            principal.tenant_id, app_id, at=self._clock.now(), author_id="platform"
        )
        version = current.version + 1 if stored is not None else 1

        try:
            candidate = self._parse(
                principal, app_id, source=source, version=version, note=note
            )
            _assert_against_profile(candidate, profile)
        except PolicyFloorViolation as exc:
            await self._audit_floor_rejection(
                principal, app_id, source=source, error=str(exc), dry_run=False
            )
            raise

        if open_incidents and not confirm_active_incident:
            raise ActiveIncidentConfirmationRequired(
                f"{len(open_incidents)} incident(s) on {app_id} are still open "
                f"({', '.join(open_incidents[:3])}). Editing the policy now "
                "changes the rules under a running response and invalidates any "
                "approval already granted under the current version. Re-send with "
                "confirm_active_incident to proceed."
            )

        async with self._uow_factory() as uow:
            await uow.policies.add(candidate)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=app_id,
                incident_id=None,
                topic=POLICY_CREATED_TOPIC,
                payload={
                    "app_id": app_id,
                    "version": candidate.version,
                    "previous_version": current.version if stored else None,
                    "checksum": candidate.checksum,
                    "author_id": principal.user_id,
                    "note": note,
                    "approval_ttl_s": candidate.approval_ttl_s,
                    "blocked_classes": sorted(
                        c.value for c in candidate.blocked_classes
                    ),
                    "action_rules": sorted(candidate.action_rules),
                    "floor_version": FLOOR_VERSION,
                },
            )
            if open_incidents:
                await record(
                    uow,
                    self._bus,
                    self._clock,
                    principal=principal,
                    app_id=app_id,
                    incident_id=open_incidents[0],
                    topic=POLICY_MID_INCIDENT_TOPIC,
                    payload={
                        "app_id": app_id,
                        "version": candidate.version,
                        "open_incidents": list(open_incidents),
                        "confirmed_by": principal.user_id,
                        "effect": "approvals granted under version "
                        f"{current.version} no longer authorise execution",
                    },
                )
            await uow.commit()
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_policy_versions_total", {"app": app_id}
            )
        return candidate

    async def get_version(
        self, principal: Principal, app_id: str, version: int
    ) -> PolicyDocument:
        async with self._uow_factory() as uow:
            document = await uow.policies.get(principal.tenant_id, app_id, version)
        if document is None:
            raise PolicyNotFoundError(
                f"{app_id} has no policy version {version} in this tenant"
            )
        return document

    # -- internals -----------------------------------------------------------

    def _parse(
        self,
        principal: Principal,
        app_id: str,
        *,
        source: str,
        version: int,
        note: str = "",
    ) -> PolicyDocument:
        return self._codec.parse(
            source,
            tenant_id=principal.tenant_id,
            app_id=app_id,
            version=version,
            at=self._clock.now(),
            author_id=principal.user_id,
            author_label=principal.display_name or "",
            note=note,
        )

    async def _audit_floor_rejection(
        self,
        principal: Principal,
        app_id: str,
        *,
        source: str,
        error: str,
        dry_run: bool,
    ) -> None:
        """The refusal goes on the record before the error goes to the caller.

        Its own transaction, so it survives the rollback of whatever the caller
        does with the exception - a refusal that vanished because the request
        failed would defeat the purpose of recording it.
        """
        async with self._uow_factory() as uow:
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=app_id,
                incident_id=None,
                topic=POLICY_FLOOR_REJECTED_TOPIC,
                payload={
                    "app_id": app_id,
                    "rejected_by": "platform-safety-floor",
                    "floor_version": FLOOR_VERSION,
                    "floor_rule": _floor_rule_of(error),
                    "reason": error,
                    "attempted_by": principal.user_id,
                    "dry_run": dry_run,
                    # The submitted document, bounded. An auditor needs to see
                    # what was attempted, not just that something was.
                    "attempted_source": source[:2_000],
                },
            )
            await uow.commit()
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_policy_floor_rejections_total",
                {"app": app_id, "rule": _floor_rule_of(error)},
            )

    @staticmethod
    async def _require_app(uow: IdentityUnitOfWork, tenant_id: str, app_id: str) -> None:
        application = await uow.applications.get(tenant_id, app_id)
        if application is None:
            raise ApplicationNotFoundError(f"application {app_id} not found")

    @staticmethod
    async def _open_incidents(
        uow: IdentityUnitOfWork, tenant_id: str, app_id: str
    ) -> tuple[str, ...]:
        incidents = await uow.incidents.list(tenant_id, app_id=app_id, limit=100)
        return tuple(
            incident.reference or incident.id
            for incident in incidents
            if incident.state in (LifecycleState.ACTIVE, LifecycleState.RECOVERING)
        )


def _evaluate(
    profile: Optional[OnboardingProfile], policy: PolicyDocument
) -> tuple[ActionPolicyView, ...]:
    """Every mapped action, with the decision the engine reaches for it now."""
    actions = tuple(profile.actions) if profile is not None else ()
    views: list[ActionPolicyView] = []
    for action in sorted(actions, key=lambda a: a.action_id):
        decision = PolicyEngine.decide(action, policy)
        high_risk = classify_action(action) is RiskClass.HIGH_RISK
        views.append(
            ActionPolicyView(
                action=action,
                decision=decision,
                locked=high_risk,
                locked_reason=(
                    f"{action.action_class.value} is a high-risk class: it can "
                    "never auto-execute and always requires approval by a role "
                    "holding approve_high_risk. This row is the platform safety "
                    "floor and is not editable."
                    if high_risk
                    else ""
                ),
            )
        )
    return tuple(views)


def _assert_against_profile(
    candidate: PolicyDocument, profile: Optional[OnboardingProfile]
) -> None:
    if profile is not None:
        candidate.assert_against_actions(profile.actions)


def _changes(
    before: Sequence[ActionPolicyView], after: Sequence[ActionPolicyView]
) -> tuple[Mapping[str, Any], ...]:
    """What the candidate would change, per action. The dry-run's whole output."""
    prior = {view.action.action_id: view for view in before}
    changes: list[Mapping[str, Any]] = []
    for view in after:
        was = prior.get(view.action.action_id)
        if was is None:
            continue
        if was.decision.outcome is view.decision.outcome and (
            was.decision.approval_ttl_s == view.decision.approval_ttl_s
        ):
            continue
        changes.append(
            {
                "action_id": view.action.action_id,
                "name": view.action.name,
                "from": was.decision.outcome.value,
                "to": view.decision.outcome.value,
                "from_ttl_s": was.decision.approval_ttl_s,
                "to_ttl_s": view.decision.approval_ttl_s,
                "reason": view.decision.reason,
            }
        )
    return tuple(changes)


def _floor_rule_of(message: str) -> str:
    """The rule id out of a floor error message, for the audit payload's label."""
    for token in message.split():
        if token.startswith("FLOOR-"):
            return token.rstrip(":")
    return "FLOOR"
