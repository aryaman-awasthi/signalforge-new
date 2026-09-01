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

from signalforge.contracts.ingest import REPLAY_WINDOW_S
from signalforge.domain.events.retention import SIGNAL_RETENTION_DEFAULT_DAYS

ROOT = Path(__file__).resolve().parents[2]
KEYS_DIR = ROOT / "var" / "keys"
ADAPTERS_DIR = Path(__file__).resolve().parent / "adapters"

VERSION = "0.2.0"

ACCESS_TTL_CAP_S = 900  # 15-minute JWT access tokens (REQ-010); config may only shorten

# Push ingestion (REQ-032). The body cap is a ceiling on what one POST may make
# this process hold in memory while it hashes it; MAX_BATCH_ITEMS items of a few
# hundred bytes each fit inside it with room to spare, so the cap bites on abuse
# rather than on legitimate traffic.
INGEST_MAX_BODY_BYTES_CAP = 4 * 1024 * 1024
INGEST_DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024
INGEST_DEFAULT_RATE_LIMIT = 600
INGEST_RATE_WINDOW_S = 60


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
    dispatch_interval_s: float
    health_interval_s: float
    log_level: str
    adapters_dir: str
    ingest_replay_window_s: int
    ingest_rate_limit: int
    ingest_rate_window_s: int
    ingest_max_body_bytes: int


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
        dispatch_interval_s=float(
            os.environ.get("SIGNALFORGE_DISPATCH_INTERVAL_S", 0.5)
        ),
        # Connector health every 30s per the architecture's poller cadence.
        health_interval_s=float(os.environ.get("SIGNALFORGE_HEALTH_INTERVAL_S", 30)),
        log_level=os.environ.get("SIGNALFORGE_LOG_LEVEL", "INFO").upper(),
        # Where the adapter registry looks for **/manifest.yaml. Configurable so
        # a deployment can mount an adapter bundle outside the package, and so a
        # test can point the registry at a tree it wrote itself.
        adapters_dir=os.environ.get("SIGNALFORGE_ADAPTERS_DIR", str(ADAPTERS_DIR)),
        # Security floor: config may only *tighten* the contract's replay
        # window and the body cap. The verifier applies the same min() again,
        # so an environment that sets a wider one gets the contract's.
        ingest_replay_window_s=min(
            int(os.environ.get("SIGNALFORGE_INGEST_REPLAY_WINDOW_S", REPLAY_WINDOW_S)),
            REPLAY_WINDOW_S,
        ),
        # At least one request per window: a limit of zero would be a way to
        # switch ingestion off by configuration rather than by revoking keys,
        # and a silently dead endpoint is worse than a visibly revoked one.
        ingest_rate_limit=max(
            int(
                os.environ.get(
                    "SIGNALFORGE_INGEST_RATE_LIMIT", INGEST_DEFAULT_RATE_LIMIT
                )
            ),
            1,
        ),
        ingest_rate_window_s=INGEST_RATE_WINDOW_S,
        ingest_max_body_bytes=min(
            int(
                os.environ.get(
                    "SIGNALFORGE_INGEST_MAX_BODY_BYTES", INGEST_DEFAULT_MAX_BODY_BYTES
                )
            ),
            INGEST_MAX_BODY_BYTES_CAP,
        ),
    )
