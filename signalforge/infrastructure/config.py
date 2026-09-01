"""Runtime configuration from the environment (CLAUDE.md: config only).

Key material comes from env vars; on the single dev machine, missing keys are
generated once and persisted under var/keys/ so restarts keep sessions and
secrets readable. Setting the env var always wins. Security floor: the access
TTL is capped, never raised, by configuration.
"""

from __future__ import annotations

import os
import secrets as pysecrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Union

from cryptography.fernet import Fernet

from signalforge.contracts.ingest import REPLAY_WINDOW_S
from signalforge.domain.detection.baselines import DEFAULT_BASELINE_WINDOW_S
from signalforge.domain.detection.rollups import ROLLUP_INTERVAL_S, ROLLUP_RETENTION_S
from signalforge.domain.events.retention import SIGNAL_RETENTION_DEFAULT_DAYS
from signalforge.domain.llm.budget import DEFAULT_INCIDENT_TOKEN_BUDGET
from signalforge.domain.llm.entities import ModelClass
from signalforge.infrastructure.llm.hosts import describe_endpoint, endpoint_is_approved

ROOT = Path(__file__).resolve().parents[2]
KEYS_DIR = ROOT / "var" / "keys"
ADAPTERS_DIR = Path(__file__).resolve().parent / "adapters"

VERSION = "0.2.0"

ACCESS_TTL_CAP_S = 900  # 15-minute JWT access tokens (REQ-010); config may only shorten

SOCKET_TICKET_TTL_S = 30
"""How long a WebSocket ticket lives (REQ-033).

Thirty seconds, and it is not configurable. A ticket exists to cross the gap
between "this session asked for one" and "this socket connected"; anything
longer is a second session credential with a longer half-life than the problem
it solves. A cockpit whose handshake takes longer than half a minute asks for
another one, which costs one authenticated request."""

# Push ingestion (REQ-032). The body cap is a ceiling on what one POST may make
# this process hold in memory while it hashes it; MAX_BATCH_ITEMS items of a few
# hundred bytes each fit inside it with room to spare, so the cap bites on abuse
# rather than on legitimate traffic.
INGEST_MAX_BODY_BYTES_CAP = 4 * 1024 * 1024
INGEST_DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024
INGEST_DEFAULT_RATE_LIMIT = 600
INGEST_RATE_WINDOW_S = 60


# --- the model path (S5-T02, REQ-051) ----------------------------------------
#
# Every one of these is configuration. Model ids in particular are swappable to
# any approved model without a code change (CLAUDE.md, Environment), which is
# why no model id appears anywhere in domain or application code - a call site
# names a classification and this file decides which model serves it.

GENAILAB_DEFAULT_BASE_URL = "https://genailab.tcs.in/v1"

DEFAULT_MODEL_GENERAL = "genailab-maas-gpt-5.4-mini"
DEFAULT_MODEL_REASONING = "genailab-maas-gpt-5.4"
DEFAULT_MODEL_EMBEDDINGS = "azure/genailab-maas-text-embedding-3-large"

LLM_DEFAULT_TIMEOUT_S = 20.0
LLM_MIN_TIMEOUT_S = 1.0
# A ceiling, not a default: a model call that can hang for ten minutes inside an
# incident is a worse failure than one that gives up and degrades.
LLM_TIMEOUT_CAP_S = 120.0
LLM_DEFAULT_MAX_ATTEMPTS = 3
LLM_MAX_ATTEMPTS_CAP = 5

# Bumped by hand when the prompt-construction or response-parsing rules change.
# It is part of every cache key, so a change invalidates warm entries instead of
# serving answers produced by rules that no longer exist.
LLM_PROMPT_CONFIG_VERSION = "1"

PROVIDER_NAME = "genailab"

_CA_BUNDLE_VARS = ("LLM_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE")


def normalize_gateway_url(raw: str) -> str:
    """Accept the endpoint with or without its `/v1` suffix.

    Both forms are published for the gateway and operators paste either, so this
    normalises rather than rejects - but only by *appending* the version
    segment. It never rewrites the host, which is the part that is a security
    control (infrastructure/llm/hosts.py).
    """
    url = (raw or "").strip().rstrip("/")
    if not url:
        return GENAILAB_DEFAULT_BASE_URL
    return url if url.endswith("/v1") else f"{url}/v1"


