"""SignalForge API entrypoint. Thin: routers + schemas only (CLAUDE.md layering).

Every router except /health and the /auth bootstrap endpoints hangs off the
RBAC dependency (REQ-012); domain errors map to HTTP in api/errors.py.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

app = FastAPI(title="SignalForge API", version="0.1.0")

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
