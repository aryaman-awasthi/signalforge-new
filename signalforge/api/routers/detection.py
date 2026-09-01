"""/apps/{app_id}/detection: what the platform currently believes (S4-T01).

Three routes, all `manage_apps` with the application named in the path, so a
per-application role binding is evaluated against the application being read -
the same rule every other `/apps` route follows.

The distinction between the two POSTs that are not here matters more than the
routes that are. There is no route that declares an incident, changes a severity
or acknowledges a proposal. Detection proposes; a human declares (REQ-043), and
the lifecycle that does it arrives in S4-T02. `POST .../detection/evaluate` is
an operator overriding the five-second cadence - "look now, I am watching" - and
it is audited as an act for exactly that reason.

`GET .../detection` advances nothing. Refreshing a screen must not be able to
age a breach into an anomaly, so the read summarises the states already stored
rather than running an evaluation.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from signalforge.api import deps
from signalforge.api.schemas.detection import (
    DetectionOut,
    RollupsOut,
    detection_out,
    rollups_out,
)
from signalforge.application.identity.principal import Principal
from signalforge.contracts.roles import Permission
from signalforge.domain.detection.rollups import MAX_ROLLUP_WINDOW_S, ROLLUP_INTERVAL_S

router = APIRouter(prefix="/apps", tags=["detection"])


@router.get("/{app_id}/detection", response_model=DetectionOut)
async def get_detection(
    app_id: str,
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    """The current stance, every signal against its baseline, the health score
    per region, SLO burn, corroboration and the severity the matrix would
    propose. Reading it changes nothing."""
    return detection_out(await container.detection.snapshot(principal, app_id))


@router.post("/{app_id}/detection/evaluate", response_model=DetectionOut)
async def evaluate_now(
    app_id: str,
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    """Run an evaluation now instead of waiting for the next tick.

    A human override of the cadence, audited as one. It proposes exactly what
    the loop would have proposed and declares nothing.
    """
    return detection_out(await container.detection.force(principal, app_id))


@router.get("/{app_id}/rollups", response_model=RollupsOut)
async def get_rollups(
    app_id: str,
    signal_key: Optional[str] = Query(default=None),
    region: Optional[str] = Query(default=None),
    window_s: float = Query(default=900.0, gt=0, le=MAX_ROLLUP_WINDOW_S),
    principal: Principal = Depends(deps.require_app(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    """The five-second buckets a verdict was derived from, so a proposal can be
    checked against the measurements rather than believed."""
    rows = await container.detection.rollups(
        principal, app_id, signal_key=signal_key, region=region, window_s=window_s
    )
    return rollups_out(app_id, window_s, ROLLUP_INTERVAL_S, rows)
