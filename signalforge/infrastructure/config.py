"""Runtime configuration from the environment (CLAUDE.md: config only).

Key material comes from env vars; on the single dev machine, missing keys are
generated once and persisted under var/keys/ so restarts keep sessions and
secrets readable. Setting the env var always wins. Security floor: the access
TTL is capped, never raised, by configuration.
"""

from __future__ import annotations

import os
import secrets as pysecrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

from signalforge.domain.events.retention import SIGNAL_RETENTION_DEFAULT_DAYS

ROOT = Path(__file__).resolve().parents[2]
KEYS_DIR = ROOT / "var" / "keys"

ACCESS_TTL_CAP_S = 900  # 15-minute JWT access tokens (REQ-010); config may only shorten


def _persisted_key(env_var: str, filename: str, generate) -> str:
    value = os.environ.get(env_var)
    if value:
        return value
    path = KEYS_DIR / filename
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    value = generate()
    path.write_text(value, encoding="utf-8")
    return value


@dataclass(frozen=True)
class Settings:
    db_url: str
    jwt_secret: str
    fernet_key: str
    access_ttl_s: int
    refresh_ttl_s: int
    login_rate_limit: int
    login_rate_window_s: int
    invite_ttl_s: int
    signal_retention_days: int
    worker_enabled: bool
    retention_interval_s: int


def load_settings() -> Settings:
    return Settings(
        db_url=os.environ.get(
            "SIGNALFORGE_DB_URL", f"sqlite+aiosqlite:///{ROOT / 'var' / 'signalforge.db'}"
        ),
        jwt_secret=_persisted_key(
            "SIGNALFORGE_JWT_SECRET", "jwt.secret", lambda: pysecrets.token_urlsafe(48)
        ),
        fernet_key=_persisted_key(
            "SIGNALFORGE_FERNET_KEY", "fernet.key", lambda: Fernet.generate_key().decode()
        ),
        access_ttl_s=min(
            int(os.environ.get("SIGNALFORGE_ACCESS_TTL_S", ACCESS_TTL_CAP_S)),
            ACCESS_TTL_CAP_S,
        ),
        refresh_ttl_s=int(os.environ.get("SIGNALFORGE_REFRESH_TTL_S", 14 * 86_400)),
        login_rate_limit=int(os.environ.get("SIGNALFORGE_LOGIN_RATE_LIMIT", 5)),
        login_rate_window_s=int(os.environ.get("SIGNALFORGE_LOGIN_RATE_WINDOW_S", 300)),
        invite_ttl_s=int(os.environ.get("SIGNALFORGE_INVITE_TTL_S", 7 * 86_400)),
        # Bounds are enforced by RetentionPolicy, not here: config chooses a
        # window inside the floor, it cannot remove the floor (REQ-023).
        signal_retention_days=int(
            os.environ.get(
                "SIGNALFORGE_SIGNAL_RETENTION_DAYS", SIGNAL_RETENTION_DEFAULT_DAYS
            )
        ),
        worker_enabled=os.environ.get("SIGNALFORGE_WORKER_ENABLED", "1") != "0",
        retention_interval_s=int(
            os.environ.get("SIGNALFORGE_RETENTION_INTERVAL_S", 3_600)
        ),
    )
