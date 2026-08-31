"""Shared test helpers: fake clock, API test context over a temp-file DB."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from signalforge.domain.identity.ports import Clock
from signalforge.infrastructure.config import ADAPTERS_DIR, Settings


class FakeClock(Clock):
    def __init__(self, start: datetime | None = None):
        self.current = start or datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        self.current += timedelta(**kwargs)


def make_test_settings(db_url: str, adapters_dir: str | None = None) -> Settings:
    return Settings(
        db_url=db_url,
        jwt_secret="test-jwt-secret-with-enough-length-0123456789",
        fernet_key=Fernet.generate_key().decode(),
        access_ttl_s=900,
        refresh_ttl_s=14 * 86_400,
        login_rate_limit=5,
        login_rate_window_s=300,
        invite_ttl_s=7 * 86_400,
        signal_retention_days=7,
        # The worker is driven explicitly in tests: a background loop writing to
        # the same SQLite file would make assertions depend on timing.
        worker_enabled=False,
        retention_interval_s=3_600,
        dispatch_interval_s=0.5,
        health_interval_s=30,
        log_level="INFO",
        # Tests that care about the registry point it at a tree they wrote; the
        # rest run against the adapters this deployment actually ships.
        adapters_dir=adapters_dir or str(ADAPTERS_DIR),
    )


@dataclass
class ApiCtx:
    client: object  # fastapi TestClient
    container: object
    clock: FakeClock
    db_path: Path

    # -- request helpers ---------------------------------------------------

    def signup(self, org="Acme Corp", email="owner@acme.io", password="a-strong-password",
               display_name="Owner"):
        response = self.client.post(
            "/auth/signup",
            json={"org_name": org, "email": email, "password": password,
                  "display_name": display_name},
        )
        assert response.status_code == 201, response.text
        return response.json()

    def auth_header(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def invite_and_accept(self, owner_token: str, *, email: str, roles: list[str],
                          app_id: str | None = None, display_name="Member",
                          password="another-strong-pw"):
        created = self.client.post(
            "/invites",
            json={"email": email, "roles": roles, "app_id": app_id},
            headers=self.auth_header(owner_token),
        )
        assert created.status_code == 201, created.text
        accepted = self.client.post(
            "/auth/accept-invite",
            json={"token": created.json()["token"], "password": password,
                  "display_name": display_name},
        )
        assert accepted.status_code == 201, accepted.text
        return accepted.json()

    # -- audit helpers (read the chain directly, sync sqlite) ---------------

    def audit_rows(self, tenant_id: str) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "select topic, actor_id, payload from audit_log "
                "where tenant_id = ? order by seq",
                (tenant_id,),
            ).fetchall()
        finally:
            conn.close()
        return [
            {"topic": t, "actor_id": a, "payload": json.loads(p)} for t, a, p in rows
        ]

    def audit_topics(self, tenant_id: str) -> list[str]:
        return [r["topic"] for r in self.audit_rows(tenant_id)]
