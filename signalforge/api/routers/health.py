from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["platform"])


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    time: datetime


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="signalforge-api",
        version="0.0.1",
        time=datetime.now(timezone.utc),
    )
