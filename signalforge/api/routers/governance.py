"""Governance and safe execution (S6-T01..T05).

The permission on each route is the interesting part of this file, so here it is
in one table:

    route                                                   guard
    ----------------------------------------------------------------------------
    GET  /apps/{id}/policy                                  tenant-scoped read
    GET  /apps/{id}/policy/versions                         read_audit
    POST /apps/{id}/policy                                  edit_policies
    POST /apps/{id}/policy/dry-run                          edit_policies
    GET  /incidents/{id}/actions                            tenant-scoped read
    POST /incidents/{id}/plan                               declare_incident
    GET  /incidents/{id}/executions                         tenant-scoped read
    POST /incidents/{id}/executions                         declare_incident
    GET  /incidents/{id}/executions/{eid}                   tenant-scoped read
    POST /incidents/{id}/executions/{eid}/approve           approve_high_risk
    POST /incidents/{id}/executions/{eid}/deny              approve_high_risk
    POST /incidents/{id}/executions/{eid}/revoke            approve_high_risk
    POST /incidents/{id}/executions/{eid}/run               declare_incident
    POST /incidents/{id}/executions/{eid}/reconcile         declare_incident
    POST /incidents/{id}/executions/{eid}/retry             declare_incident
    POST /incidents/{id}/executions/{eid}/compensate        declare_incident
    POST /incidents/{id}/executions/{eid}/cancel            declare_incident
    POST /incidents/{id}/executions/{eid}/takeover          declare_incident
    GET  /incidents/{id}/communications                     tenant-scoped read
    POST /incidents/{id}/communications                     tenant-scoped write
    POST /incidents/{id}/communications/{cid}/approve       audience permission
    POST /incidents/{id}/communications/{cid}/issue         audience permission
    POST /incidents/{id}/communications/{cid}/correction    tenant-scoped write
    GET  /incidents/{id}/validation                         tenant-scoped read
    POST /incidents/{id}/validation                         declare_incident

Three of those deserve their reasoning stated rather than assumed.

**Approving is separated from requesting.** `POST .../executions` carries
`declare_incident` - incident command work, like paging and task assignment.
`.../approve` carries `approve_high_risk`, which the frozen matrix gives only to
the Incident Commander and the Tech Lead. Two permissions because they are two
decisions by two people, and `Approval.granted` refuses an approval by the
person who requested it whatever permissions they hold.

**Drafting a communication carries no permission; approving one does.** The SME
holds nothing in the roles matrix and is the person most likely to know what the
customer-facing symptom actually is. What they cannot do is approve or issue: the
audience decides which permission that needs, and the service checks it against
the audience rather than the route - a customer status update approved by
somebody holding only `approve_exec_comms` is refused inside
`CommunicationService.approve`, which is where the audience is known.

**Reading is open to the tenant, writing is not.** Same reasoning as the war room
and the brain (DR-071): a paged responder who cannot see the approval countdown,
the ledger or the closure gate is a responder who will ask in chat instead.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from signalforge.api import deps
from signalforge.api.schemas.governance import (
    CommsOut,
    CommunicationIn,
    CommunicationOut,
    CorrectionIn,
    DryRunIn,
    DryRunOut,
    ExecutionIn,
    ExecutionOut,
    GatewayOut,
    LedgerOut,
    NbaOut,
    NoteIn,
    PlanIn,
    PlanOut,
    PolicyIn,
    PolicyOut,
    PolicyVersionOut,
    TakeoverIn,
    ValidationIn,
    ValidationOut,
    ValidationViewOut,
    approval_out,
    comms_out,
    communication_out,
    decision_out,
    dry_run_out,
    execution_out,
    gateway_out,
    nba_out,
    plan_out,
    policy_out,
    policy_version_out,
    validation_out,
    validation_view_out,
)
from signalforge.application.identity.principal import Principal
from signalforge.contracts.roles import Permission
from signalforge.domain.governance.comms import Audience
from signalforge.domain.governance.errors import GovernanceValidationError
from signalforge.domain.onboarding.profile import ExecutionMode

router = APIRouter(tags=["governance"])


def _audience(raw: str) -> Audience:
    try:
        return Audience(str(raw).strip().lower())
    except ValueError as exc:
        raise GovernanceValidationError(
            f"{raw!r} is not an audience; one of "
            f"{[a.value for a in Audience]}"
        ) from exc


def _intent(raw: Optional[str]) -> Optional[ExecutionMode]:
    if raw is None:
        return None
    try:
        return ExecutionMode(str(raw).strip().lower())
    except ValueError as exc:
        raise GovernanceValidationError(
            f"{raw!r} is not an execution intent; one of "
            f"{[m.value for m in ExecutionMode]}"
        ) from exc


# --- policy -------------------------------------------------------------------


@router.get("/apps/{app_id}/policy", response_model=PolicyOut)
async def get_policy(
    app_id: str,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """The policy in force, what it means for every published action, and the
    immutable floor above which it sits.

    `locked` on a row is the domain's answer, not the browser's: the six
    high-risk classes can never auto-execute, the editor renders those rows
    uneditable because of this field, and the backend refuses the edit anyway.
    """
    return policy_out(await container.policies.view(principal, app_id))


@router.get(
    "/apps/{app_id}/policy/versions", response_model=list[PolicyVersionOut]
)
async def get_policy_versions(
    app_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(deps.require_app(Permission.READ_AUDIT)),
    container: deps.Container = Depends(deps.get_container),
):
    """Every version, newest first, each naming its author.

    `read_audit` rather than `edit_policies`: the Auditor's whole job is to read
    what changed and who changed it, and an audit trail only the editor can see
    is not an audit trail.
    """
    versions = await container.policies.history(principal, app_id, limit=limit)
    return [policy_version_out(version) for version in versions]


@router.post("/apps/{app_id}/policy", response_model=PolicyOut, status_code=201)
async def edit_policy(
    app_id: str,
    body: PolicyIn,
    principal: Principal = Depends(deps.require_app(Permission.EDIT_POLICIES)),
    container: deps.Container = Depends(deps.get_container),
):
    """Write a new policy version.

    A document that contradicts the platform safety floor is refused with 422
    *and* recorded as `policy.floor.rejected` in the audit chain first - because
    the error response is seen only by the person who tried, and "somebody tried
    to make rollbacks auto-execute" is exactly what a security review looks for.

    An open incident on this application requires `confirm_active_incident`, and
    a confirmed mid-incident edit invalidates approvals granted under the
    previous version rather than silently re-interpreting them.
    """
    await container.policies.edit(
        principal,
        app_id,
        source=body.source,
        note=body.note,
        confirm_active_incident=body.confirm_active_incident,
    )
    return policy_out(await container.policies.view(principal, app_id))


@router.post("/apps/{app_id}/policy/dry-run", response_model=DryRunOut)
async def dry_run_policy(
    app_id: str,
    body: DryRunIn,
    principal: Principal = Depends(deps.require_app(Permission.EDIT_POLICIES)),
    container: deps.Container = Depends(deps.get_container),
):
    """What a candidate document would do, evaluated by the engine that decides.

    Same code path as a real decision, so "what would this change" and "what
    does this do" cannot diverge. A candidate the floor refuses comes back with
    `accepted: false` and the rule that refused it, rather than as an error - the
    editor has to show it beside the current decisions.
    """
    return dry_run_out(
        await container.policies.dry_run(principal, app_id, source=body.source)
    )


# --- next best action -----------------------------------------------------------


@router.get("/incidents/{incident_id}/actions", response_model=NbaOut)
async def get_next_best_actions(
    incident_id: str,
    explain: bool = Query(
        default=True,
        description="Ask the model for a one-sentence rationale per action. The "
        "ranking is identical either way - the score is arithmetic and is "
        "computed before any model is called.",
    ),
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """The ranked actions, with the score broken into its four terms.

    Actions that cannot be performed right now are returned in `ineligible` with
    the reason, never as the primary recommendation: showing an impossible action
    at the top of a war-room screen is how fifteen minutes get spent discovering
    it is impossible.
    """
    return nba_out(await container.nba.recommend(principal, incident_id, explain=explain))


@router.get("/incidents/{incident_id}/plan", response_model=list[PlanOut])
async def get_plans(
    incident_id: str,
    limit: int = Query(default=10, ge=1, le=50),
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """Plan versions, newest first. The diff triad reads the top two."""
    plans = await container.nba.plans(principal, incident_id, limit=limit)
    return [plan_out(plan) for plan in plans]


@router.post("/incidents/{incident_id}/plan", response_model=NbaOut, status_code=201)
async def revise_plan(
    incident_id: str,
    body: PlanIn,
    principal: Principal = Depends(
        deps.require_incident(Permission.DECLARE_INCIDENT)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """Write plan version n+1, marking every action kept, paused or added.

    Never a replacement. The previous version stays readable and the assumption
    that changed travels with the new one.
    """
    return nba_out(
        await container.nba.revise(
            principal,
            incident_id,
            changed_assumptions=body.changed_assumptions,
            note=body.note,
        )
    )


# --- the Tool Gateway -----------------------------------------------------------


@router.get("/incidents/{incident_id}/executions", response_model=LedgerOut)
async def get_ledger(
    incident_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """Every attempt to change this application's estate during this incident.

    `retry_blocked` travels beside `state`: a client must not offer retry on an
    uncertain outcome, because the target may have applied it after SignalForge
    stopped waiting.
    """
    executions, approvals = await container.gateway.ledger(
        principal, incident_id, limit=limit
    )
    now = container.clock.now()
    return LedgerOut(
        executions=[execution_out(record) for record in executions],
        approvals=[approval_out(approval, now=now) for approval in approvals],
    )


@router.post(
    "/incidents/{incident_id}/executions", response_model=GatewayOut, status_code=201
)
async def request_execution(
    incident_id: str,
    body: ExecutionIn,
    principal: Principal = Depends(
        deps.require_incident(Permission.DECLARE_INCIDENT)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """Ask for an action. The one entry point to every consequential call.

    Arguments are validated against the schema the application published, the
    policy engine decides, and the decision is recorded with its `rules_hit`
    before anything happens. A high-risk action returns `approval_required` and
    creates the approval request; asking for `intent: auto` on one returns a
    recorded BLOCK naming the floor rule.
    """
    result = await container.gateway.request(
        principal,
        incident_id,
        action_id=body.action_id,
        args=body.args,
        intent=_intent(body.intent),
        note=body.note,
        run=body.run,
    )
    return gateway_out(result, now=container.clock.now())


@router.get(
    "/incidents/{incident_id}/executions/{execution_id}", response_model=GatewayOut
)
async def get_execution(
    incident_id: str,
    execution_id: str,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    execution, approvals = await container.gateway.get(
        principal, incident_id, execution_id
    )
    now = container.clock.now()
    granted = next((a for a in approvals if a.state.open), None)
    return GatewayOut(
        execution=execution_out(execution),
        decision=decision_out(container.gateway.decision_of(execution)),
        approval=approval_out(granted, now=now) if granted else None,
        duplicate=False,
    )


@router.post(
    "/incidents/{incident_id}/executions/{execution_id}/approve",
    response_model=GatewayOut,
)
async def approve_execution(
    incident_id: str,
    execution_id: str,
    body: NoteIn,
    principal: Principal = Depends(
        deps.require_incident(Permission.APPROVE_HIGH_RISK)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """A person holding approve_high_risk says yes, for a bounded time.

    The approval is bound to this execution and to a fingerprint over the action,
    its arguments, its risk class and the policy version - so it authorises this
    request and nothing else, and it stops authorising anything the moment any of
    those change.
    """
    result = await container.gateway.approve(
        principal, incident_id, execution_id, note=body.note
    )
    return gateway_out(result, now=container.clock.now())


@router.post(
    "/incidents/{incident_id}/executions/{execution_id}/deny",
    response_model=GatewayOut,
)
async def deny_execution(
    incident_id: str,
    execution_id: str,
    body: NoteIn,
    principal: Principal = Depends(
        deps.require_incident(Permission.APPROVE_HIGH_RISK)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    result = await container.gateway.deny(
        principal, incident_id, execution_id, note=body.note
    )
    return gateway_out(result, now=container.clock.now())


@router.post(
    "/incidents/{incident_id}/executions/{execution_id}/revoke",
    response_model=GatewayOut,
)
async def revoke_approval(
    incident_id: str,
    execution_id: str,
    body: NoteIn,
    principal: Principal = Depends(
        deps.require_incident(Permission.APPROVE_HIGH_RISK)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """Withdraw an approval that has not been spent. Saying no later has to be
    as easy as saying yes was."""
    result = await container.gateway.revoke(
        principal, incident_id, execution_id, note=body.note
    )
    return gateway_out(result, now=container.clock.now())


@router.post(
    "/incidents/{incident_id}/executions/{execution_id}/run",
    response_model=GatewayOut,
)
async def run_execution(
    incident_id: str,
    execution_id: str,
    principal: Principal = Depends(
        deps.require_incident(Permission.DECLARE_INCIDENT)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """Send an approved execution.

    Everything is re-checked here and nothing is trusted from the request that
    created the row: the policy version in force now, the approval's expiry, the
    fingerprint, and whether the approver still holds the permission.
    """
    result = await container.gateway.execute(principal, incident_id, execution_id)
    return gateway_out(result, now=container.clock.now())


@router.post(
    "/incidents/{incident_id}/executions/{execution_id}/reconcile",
    response_model=GatewayOut,
)
async def reconcile_execution(
    incident_id: str,
    execution_id: str,
    principal: Principal = Depends(
        deps.require_incident(Permission.DECLARE_INCIDENT)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """Ask the target system what actually happened.

    The only door out of `uncertain`. Resolves to `completed_late` (do not
    repeat), `not_applied` (a retry is safe) or `partial` (compensate, continue
    or take over), and only `not_applied` clears `retry_blocked`.
    """
    result = await container.gateway.reconcile(principal, incident_id, execution_id)
    return gateway_out(result, now=container.clock.now())


@router.post(
    "/incidents/{incident_id}/executions/{execution_id}/retry",
    response_model=GatewayOut,
)
async def retry_execution(
    incident_id: str,
    execution_id: str,
    principal: Principal = Depends(
        deps.require_incident(Permission.DECLARE_INCIDENT)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """A fresh attempt at something that provably did not happen.

    Refused with 409 while the previous outcome is unknown, and the refusal is
    audited: retrying an unreconciled rollback is exactly the moment this control
    exists for.
    """
    result = await container.gateway.retry(principal, incident_id, execution_id)
    return gateway_out(result, now=container.clock.now())


@router.post(
    "/incidents/{incident_id}/executions/{execution_id}/compensate",
    response_model=GatewayOut,
)
async def compensate_execution(
    incident_id: str,
    execution_id: str,
    body: NoteIn,
    principal: Principal = Depends(
        deps.require_incident(Permission.DECLARE_INCIDENT)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """Undo what landed, through the action's declared reverse action.

    A compensation is a full execution in its own right and goes through the same
    policy decision and approval gate: undoing a failover is as consequential as
    doing one.
    """
    result = await container.gateway.compensate(
        principal, incident_id, execution_id, note=body.note
    )
    return gateway_out(result, now=container.clock.now())


@router.post(
    "/incidents/{incident_id}/executions/{execution_id}/cancel",
    response_model=GatewayOut,
)
async def cancel_execution(
    incident_id: str,
    execution_id: str,
    body: NoteIn,
    principal: Principal = Depends(
        deps.require_incident(Permission.DECLARE_INCIDENT)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    result = await container.gateway.cancel(
        principal, incident_id, execution_id, note=body.note
    )
    return gateway_out(result, now=container.clock.now())


@router.post(
    "/incidents/{incident_id}/executions/{execution_id}/takeover",
    response_model=GatewayOut,
)
async def take_over_execution(
    incident_id: str,
    execution_id: str,
    body: TakeoverIn,
    principal: Principal = Depends(
        deps.require_incident(Permission.DECLARE_INCIDENT)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """A person is doing the rest by hand, and says what they are doing.

    The honest terminal state for an execution the platform cannot resolve. The
    note is required: "a human took over" with no record of what they did is
    worse than no state at all.
    """
    result = await container.gateway.take_over(
        principal, incident_id, execution_id, note=body.note
    )
    return gateway_out(result, now=container.clock.now())


# --- communications ---------------------------------------------------------------


@router.get("/incidents/{incident_id}/communications", response_model=CommsOut)
async def get_communications(
    incident_id: str,
    audience: Optional[str] = Query(default=None),
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """The board, having first checked whether anything on it has stopped being
    true.

    The contradiction sweep runs on read as well as on the bus: a contradiction
    that only appeared after a background job would let somebody open the screen
    and see a statement the platform already knew was wrong.
    """
    return comms_out(
        await container.comms.list(
            principal,
            incident_id,
            audience=_audience(audience) if audience else None,
        )
    )


@router.post(
    "/incidents/{incident_id}/communications",
    response_model=CommunicationOut,
    status_code=201,
)
async def draft_communication(
    incident_id: str,
    body: CommunicationIn,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """Write a draft, grounded on this incident's graph nodes.

    A draft, always. There is no parameter that issues one, and the audience's
    approval permission is checked when somebody approves it - not here, because
    drafting is the part a paged SME should be able to do.
    """
    return communication_out(
        await container.comms.draft(
            principal,
            incident_id,
            audience=_audience(body.audience),
            subject=body.subject,
            body=body.body,
            node_ids=body.node_ids,
            use_model=body.use_model,
        )
    )


@router.post(
    "/incidents/{incident_id}/communications/{communication_id}/approve",
    response_model=CommunicationOut,
)
async def approve_communication(
    incident_id: str,
    communication_id: str,
    body: NoteIn,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """A named person with the audience's permission says the text is right.

    The permission is not on the route because the *audience* decides which one
    applies and the route has not read the communication yet. It is enforced in
    the service, against the audience, with `approve_customer_comms` for anything
    customer-facing and `approve_exec_comms` for leadership.
    """
    return communication_out(
        await container.comms.approve(
            principal, incident_id, communication_id, note=body.note
        )
    )


@router.post(
    "/incidents/{incident_id}/communications/{communication_id}/issue",
    response_model=CommunicationOut,
)
async def issue_communication(
    incident_id: str,
    communication_id: str,
    body: NoteIn,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """Publish. A person, always - there is no scheduler behind this route and no
    configuration that could add one."""
    return communication_out(
        await container.comms.issue(
            principal, incident_id, communication_id, note=body.note
        )
    )


@router.post(
    "/incidents/{incident_id}/communications/{communication_id}/correction",
    response_model=CommunicationOut,
    status_code=201,
)
async def draft_correction(
    incident_id: str,
    communication_id: str,
    body: CorrectionIn,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """A new draft carrying a redline against what was published.

    The issued text is never edited. The redline is computed from the two bodies
    rather than typed, so a correction cannot claim to change one thing while
    changing another, and it needs its own approval like any other communication.
    """
    return communication_out(
        await container.comms.draft_correction(
            principal,
            incident_id,
            communication_id,
            subject=body.subject,
            body=body.body,
            node_ids=body.node_ids,
            reason=body.reason,
            use_model=body.use_model,
        )
    )


# --- layered validation ------------------------------------------------------------


@router.get("/incidents/{incident_id}/validation", response_model=ValidationViewOut)
async def get_validation(
    incident_id: str,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """The four layers, the stability window and whether closure is locked.

    Every check names the system it was read from, and none of them is the
    Incident Brain: recovery is checked against the application, never against
    the platform's own reasoning about it.
    """
    return validation_view_out(
        await container.validation.view(principal, incident_id),
        now=container.clock.now(),
    )


@router.post(
    "/incidents/{incident_id}/validation",
    response_model=ValidationOut,
    status_code=201,
)
async def run_validation(
    incident_id: str,
    body: ValidationIn,
    principal: Principal = Depends(
        deps.require_incident(Permission.DECLARE_INCIDENT)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """Run one validation cycle against the application.

    A cycle that does not confirm recovery writes its failure into the incident
    graph as evidence, returns the incident to `active` if it was `recovering`,
    increments the failed-cycle count and escalates after the number of cycles
    this application's policy allows.
    """
    return validation_out(
        await container.validation.run(principal, incident_id, note=body.note),
        now=container.clock.now(),
    )
