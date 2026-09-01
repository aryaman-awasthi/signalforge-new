"""The ingest key: the credential a sending application holds (REQ-032, REQ-017).

The frozen ingest contract asks for two things on every push: a bearer token
that identifies who is posting, and an HMAC-SHA256 signature over
"<timestamp>.<raw body>" made with that sender's shared secret. An ingest key is
that pair, issued by SignalForge for one tenant's one application and one
stream:

- the **token** is 256 bits of randomness and only its sha256 is ever stored,
  exactly as refresh tokens are (`domain/identity/tokens.py`). The plaintext is
  returned once, at issue, and the platform cannot show it again;
- the **secret** is stored Fernet-encrypted in the tenant's secret store under
  `ingest/<key_id>/secret`, so it lives on the same path as every other
  credential this platform holds (REQ-015).

Both belong to the *key*, not to the connector binding, and that is what makes
rotation safe: a second key is issued, the sender is moved onto it, and only
then is the first revoked. Two keys are live during the handover, each with its
own secret, which is what the contract's optional `X-SignalForge-Key-Id` header
is for - it names which of them the sender believes it signed with, and a
mismatch is refused rather than silently ignored.

Scope is deliberately narrow. A key is bound to one stream, so a token that
leaks from a deploy script cannot be used to inject alerts, and it is bound to
one app_id, so everything it writes lands under that application.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional

from signalforge.domain.ingest.errors import IngestAuthError, IngestStreamError
from signalforge.domain.ingest.streams import IngestStream

TOKEN_PREFIX = "sfi_"
"""Recognisable at a glance in a config file or a leaked log, which is what
makes a secret scanner able to find one."""

TOKEN_ENTROPY_BYTES = 32
DISPLAY_CHARS = 12
KEY_NAME_PATTERN = re.compile(r"^[\w][\w .:/-]{0,79}$")

SECRET_PREFIX = "ingest"


def new_ingest_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)


def new_ingest_secret() -> str:
    return secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)


def hash_ingest_token(token: str) -> str:
    """Only this ever reaches storage. A stolen database yields no usable token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def display_prefix(token: str) -> str:
    """What the operator screen shows so one key can be told from another
    without the platform being able to show either of them again."""
    return token[:DISPLAY_CHARS]


def secret_name(key_id: str) -> str:
    """Where this key's shared secret lives in the tenant's secret store.
    Encoding the key id means revoking a key can destroy exactly its own secret."""
    return f"{SECRET_PREFIX}/{key_id}/secret"


def bearer_token(authorization: str) -> str:
    """Pull the token out of an Authorization header, or refuse.

    Deliberately strict about the scheme: accepting a bare token would mean an
    accidentally-forwarded header of some other kind could authenticate.
    """
    header = (authorization or "").strip()
    if not header.lower().startswith("bearer "):
        raise IngestAuthError("missing bearer token")
    token = header[len("bearer "):].strip()
    if not token:
        raise IngestAuthError("empty bearer token")
    return token


@dataclass(frozen=True)
class IngestKey:
    id: str
    tenant_id: str
    app_id: str
    stream: IngestStream
    name: str
    token_hash: str
    token_prefix: str
    binding_id: Optional[str] = None
    """The push connector binding whose adapter translates a vendor-shaped
    payload into the canonical model. Optional: a sender that already speaks the
    canonical contract needs no translator, and two of the five streams -
    collaboration and itsm - cannot have a push adapter at all, because the
    frozen SPI defines `normalize` only on alerts, changes and topology."""
    rotated_from: Optional[str] = None
    created_at: Optional[datetime] = None
    created_by: str = ""
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    last_used_at: Optional[datetime] = None
    last_event_at: Optional[datetime] = None
    last_error: Optional[str] = None
    accepted_count: int = 0
    duplicate_count: int = 0
    rejected_count: int = 0

    def __post_init__(self) -> None:
        if not KEY_NAME_PATTERN.match(self.name or ""):
            raise ValueError("ingest key name must be 1-80 printable characters")

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    @property
    def path(self) -> str:
        return self.stream.path

    def freshness_s(self, now: datetime) -> Optional[float]:
        """Seconds since anything actually arrived on this key.

        This is the number that answers "is the app still posting", which a ping
        cannot: SignalForge is the *server* for a push binding, so it is always
        reachable and always would be, even if the sender died a week ago.
        """
        if self.last_event_at is None:
            return None
        return max((now - self.last_event_at).total_seconds(), 0.0)

    # -- the checks every inbound request passes -----------------------------

    def assert_presentable(self, presented_key_id: Optional[str]) -> None:
        """What can be checked before the signature is verified."""
        if not self.active:
            raise IngestAuthError("ingest key revoked")
        if presented_key_id and presented_key_id.strip() != self.id:
            # The sender signed with a secret it believes belongs to a different
            # key. Refusing is the point: during a rotation the two keys have
            # two secrets, and quietly verifying against the one the token
            # happens to name would defeat the handover.
            raise IngestAuthError("key id does not match the presented token")

    def assert_stream(self, stream: IngestStream) -> None:
        """Checked after the signature, so the reason can be recorded honestly."""
        if stream is not self.stream:
            raise IngestStreamError(
                f"this key posts to {self.stream.value}, not {stream.value}"
            )

    # -- transitions ---------------------------------------------------------

    def used(self, at: datetime) -> "IngestKey":
        return replace(self, last_used_at=at, last_error=None)

    def observed_batch(
        self, at: datetime, *, accepted: int, duplicates: int, rejected: int
    ) -> "IngestKey":
        return replace(
            self,
            last_used_at=at,
            last_event_at=at if accepted else self.last_event_at,
            accepted_count=self.accepted_count + accepted,
            duplicate_count=self.duplicate_count + duplicates,
            rejected_count=self.rejected_count + rejected,
            last_error=None if not rejected else self.last_error,
        )

    def refused(self, at: datetime, reason: str) -> "IngestKey":
        """A refusal is recorded on the key so that the operator who can see the
        key - and only that operator - can find out why a sender is failing,
        while the sender itself gets a bare 401."""
        return replace(
            self, last_used_at=at, last_error=reason, rejected_count=self.rejected_count + 1
        )

    def revoked(self, at: datetime, by: str) -> "IngestKey":
        return replace(self, revoked_at=at, revoked_by=by)
