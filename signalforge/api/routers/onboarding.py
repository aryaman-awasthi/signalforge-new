"""/apps: the onboarding wizard's seven steps (REQ-036, REQ-012).

Every write is manage_apps work, and the application is named in the *path*
rather than in the query string - which is why the RBAC dependency reads
`app_id` from the path parameters as well: an operator whose manage_apps binding
is scoped to one application must be evaluated against that application, not
org-wide. The two plain reads (the list and one application) are tenant-scoped
and filtered by the caller's bindings instead, so the people an invite names for
an application can open it.

The routes are thin. Every rule that matters - the policy floor, topology
idempotency, what a connection test is allowed to call - lives in the domain and
the application services, so that a second caller (a seed, a drill, an agent)
cannot reach the same state by a different door.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Response

from signalforge.api import deps
from signalforge.api.schemas.onboarding import (
    ActionPatchIn,
    ActionsImportIn,
    ActionsImportOut,
    ApplicationDetailOut,
    ApplicationIn,
    ApplicationPatchIn,
    ApplicationSummaryOut,
    BaselineIn,
    BaselineRunOut,
    ConnectionTestIn,
    ConnectionTestOut,
    DiscoverIn,
    DiscoveryOut,
    GovernanceIn,
    MappingIn,
    ProfileOut,
    ReadinessOut,
    TestPageOut,
    TopologyIn,
    TopologyOut,
    TopologyUploadOut,
    actions_import_out,
    application_detail_out,
    application_summary_out,
    baseline_run_out,
    connection_test_out,
    discovery_out,
    profile_out,
    readiness_out,
    test_page_out,
    topology_version_out,
)
from signalforge.application.identity.principal import Principal
from signalforge.contracts.roles import Permission
from signalforge.domain.onboarding.codec import (
    journey_from_mapping,
    probe_from_mapping,
    severity_matrix_from_mapping,
    signal_from_mapping,
    slo_from_mapping,
)
from signalforge.domain.onboarding.profile import SeverityMatrix

router = APIRouter(prefix="/apps", tags=["onboarding"])


# --- basics -------------------------------------------------------------------


@router.get("", response_model=list[ApplicationSummaryOut])
async def list_applications(
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """The applications this person's bindings cover.

    A tenant-scoped read rather than `manage_apps`, like the incident list and
    the lifecycle route: the Incident Commander and the SME hold no
    `manage_apps` in the frozen matrix, and they are the people an invite names
    for an application. The service filters by binding - org-wide sees all,
    app-scoped sees its own - and every write below still carries the permission.
    """
    views = await container.applications.list(principal)
    summaries = []
    for view in views:
        _app, _profile, readiness = await container.readiness.readiness(
            principal, view.application.app_id
        )
        summaries.append(application_summary_out(view, readiness))
    return summaries


@router.post("", response_model=ApplicationDetailOut, status_code=201)
async def register_application(
    body: ApplicationIn,
    principal: Principal = Depends(deps.require(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    view = await container.applications.register(
        principal,
        app_id=body.app_id,
        name=body.name,
        tier=body.tier,
        environment=body.environment,
        base_url=body.base_url,
        description=body.description,
        owner_team=body.owner_team,
    )
    _app, _profile, readiness = await container.readiness.readiness(
        principal, view.application.app_id
    )
    return application_detail_out(view, readiness)


@router.get("/{app_id}", response_model=ApplicationDetailOut)
async def get_application(
    app_id: str,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """One application, readable by anybody whose binding names it. A binding
    scoped to another application is refused with the reason, in the same
    tenant, so the fix is in the message."""
    view = await container.applications.get(principal, app_id)
    _app, _profile, readiness = await container.readiness.readiness(principal, app_id)
    return application_detail_out(view, readiness)


@router.patch("/{app_id}", response_model=ApplicationDetailOut)
async def update_application(
    app_id: str,
    body: ApplicationPatchIn,
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    view = await container.applications.update(
        principal,
        app_id,
        name=body.name,
        tier=body.tier,
        environment=body.environment,
        base_url=body.base_url,
        description=body.description,
        owner_team=body.owner_team,
        clear_base_url=body.clear_base_url,
    )
    _app, _profile, readiness = await container.readiness.readiness(principal, app_id)
    return application_detail_out(view, readiness)


@router.delete("/{app_id}", status_code=204, response_class=Response)
async def remove_application(
    app_id: str,
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    await container.applications.remove(principal, app_id)
    return Response(status_code=204)


# --- connect ------------------------------------------------------------------


@router.post("/{app_id}/discover", response_model=DiscoveryOut)
async def discover(
    app_id: str,
    body: DiscoverIn,
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    """Probe the application's base URL for the frozen observe contract, and the
    machine's well-known monitoring ports, without a credential and without
    writing a binding."""
    return discovery_out(await container.discovery.discover(principal, app_id))


@router.post("/{app_id}/connection-tests", response_model=list[ConnectionTestOut])
async def run_connection_tests(
    app_id: str,
    body: ConnectionTestIn,
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    results = await container.connection_tests.test(
        principal, app_id, binding_id=body.binding_id
    )
    return [connection_test_out(result) for result in results]


# --- map ----------------------------------------------------------------------


@router.put("/{app_id}/mapping", response_model=ProfileOut)
async def set_mapping(
    app_id: str,
    body: MappingIn,
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    profile = await container.onboarding.set_mapping(
        principal,
        app_id,
        signals=(
            None
            if body.signals is None
            else [signal_from_mapping(item.model_dump()) for item in body.signals]
        ),
        region_label=body.region_label,
        regions=body.regions,
        journeys=(
            None
            if body.journeys is None
            else [journey_from_mapping(item.model_dump()) for item in body.journeys]
        ),
        probes=(
            None
            if body.probes is None
            else [probe_from_mapping(item.model_dump()) for item in body.probes]
        ),
        clear_region_label=body.clear_region_label,
    )
    return profile_out(profile)


@router.post("/{app_id}/actions/import", response_model=ActionsImportOut)
async def import_actions(
    app_id: str,
    body: ActionsImportIn,
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    """Import the actions the application publishes in its own manifest. The
    import never makes anything executable: a high-risk class arrives requiring
    approval and can never be moved to auto."""
    result = await container.onboarding.import_actions(
        principal, app_id, binding_id=body.binding_id
    )
    return actions_import_out(app_id, result)


@router.patch("/{app_id}/actions/{action_id}", response_model=ProfileOut)
async def override_action(
    app_id: str,
    action_id: str,
    body: ActionPatchIn,
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    profile = await container.onboarding.override_action(
        principal,
        app_id,
        action_id,
        risk_override=body.risk_override,
        clear_risk_override=body.clear_risk_override,
        approver_roles=body.approver_roles,
        execution_mode=body.execution_mode,
        enabled=body.enabled,
    )
    return profile_out(profile)


# --- topology -----------------------------------------------------------------


@router.put("/{app_id}/topology", response_model=TopologyUploadOut)
async def upload_topology(
    app_id: str,
    body: TopologyIn,
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    upload = await container.onboarding.upload_topology(
        principal, app_id, body.document, binding_id=body.binding_id
    )
    return TopologyUploadOut(
        app_id=app_id,
        version=upload.version.version,
        unchanged=upload.unchanged,
        node_count=upload.version.node_count,
        edge_count=upload.version.edge_count,
        normalized_by=upload.version.normalized_by,
        imported_at=upload.version.imported_at,
    )


@router.get("/{app_id}/topology", response_model=TopologyOut)
async def get_topology(
    app_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    latest, versions = await container.onboarding.topology_versions(
        principal, app_id, limit=limit
    )
    return TopologyOut(
        app_id=app_id,
        version=latest.version if latest else None,
        snapshot=latest.snapshot if latest else None,
        versions=[topology_version_out(version) for version in versions],
    )


# --- SLOs, severity and policy defaults ---------------------------------------


@router.put("/{app_id}/governance", response_model=ProfileOut)
async def set_governance(
    app_id: str,
    body: GovernanceIn,
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    """SLO targets, the severity matrix and the policy defaults. The floor is
    enforced on the merged policy, so a partial edit cannot widen it."""
    severity: Optional[SeverityMatrix] = None
    if body.severity_matrix is not None:
        severity = severity_matrix_from_mapping(body.severity_matrix.model_dump())
    profile = await container.onboarding.set_governance(
        principal,
        app_id,
        slos=(
            None
            if body.slos is None
            else [slo_from_mapping(item.model_dump()) for item in body.slos]
        ),
        severity_matrix=severity,
        policy_patch=(
            None
            if body.policy is None
            else body.policy.model_dump(exclude_none=True)
        ),
    )
    return profile_out(profile)


# --- baseline and test --------------------------------------------------------


@router.post("/{app_id}/baseline", response_model=BaselineRunOut)
async def learn_baseline(
    app_id: str,
    body: BaselineIn,
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    run = await container.onboarding.learn_baselines(
        principal, app_id, samples=body.samples, binding_id=body.binding_id
    )
    return baseline_run_out(app_id, run)


@router.post("/{app_id}/test", response_model=TestPageOut)
async def run_test_page(
    app_id: str,
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    """Everything at once: every connector tested in its own direction, every
    journey probe run in every declared region, and the readiness score
    recomputed from what came back."""
    return test_page_out(await container.readiness.run_test_page(principal, app_id))


@router.get("/{app_id}/readiness", response_model=ReadinessOut)
async def get_readiness(
    app_id: str,
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    _app, _profile, readiness = await container.readiness.readiness(principal, app_id)
    return readiness_out(app_id, readiness)

