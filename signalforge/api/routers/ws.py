"""/ws: the cockpit's live notification socket (REQ-033).

Authenticated, self-scoped and deliberately without a permission check. Every
other product route names a permission from the roles matrix; this one cannot,
because the people who most need to be paged - SMEs - hold no permissions at
all. The compensating control is that the socket is incapable of naming anyone
else: the principal comes from the session, the subscription is keyed on that
principal's tenant and user id, and catch-up reads that recipient's own
notifications. There is no parameter through which a caller could ask for
somebody else's.

Browsers cannot set an Authorization header on a WebSocket handshake, so the
token may also arrive as the `bearer, <token>` subprotocol pair - a standard
pattern, and specifically not a query parameter, which would put a session token
into every access log and proxy history on the path.

Frames the client may send:

    {"type": "hello", "channels": ["in_app", "browser_push"]}
    {"type": "ack",   "notification_id": "..."}
    {"type": "ping"}

Frames the server sends: `ready`, one `notification` per open item on connect
(marked `replay`), then live `notification` frames, plus `ack.ok`, `pong` and
`error`.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from signalforge.api import deps
from signalforge.application.identity.principal import Principal
from signalforge.contracts.canonical import NotificationChannel
from signalforge.domain.integrations.entities import delivery_frame
from signalforge.domain.shared.errors import DomainValidationError, NotFoundError

logger = logging.getLogger("signalforge.notifications")

router = APIRouter(tags=["notifications"])

BEARER_SUBPROTOCOL = "bearer"


@router.websocket("/ws")
async def notification_socket(
    websocket: WebSocket,
    principal: Principal = Depends(deps.get_ws_principal),
    container: deps.Container = Depends(deps.get_container),
):
    subprotocol = (
        BEARER_SUBPROTOCOL
        if BEARER_SUBPROTOCOL in (websocket.scope.get("subprotocols") or [])
        else None
    )
    await websocket.accept(subprotocol=subprotocol)

    hub = container.notification_hub
    subscription = hub.subscribe(principal.tenant_id, principal.user_id)
    receiving: asyncio.Task | None = None
    sending: asyncio.Task | None = None
    try:
        await websocket.send_json(
            {
                "type": "ready",
                "user_id": principal.user_id,
                "channels": sorted(c.value for c in subscription.channels),
            }
        )
        await _replay_open(websocket, container, principal, subscription)

        receiving = asyncio.create_task(websocket.receive_json())
        sending = asyncio.create_task(subscription.queue.get())
        while True:
            done, _ = await asyncio.wait(
                {receiving, sending}, return_when=asyncio.FIRST_COMPLETED
            )
            if sending in done:
                await websocket.send_json(sending.result())
                sending = asyncio.create_task(subscription.queue.get())
            if receiving in done:
                # Raises WebSocketDisconnect when the client goes away, which is
                # the normal way out of this loop.
                frame = receiving.result()
                await _handle(websocket, container, principal, subscription, frame)
                receiving = asyncio.create_task(websocket.receive_json())
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.warning("notification socket closed on an error", exc_info=True)
        raise
    finally:
        hub.unsubscribe(subscription)
        for task in (receiving, sending):
            if task is not None and not task.done():
                task.cancel()


async def _replay_open(
    websocket: WebSocket,
    container: deps.Container,
    principal: Principal,
    subscription,
) -> None:
    """Hand over anything still waiting for this person.

    A notification produced while nobody was connected is queued, not lost, and
    this is where it is finally delivered - which is what makes "no cockpit is
    open" an acceptable outcome for a page rather than a failure.
    """
    open_items = await container.notification_inbox.catch_up(
        principal.tenant_id,
        principal.user_id,
        channels=tuple(subscription.channels),
    )
    for delivery in open_items:
        await websocket.send_json(delivery_frame(delivery, replay=True))


async def _handle(
    websocket: WebSocket,
    container: deps.Container,
    principal: Principal,
    subscription,
    frame,
) -> None:
    if not isinstance(frame, dict):
        await websocket.send_json({"type": "error", "detail": "frame must be an object"})
        return
    kind = frame.get("type")
    if kind == "ping":
        await websocket.send_json({"type": "pong"})
        return
    if kind == "hello":
        subscription.channels = _channels(frame.get("channels"))
        await websocket.send_json(
            {
                "type": "subscribed",
                "channels": sorted(c.value for c in subscription.channels),
            }
        )
        # Channels may have widened; catch up on the newly visible ones.
        await _replay_open(websocket, container, principal, subscription)
        return
    if kind == "ack":
        notification_id = str(frame.get("notification_id") or "")
        try:
            delivery = await container.notification_inbox.acknowledge(
                principal.tenant_id, notification_id, by=principal.user_id
            )
        except NotFoundError:
            # Deliberately the same answer for another tenant's id and for one
            # that never existed.
            await websocket.send_json(
                {"type": "error", "detail": "notification not found"}
            )
            return
        except DomainValidationError as exc:
            await websocket.send_json({"type": "error", "detail": str(exc)})
            return
        await websocket.send_json(
            {
                "type": "ack.ok",
                "notification_id": delivery.notification_id,
                "status": delivery.status.value,
                "acked_by": delivery.acked_by,
            }
        )
        return
    await websocket.send_json({"type": "error", "detail": f"unknown frame type {kind!r}"})


def _channels(raw) -> set[NotificationChannel]:
    if not isinstance(raw, list) or not raw:
        return {NotificationChannel.IN_APP}
    channels: set[NotificationChannel] = set()
    for value in raw:
        try:
            channels.add(NotificationChannel(value))
        except ValueError:
            continue
    return channels or {NotificationChannel.IN_APP}
