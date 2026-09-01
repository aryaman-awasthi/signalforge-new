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
from signalforge.api.middleware import RequestContextMiddleware
from signalforge.api.openapi import build_openapi
from signalforge.api.routers import (
    adapters,
    auth,
    brain,
    detection,
    escalation,
    events,
    health,
    incidents,
    ingest,
    ingest_keys,
    invites,
    onboarding,
    oncall,
    orgs,
    platform,
    roles,
    users,
    warroom,
    ws,
)
from signalforge.infrastructure.config import VERSION, load_settings
from signalforge.infrastructure.observability.logging import configure_logging

def current_container() -> deps.Container:
    """Resolve through the dependency override so the middleware and the worker
    use a test's container - and its database - rather than the real one."""
    provider = app.dependency_overrides.get(deps.get_container, deps.get_container)
    return provider()


@asynccontextmanager
async def lifespan(application: FastAPI):
    container = current_container()
    # Again here: the server installs its own plain-text handlers between import
    # and startup, so this is where one log format finally wins.
    configure_logging(container.settings.log_level)
    # The rollup database is derived and rebuildable, so it is created from its
    # models here rather than migrated (S4-T01). Doing it before the worker
    # starts means the first five-second tick has somewhere to write.
    await container.prepare_rollup_store()
    if container.settings.worker_enabled:
        await container.worker.start()
    try:
        yield
    finally:
        await container.worker.stop()


configure_logging(load_settings().log_level)

app = FastAPI(title="SignalForge API", version=VERSION, lifespan=lifespan)

# The published document says what credential and what permission each route
# wants, read from the route's own guard (api/openapi.py). Assigned rather than
# subclassed so GET /openapi.json and the exported docs/11-openapi.json are the
# same bytes.
app.openapi = lambda: build_openapi(app)

app.add_middleware(RequestContextMiddleware, container_provider=current_container)

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
app.include_router(adapters.router)
app.include_router(onboarding.router)
app.include_router(detection.router)
app.include_router(incidents.router)
app.include_router(incidents.apps_router)
app.include_router(warroom.router)
app.include_router(brain.router)
app.include_router(ingest_keys.router)
app.include_router(ingest.router)
app.include_router(platform.router)
app.include_router(ws.router)