def _ca_bundle(env: Mapping[str, str]) -> Optional[str]:
    """A CA bundle from the environment, if it names a file that exists.

    This is the supported answer to a corporate TLS-interception certificate.
    There is deliberately no variable that disables verification: an operator
    who cannot present a CA bundle gets a degraded model path, not an
    unauthenticated one (CLAUDE.md rule 4).
    """
    for name in _CA_BUNDLE_VARS:
        value = (env.get(name) or "").strip()
        if value and Path(value).is_file():
            return value
    return None


@dataclass(frozen=True)
class LlmSettings:
    """Everything the provider needs, resolved once from the environment.

    `api_key` carries `repr=False` so that a settings object printed into a log,
    a traceback or a debugger never shows the credential; `redacted` is what
    diagnostics print instead.
    """

    base_url: str
    api_key: str = field(default="", repr=False)
    key_source: str = ""
    models: Mapping[ModelClass, str] = field(default_factory=dict)
    timeout_s: float = LLM_DEFAULT_TIMEOUT_S
    max_attempts: int = LLM_DEFAULT_MAX_ATTEMPTS
    cache_enabled: bool = True
    incident_token_budget: int = DEFAULT_INCIDENT_TOKEN_BUDGET
    prompt_version: str = LLM_PROMPT_CONFIG_VERSION
    verify: Union[bool, str] = True
    provider: str = PROVIDER_NAME

    @property
    def endpoint_approved(self) -> bool:
        return endpoint_is_approved(self.base_url)

    @property
    def configured(self) -> bool:
        """Whether a live call may be attempted at all. Both halves matter: a
        key pointed at an unapproved host is worse than no key."""
        return bool(self.api_key) and self.endpoint_approved

    @property
    def redacted(self) -> dict[str, object]:
        """What diagnostics, `/status` and the degraded event are allowed to say."""
        return {
            "provider": self.provider,
            "endpoint": describe_endpoint(self.base_url),
            "endpoint_approved": self.endpoint_approved,
            "key_present": bool(self.api_key),
            "key_source": self.key_source or None,
            "models": {kind.value: name for kind, name in self.models.items()},
            "timeout_s": self.timeout_s,
            "cache_enabled": self.cache_enabled,
            "tls_verify": "ca-bundle" if isinstance(self.verify, str) else bool(self.verify),
        }

    def model_for(self, model_class: ModelClass) -> str:
        return self.models[model_class]


