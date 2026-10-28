"""/incidents and the two lifecycle reads under /apps (S4-T02, REQ-043, REQ-044).

Reads and writes are guarded differently here, and the reason is in the frozen
roles matrix rather than in a preference. `ROLE_PERMISSIONS[Role.SME]` is empty:
the person most likely to be paged holds no permission at all. Putting one on
the read path would lock the incident out of exactly the people it exists to
reach, so the reads are session-scoped to the caller's own tenant and carry no
permission - the same decision `/ws` records as DR-031 - while every write names
one of the three incident permissions the matrix does define:

    POST   /incidents                        declare_incident
    PATCH  /incidents/{id}/severity          change_severity
    POST   /incidents/{id}/state             declare_incident
    POST   /incidents/{id}/close             close_incident
    POST   /incidents/{id}/itsm-sync         declare_incident

Declaring names its application in the query string rather than the path, so the
existing `deps.require` factory evaluates a per-application role binding against
it; the incident-scoped writes read the application off the incident through
`deps.require_incident`.

The two `/apps/{app_id}` reads are the app-detail screen's stance and its
change-window chip, and they follow the same rule as the incident reads rather
than the `/apps` rule: session-scoped to the caller's tenant, no permission. The
S4 browser walkthrough is what settled it (DR-089). `manage_apps` belongs only
to the org owner in the frozen matrix, and the incident commander - the one role
that may declare - never holds it, so guarding these two with it meant the only
person allowed to declare could not open the screen the Declare button is on,
while the only person who could open it was not allowed to declare. Every write
under `/apps` still carries `manage_apps`.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from signalforge.api import deps
from signalforge.api.schemas.incidents import (
    ChangeWindowOut,
    CloseIn,
    DeclareIn,
    IncidentDetailOut,
    IncidentListOut,
    IncidentOut,
    LifecycleOut,
    SeverityIn,
    StateIn,
    change_window_out,
    incident_detail_out,
    incident_list_out,
    incident_out,
    lifecycle_out,
)
from signalforge.application.identity.principal import Principal
from signalforge.contracts.roles import Permission

router = APIRouter(tags=["incidents"])
apps_router = APIRouter(prefix="/apps", tags=["incidents"])


# --- declaring ----------------------------------------------------------------


@router.post("/incidents", response_model=IncidentDetailOut, status_code=201)
async def declare_incident(
    body: DeclareIn,
    app_id: str = Query(description="The application this incident is about"),
    principal: Principal = Depends(
        deps.require(Permission.DECLARE_INCIDENT, app_id_from_query=True)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """Declare an incident. The only way one comes into existence.

    Detection can reach a corroborated proposal and stops there; this is the act
    that turns it into an incident, and it is always a person's. The severity
    detection proposed is accepted when none is given, and recorded as `chosen`
    when one is - both are human decisions, and the record says which.
    """
    view = await container.incidents.declare(
        principal,
        app_id,
        title=body.title,
        summary=body.summary,
        severity=body.severity,
        reason=body.reason,
    )
    return incident_detail_out(view)


# --- reading ------------------------------------------------------------------


@router.get("/incidents", response_model=IncidentListOut)
async def list_incidents(
    app_id: Optional[str] = Query(default=None),
    state: Optional[str] = Query(
        default=None, description="A lifecycle state, or `open` for active plus recovering"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """The tenant's incidents. Readable by every member of the tenant, because
    the role that answers a page holds no permission in the frozen matrix."""
    return incident_list_out(
        await container.incidents.list(
            principal, app_id=app_id, state=state, limit=limit
        )
    )


@router.get("/incidents/{incident_id}", response_model=IncidentDetailOut)
async def get_incident(
    incident_id: str,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """One incident with its whole transition history: who moved it, when, and
    what they said."""
    return incident_detail_out(await container.incidents.get(principal, incident_id))


# --- running it ---------------------------------------------------------------


@router.patch("/incidents/{incident_id}/severity", response_model=IncidentOut)
async def change_severity(
    incident_id: str,
    body: SeverityIn,
    principal: Principal = Depends(
        deps.require_incident(Permission.CHANGE_SEVERITY)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """Re-grade an open incident. Nothing else in the platform can: detection
    proposes a severity and never sets one."""
    view = await container.incidents.change_severity(
        principal, incident_id, body.severity, reason=body.reason
    )
    return incident_out(view.incident)


@router.post("/incidents/{incident_id}/state", response_model=IncidentOut)
async def set_incident_state(
    incident_id: str,
    body: StateIn,
    principal: Principal = Depends(
        deps.require_incident(Permission.DECLARE_INCIDENT)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """Move an incident to recovering, or back to active after a regression."""
    view = await container.incidents.set_state(
        principal, incident_id, body.state, reason=body.reason
    )
    return incident_out(view.incident)


@router.post("/incidents/{incident_id}/close", response_model=IncidentOut)
async def close_incident(
    incident_id: str,
    body: CloseIn,
    principal: Principal = Depends(deps.require_incident(Permission.CLOSE_INCIDENT)),
    container: deps.Container = Depends(deps.get_container),
):
    """Close an incident. Terminal: a regression afterwards is a new declaration
    by a person at a new time, which is what the record should say."""
    view = await container.incidents.close(principal, incident_id, reason=body.reason)
    return incident_out(view.incident)


@router.post("/incidents/{incident_id}/itsm-sync", response_model=IncidentOut)
async def resync_ticket(
    incident_id: str,
    principal: Principal = Depends(
        deps.require_incident(Permission.DECLARE_INCIDENT)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """Try the ITSM again now instead of waiting for the retry sweep. Reconciles
    before it creates, so a link left uncertain by a timeout cannot become a
    second ticket."""
    view = await container.incidents.resync_ticket(principal, incident_id)
    return incident_out(view.incident)


# --- the application's own view -----------------------------------------------


@apps_router.get("/{app_id}/lifecycle", response_model=LifecycleOut)
async def get_lifecycle(
    app_id: str,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """Where this application sits: a detection stance, or an incident somebody
    declared - and the response says which of the two it is reading."""
    return lifecycle_out(await container.incidents.lifecycle(principal, app_id))


@apps_router.get("/{app_id}/change-window", response_model=ChangeWindowOut)
async def get_change_window(
    app_id: str,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """Changes announced as started and never announced as completed - the chip
    beside the health score, sourced from real deploy-hook calls."""
    return change_window_out(await container.changes.read(principal, app_id))
