"""/health: the liveness and readiness probe (REQ-025).

Unauthenticated, because a probe has no session, which is exactly why it reports
component statuses and nothing that identifies a tenant, an incident or a person.
Operators who need lag, dead-letter depth and worker detail read
/platform/status behind RBAC.

The S1 response fields are unchanged so existing callers keep working; the
component list and the overall status are added beside them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import Depends, Response
from fastapi import APIRouter
from pydantic import BaseModel

from signalforge.api import deps
from signalforge.contracts.canonical import HealthStatus
from signalforge.infrastructure.config import VERSION

router = APIRouter(tags=["platform"])


class ComponentOut(BaseModel):
    component: str
    status: str
    detail: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    time: datetime
    components: list[ComponentOut] = []


@router.get("/health", response_model=HealthResponse)
async def health(
    response: Response, container: deps.Container = Depends(deps.get_container)
) -> HealthResponse:
    report = await container.platform_status.health_report()
    if report.status is HealthStatus.DOWN:
        response.status_code = 503
    return HealthResponse(
        status="ok" if report.status is HealthStatus.UP else report.status.value,
        service="signalforge-api",
        version=VERSION,
        time=report.checked_at,
        components=[
            ComponentOut(
                component=c.component, status=c.status.value, detail=c.detail
            )
            for c in report.components
        ],
    )
