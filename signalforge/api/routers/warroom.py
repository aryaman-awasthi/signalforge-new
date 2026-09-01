"""The war-room surface (S4-T03, REQ-045).

The permission split here is the most deliberate one in the API, and it exists
because of a fact in the frozen roles matrix: `ROLE_PERMISSIONS[Role.SME]` is
empty. The person most likely to be paged holds nothing. So the acts that *are*
that person - answering a page, joining the room, speaking on the bridge,
ticking their own task - cannot be behind a permission without locking the war
room's whole purpose out of it.

They are not therefore unguarded. Each is **self-scoped**: the actor is the
session and can never be a parameter, and the entity refuses the act when it
names somebody else. That is a stronger control than a permission would be, not
a weaker one - `declare_incident` would let *any* holder acknowledge *anybody's*
page, whereas `Page.acknowledged` refuses everyone but the addressee.

    write                                          guard
    ------------------------------------------------------------------
    POST   /incidents/{id}/pages                   declare_incident
    POST   /incidents/{id}/pages/{pid}/escalate    declare_incident
    POST   /incidents/{id}/tasks                   declare_incident
    PATCH  /incidents/{id}/tasks/{tid}             declare_incident
    POST   /incidents/{id}/pages/{pid}/ack         self-scoped
    POST   /incidents/{id}/war-room/join           self-scoped
    POST   /incidents/{id}/bridge                  self-scoped
    POST   /incidents/{id}/tasks/{tid}/done        self-scoped

The four self-scoped writes are the only session writes in this platform that
name no permission besides `POST /auth/logout`, and each is pinned by name in
signalforge/tests/security/test_warroom_boundary.py together with the assertion
that it refuses to act on anybody else's behalf.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from signalforge.api import deps
from signalforge.api.schemas.warroom import (
    AckIn,
    BridgeOut,
    BridgePostIn,
    BriefOut,
    JoinIn,
    PageIn,
    PageListOut,
    PageOut,
    TaskDoneIn,
    TaskIn,
    TaskListOut,
    TaskOut,
    TaskPatchIn,
    WarRoomOut,
    brief_out,
    bridge_out,
    page_list_out,
    page_out,
    task_list_out,
    task_out,
    war_room_out,
)
from signalforge.application.identity.principal import Principal
from signalforge.contracts.roles import Permission, Role
from signalforge.domain.warroom.errors import WarRoomValidationError

router = APIRouter(tags=["war-room"])


# --- the room -----------------------------------------------------------------


@router.get("/incidents/{incident_id}/war-room", response_model=WarRoomOut)
async def get_war_room(
    incident_id: str,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """The room: who should be there, who was paged, who turned up, and the
    roles nobody holds.

    Readable by every member of the tenant, on the same reasoning the incident
    reads carry no permission (DR-071): a war room that the paged SME cannot open
    is a war room that has failed at the one thing it exists for.
    """
    view = await container.war_rooms.get(principal, incident_id)
    return war_room_out(view, now=container.clock.now())


@router.post("/incidents/{incident_id}/war-room/join", response_model=WarRoomOut)
async def join_war_room(
    incident_id: str,
    body: JoinIn,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """Turn up. Self-scoped: you join yourself, into a room in your own tenant.

    There is no field for joining somebody else, so there is nothing a
    permission would protect that the session does not already decide.
    """
    view = await container.war_rooms.join(principal, incident_id, note=body.note)
    return war_room_out(view, now=container.clock.now())


@router.get("/incidents/{incident_id}/brief", response_model=BriefOut)
async def get_join_brief(
    incident_id: str,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """What somebody walking in at minute forty needs to know.

    Addressed to the caller when they are in the room: what is being asked of
    *them* is the part a responder reads first. `source` says who wrote it -
    `template` today, `model` when S5's writer lands.
    """
    return brief_out(await container.war_rooms.brief(principal, incident_id))


# --- paging -------------------------------------------------------------------


@router.get("/incidents/{incident_id}/pages", response_model=PageListOut)
async def list_pages(
    incident_id: str,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """The ack board, oldest first, each row carrying its own countdown."""
    view = await container.war_rooms.get(principal, incident_id)
    return page_list_out(view.pages, now=container.clock.now())


@router.post("/incidents/{incident_id}/pages", response_model=PageOut, status_code=201)
async def raise_page(
    incident_id: str,
    body: PageIn,
    principal: Principal = Depends(deps.require_incident(Permission.DECLARE_INCIDENT)),
    container: deps.Container = Depends(deps.get_container),
):
    """Page somebody who was not in the blast radius.

    Command work: putting a phone call into somebody's night is a decision about
    another person's time, so it carries `declare_incident`.
    """
    incident = (await container.incidents.get(principal, incident_id)).incident
    role = None
    if body.role:
        try:
            role = Role(body.role)
        except ValueError as exc:
            raise WarRoomValidationError(
                f"{body.role!r} is not a role; one of {[r.value for r in Role]}"
            ) from exc
    page = await container.paging.page(
        tenant_id=principal.tenant_id,
        incident=incident,
        recipient=body.user_id,
        role=role,
        reason=body.reason or "paged by the incident commander",
        timeout_s=body.ack_timeout_s,
        requested_by=principal.user_id,
        requested_by_type="human",
    )
    return page_out(page, now=container.clock.now())


@router.post("/incidents/{incident_id}/pages/{page_id}/ack", response_model=PageOut)
async def acknowledge_page(
    incident_id: str,
    page_id: str,
    body: AckIn,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """"I have this."

    Self-scoped and carries no permission, because the role that answers a page
    holds none. The control is that the page names its recipient and the session
    names itself: acknowledging anybody else's page is a 403 from the entity,
    whatever the caller's roles say. Idempotent and terminal.
    """
    page = await container.paging.acknowledge(principal, page_id, note=body.note)
    return page_out(page, now=container.clock.now())


@router.post(
    "/incidents/{incident_id}/pages/{page_id}/escalate", response_model=PageOut
)
async def escalate_page(
    incident_id: str,
    page_id: str,
    principal: Principal = Depends(deps.require_incident(Permission.DECLARE_INCIDENT)),
    container: deps.Container = Depends(deps.get_container),
):
    """Escalate now rather than waiting out the countdown.

    Returns the *successor* page - the one now ringing - because that is the row
    the board needs to start counting down.
    """
    successor = await container.paging.escalate_now(principal, page_id)
    return page_out(successor, now=container.clock.now())


@router.get("/my/pages", response_model=PageListOut)
async def my_pages(
    open_only: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """What is on this person's phone. The S22 mobile paging page reads this.

    Self-scoped by construction: the recipient is the session and there is no
    parameter for anybody else, which is what lets it carry no permission.
    """
    pages = await container.paging.list_for_recipient(
        principal, open_only=open_only, limit=limit
    )
    return page_list_out(pages, now=container.clock.now())


# --- the bridge ---------------------------------------------------------------


@router.get("/incidents/{incident_id}/bridge", response_model=BridgeOut)
async def read_bridge(
    incident_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """The live transcript. Says so plainly when no bridge is bound rather than
    rendering an empty panel that looks like silence."""
    view = await container.war_rooms.get(principal, incident_id)
    messages = await container.bridge.read(principal, incident_id, limit=limit)
    return bridge_out(
        incident_id,
        messages,
        channel=view.room.bridge_channel,
        has_bridge=view.room.has_bridge,
    )


@router.post("/incidents/{incident_id}/bridge", response_model=BridgeOut, status_code=201)
async def post_to_bridge(
    incident_id: str,
    body: BridgePostIn,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """Say something. Self-scoped: the author is the session and cannot be
    anybody else, and speaking marks you present in the room."""
    message = await container.bridge.post(principal, incident_id, body.text)
    view = await container.war_rooms.get(principal, incident_id)
    return bridge_out(
        incident_id,
        (message,),
        channel=view.room.bridge_channel,
        has_bridge=view.room.has_bridge,
    )


# --- the task board -----------------------------------------------------------


@router.get("/incidents/{incident_id}/tasks", response_model=TaskListOut)
async def list_tasks(
    incident_id: str,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """Commitments with an owner and a due time, harvested or typed."""
    tasks = await container.incident_tasks.list(principal, incident_id)
    return task_list_out(tasks, now=container.clock.now())


@router.post("/incidents/{incident_id}/tasks", response_model=TaskOut, status_code=201)
async def create_task(
    incident_id: str,
    body: TaskIn,
    principal: Principal = Depends(deps.require_incident(Permission.DECLARE_INCIDENT)),
    container: deps.Container = Depends(deps.get_container),
):
    """Put work on the board. Command work, so it carries a permission."""
    task = await container.incident_tasks.create(
        principal,
        incident_id,
        title=body.title,
        owner_user_id=body.owner_user_id,
        owner_label=body.owner_label,
        due_at=body.due_at,
        note=body.note,
    )
    return task_out(task, now=container.clock.now())


@router.patch("/incidents/{incident_id}/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    incident_id: str,
    task_id: str,
    body: TaskPatchIn,
    principal: Principal = Depends(deps.require_incident(Permission.DECLARE_INCIDENT)),
    container: deps.Container = Depends(deps.get_container),
):
    """Reassign, re-time or cancel - all decisions about somebody else's work."""
    task = await container.incident_tasks.update(
        principal,
        task_id,
        owner_user_id=body.owner_user_id,
        due_at=body.due_at,
        cancel=body.cancel,
        note=body.note,
    )
    return task_out(task, now=container.clock.now())


@router.post("/incidents/{incident_id}/tasks/{task_id}/done", response_model=TaskOut)
async def complete_task(
    incident_id: str,
    task_id: str,
    body: TaskDoneIn,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """Tick your own task.

    Self-scoped: an owned task is completed by its owner and nobody else, because
    "done" is a claim about work somebody did. An *unassigned* task is completable
    by anybody in the tenant - somebody has to be able to close out work that was
    actually done, and refusing until a commander assigns it first is how a board
    fills with things everybody knows are finished.
    """
    task = await container.incident_tasks.complete(principal, task_id, note=body.note)
    return task_out(task, now=container.clock.now())
