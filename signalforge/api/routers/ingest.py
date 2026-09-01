"""The push-ingest API (REQ-032, REQ-017, REQ-004).

Five routes, one per stream, registered at exactly the paths the frozen contract
names - not one templated route with a path parameter, so that `/api/v1/ingest/
alerts` is a real endpoint in the OpenAPI document and an unknown stream is a
404 from the router rather than a validation error from a handler.

Every one of them is thin by construction: the dependency has already
authenticated the bearer token, verified the HMAC over the exact bytes received,
checked the replay window and checked that the key is bound to this stream, so
the handler's only job is to hand the verified request to the application layer
and return the 202 the contract specifies.

These are the only product routes that do not resolve a *human* principal, and
that is the point of them: the caller is a machine holding a credential this
platform issued, scoped to one tenant, one application and one stream. The
security suite's router walk treats the ingest authenticator as equivalent to
the RBAC dependency for coverage, because both resolve a real, revocable
credential before any handler runs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from signalforge.api import deps
from signalforge.application.ingest.ingest_service import AuthenticatedIngest
from signalforge.contracts.ingest import INGEST_PATHS, IngestAck
from signalforge.domain.ingest.streams import IngestStream
from signalforge.infrastructure.observability.logging import request_id_var

router = APIRouter(tags=["ingest"])

SUMMARIES = {
    IngestStream.CHANGES: "Accept a batch of change events (deployments, config, flags)",
    IngestStream.ALERTS: "Accept a batch of alerts",
    IngestStream.TOPOLOGY: "Accept a topology snapshot",
    IngestStream.MESSAGES: "Accept a batch of collaboration messages",
    IngestStream.TICKETS: "Accept a batch of ITSM tickets",
}


def _register(stream: IngestStream) -> None:
    @router.post(
        INGEST_PATHS[stream.value],
        response_model=IngestAck,
        status_code=202,
        name=f"ingest_{stream.value}",
        summary=SUMMARIES[stream],
    )
    async def ingest(  # noqa: D401 - one handler per stream, registered in a loop
        authenticated: AuthenticatedIngest = Depends(deps.require_ingest(stream)),
        container: deps.Container = Depends(deps.get_container),
    ) -> IngestAck:
        return await container.ingest.accept(
            authenticated, request_id=request_id_var.get()
        )


for _stream in IngestStream:
    _register(_stream)
