"""/incidents/{id}/replay and /incidents/{id}/pir (REQ-072, REQ-012).

Both hang off `read_audit`, which is the Auditor's only permission - the whole
point of the role is somebody who can read the record and change nothing. The
tenant is taken from the principal and appears in no parameter, so a
cross-tenant replay is not a check that could be got wrong: there is no way to
ask for one.

The scrubber is `until_seq`. Replaying to a point is how the S20 screen
re-renders the incident as it stood at that moment, and it is the same code path
as the full replay with a different bound, so a scrubbed view cannot disagree
with the whole.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from signalforge.api import deps
from signalforge.api.schemas.audit import PirOut, ReplayOut, pir_out, replay_out
from signalforge.application.identity.principal import Principal
from signalforge.application.pir.service import AuditChainBrokenError
from signalforge.contracts.roles import Permission

router = APIRouter(tags=["audit"])


@router.get("/incidents/{incident_id}/replay", response_model=ReplayOut)
async def replay_incident(
    incident_id: str,
    until_seq: Optional[int] = Query(
        default=None,
        ge=1,
        description="Replay only up to this audit seq - the scrubber position.",
    ),
    principal: Principal = Depends(deps.require(Permission.READ_AUDIT)),
    container: deps.Container = Depends(deps.get_container),
):
    """Reconstruct the incident from the hash-chained audit log.

    Returns a document whether or not the chain verifies, and says which it is.
    Hiding a broken chain would be the one unforgivable behaviour here; refusing
    to return anything would deny an auditor the very view that shows what was
    tampered with.
    """
    document = await container.incident_replay.replay(
        principal, incident_id, until_seq=until_seq
    )
    return replay_out(document)


@router.post("/incidents/{incident_id}/pir", response_model=PirOut, status_code=201)
async def generate_pir(
    incident_id: str,
    principal: Principal = Depends(deps.require(Permission.READ_AUDIT)),
    container: deps.Container = Depends(deps.get_container),
):
    """Generate the post-incident review from the incident's own records.

    409 when the audit chain does not verify. A PIR is the artefact people quote
    six months later, so it is not produced at all from records that no longer
    match their hashes.
    """
    try:
        review = await container.pir.generate(principal, incident_id)
    except AuditChainBrokenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return pir_out(review)


@router.get("/incidents/{incident_id}/pir", response_model=PirOut)
async def read_pir(
    incident_id: str,
    principal: Principal = Depends(deps.require(Permission.READ_AUDIT)),
    container: deps.Container = Depends(deps.get_container),
):
    """The same document, regenerated.

    A PIR is derived, not stored: regenerating it from the record is the whole
    guarantee, and a stored copy would be the thing that drifts.
    """
    try:
        review = await container.pir.generate(principal, incident_id)
    except AuditChainBrokenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return pir_out(review)
