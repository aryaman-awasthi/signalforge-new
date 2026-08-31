"""SignalForge API entrypoint. Thin: routers + schemas only (CLAUDE.md layering).

Every router except /health and the /auth bootstrap endpoints hangs off the
RBAC dependency (REQ-012); domain errors map to HTTP in api/errors.py.

One process, two asyncio groups (architecture section 4): the app serves
requests, and the lifespan owns the worker group that runs everything scheduled.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from signalforge.api import deps
from signalforge.api.errors import register_error_handlers
from signalforge.api.routers import (
    auth,
    escalation,
    events,
    health,
    invites,
    oncall,
    orgs,
    roles,
    users,
)

@asynccontextmanager
async def lifespan(application: FastAPI):
    # Resolve through the override so a test's container - and its database -
    # is the one the worker runs against, or nothing runs at all.
    provider = application.dependency_overrides.get(
        deps.get_container, deps.get_container
    )
    container = provider()
    if container.settings.worker_enabled:
        await container.worker.start()
    try:
        yield
    finally:
        await container.worker.stop()


app = FastAPI(title="SignalForge API", version="0.2.0", lifespan=lifespan)

# Dev CORS for the web dev server; the browser talks to the Next.js auth
# cookie proxy in production-shaped setups.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

register_error_handlers(app)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(orgs.router)
app.include_router(users.router)
app.include_router(invites.router)
app.include_router(roles.router)
app.include_router(oncall.router)
app.include_router(escalation.router)
app.include_router(events.router)