def load_llm_settings(env: Optional[Mapping[str, str]] = None) -> LlmSettings:
    """Resolve the model path from the environment.

    `GENAILAB_API_KEY` wins. `OPENAI_API_KEY` is accepted as an alias so that an
    existing local setup keeps working - but it selects nothing: the endpoint is
    the gateway either way, and `OPENAI_BASE_URL`/`OPENAI_API_BASE` are ignored,
    because a provider inferred from a variable name is how prompts end up at a
    public API by accident.
    """
    env = os.environ if env is None else env

    key = (env.get("GENAILAB_API_KEY") or "").strip()
    key_source = "GENAILAB_API_KEY" if key else ""
    if not key:
        key = (env.get("OPENAI_API_KEY") or "").strip()
        key_source = "OPENAI_API_KEY" if key else ""

    return LlmSettings(
        base_url=normalize_gateway_url(env.get("GENAILAB_BASE_URL", "")),
        api_key=key,
        key_source=key_source,
        models={
            ModelClass.GENERAL: env.get("LLM_MODEL_GENERAL") or DEFAULT_MODEL_GENERAL,
            ModelClass.REASONING: env.get("LLM_MODEL_REASONING") or DEFAULT_MODEL_REASONING,
            ModelClass.EMBEDDINGS: (
                env.get("LLM_MODEL_EMBEDDINGS") or DEFAULT_MODEL_EMBEDDINGS
            ),
        },
        timeout_s=min(
            max(_float(env, "LLM_TIMEOUT_S", LLM_DEFAULT_TIMEOUT_S), LLM_MIN_TIMEOUT_S),
            LLM_TIMEOUT_CAP_S,
        ),
        max_attempts=min(
            max(_int(env, "LLM_MAX_ATTEMPTS", LLM_DEFAULT_MAX_ATTEMPTS), 1),
            LLM_MAX_ATTEMPTS_CAP,
        ),
        cache_enabled=(env.get("LLM_CACHE", "true").strip().lower()
                       not in {"0", "false", "no", "off"}),
        incident_token_budget=max(
            _int(env, "LLM_INCIDENT_TOKEN_BUDGET", DEFAULT_INCIDENT_TOKEN_BUDGET), 1_000
        ),
        prompt_version=env.get("LLM_PROMPT_VERSION") or LLM_PROMPT_CONFIG_VERSION,
        verify=_ca_bundle(env) or True,
    )


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(env.get(name, default))
    except (TypeError, ValueError):
        return default


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, default))
    except (TypeError, ValueError):
        return default


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
    # Detection (S4-T01). rollup_db_url is a *second* database: derived,
    # high-frequency, pruned in hours, and deliberately not the one carrying the
    # audit hash chain (see infrastructure/persistence/rollup_models.py).
    rollup_db_url: str = f"sqlite+aiosqlite:///{ROOT / 'var' / 'rollups.db'}"
    detection_enabled: bool = True
    rollup_interval_s: float = float(ROLLUP_INTERVAL_S)
    detection_interval_s: float = float(ROLLUP_INTERVAL_S)
    probe_interval_s: float = 30.0
    rollup_retention_s: float = float(ROLLUP_RETENTION_S)
    detection_baseline_window_s: float = float(DEFAULT_BASELINE_WINDOW_S)
    # Incident lifecycle (S4-T02). How often the ITSM link is retried for
    # incidents whose ticket is pending, failed or - the one that matters -
    # uncertain after a timeout.
    itsm_sync_interval_s: float = 30.0
    # The war room (S4-T03). The escalation sweep is the tighter of the two
    # because it is the one somebody is waiting on: it decides how much later
    # than its deadline an unanswered page actually escalates.
    escalation_interval_s: float = 15.0
    bridge_harvest_interval_s: float = 30.0


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
        rollup_db_url=os.environ.get(
            "SIGNALFORGE_ROLLUP_DB_URL",
            f"sqlite+aiosqlite:///{ROOT / 'var' / 'rollups.db'}",
        ),
        detection_enabled=os.environ.get("SIGNALFORGE_DETECTION_ENABLED", "1") != "0",
        # The cadence is a floor as well as a default: a poll interval wider than
        # the bucket would leave holes a baseline has to interpolate across, and
        # one narrower would put two polls in one bucket, where the second is a
        # duplicate by design and therefore wasted work.
        rollup_interval_s=max(
            float(os.environ.get("SIGNALFORGE_ROLLUP_INTERVAL_S", ROLLUP_INTERVAL_S)),
            1.0,
        ),
        detection_interval_s=max(
            float(
                os.environ.get("SIGNALFORGE_DETECTION_INTERVAL_S", ROLLUP_INTERVAL_S)
            ),
            1.0,
        ),
        probe_interval_s=max(
            float(os.environ.get("SIGNALFORGE_PROBE_INTERVAL_S", 30)), 5.0
        ),
        rollup_retention_s=max(
            float(os.environ.get("SIGNALFORGE_ROLLUP_RETENTION_S", ROLLUP_RETENTION_S)),
            600.0,
        ),
        detection_baseline_window_s=max(
            float(
                os.environ.get(
                    "SIGNALFORGE_DETECTION_BASELINE_WINDOW_S", DEFAULT_BASELINE_WINDOW_S
                )
            ),
            60.0,
        ),
        # At least five seconds: a retry sweep tighter than that would hammer a
        # ticket queue that is already failing, which is the opposite of what a
        # recovery path is for.
        itsm_sync_interval_s=max(
            float(os.environ.get("SIGNALFORGE_ITSM_SYNC_INTERVAL_S", 30)), 5.0
        ),
        # At least a second. The escalation sweep is arithmetic over an indexed
        # work list, so a tight cadence is cheap, but a sub-second one would burn
        # a core to shave milliseconds off a countdown measured in minutes.
        escalation_interval_s=max(
            float(os.environ.get("SIGNALFORGE_ESCALATION_INTERVAL_S", 15)), 1.0
        ),
        bridge_harvest_interval_s=max(
            float(os.environ.get("SIGNALFORGE_BRIDGE_HARVEST_INTERVAL_S", 30)), 5.0
        ),
    )
