"""SignalForge API entrypoint. Thin: routers + schemas only (CLAUDE.md layering).

Sprint 0 scope: /health so the web scaffold has a live API to call. Routers for
auth, orgs, ingest etc. land in their owning sprints.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from signalforge.api.routers import health

app = FastAPI(title="SignalForge API", version="0.0.1")

# Web dev server origins; auth cookie proxy hardens this in Sprint 1.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(health.router)
