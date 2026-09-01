"""Ingest domain errors.

They subclass the shared categories so api/errors.py maps them without a new
handler. Two mapping decisions are deliberate:

- Everything an unverified caller can trigger is an `IngestAuthError`, which
  becomes a bare 401 `authentication failed`. A sender holding a real key does
  not learn from the *response* whether it got the token, the signature or the
  clock wrong; it learns from `GET /ingest-keys`, where the key's own
  `last_error` names the reason to whoever holds manage_apps. An anonymous
  prober learns nothing at all.
- A body that is authentic but malformed is a `DomainValidationError` (422),
  because at that point the caller is trusted enough to be told what is wrong -
  and a 401 for a missing field would send an integrator hunting the wrong bug.
"""

from __future__ import annotations

from signalforge.domain.identity.errors import (
    AuthenticationError,
    PermissionDeniedError,
)
from signalforge.domain.shared.errors import DomainValidationError, NotFoundError


class IngestAuthError(AuthenticationError):
    """The presented ingest credential could not be verified.

    Covers an absent or unknown bearer token, a revoked key, a missing or
    malformed signature header, a signature that does not match, a timestamp
    outside the replay window and a key-id header naming a different key.
    """

    def __init__(self, reason: str):
        super().__init__("ingest authentication failed")
        self.reason = reason


class IngestStreamError(PermissionDeniedError):
    """A valid key was presented at an endpoint it is not bound to.

    A key is issued for one stream so that a token leaked from a deploy script
    cannot be used to inject alerts. The signature has already been verified
    when this is raised, so the reason is recorded on the key.
    """

    def __init__(self, reason: str):
        super().__init__("ingest key is not bound to this stream")
        self.reason = reason


class IngestKeyNotFoundError(NotFoundError):
    """No such ingest key in this tenant's scope."""


class IngestPayloadError(DomainValidationError):
    """The batch envelope itself is not a valid ingest request."""


class IngestKeyConfigError(DomainValidationError):
    """An ingest key was asked for in a shape that cannot work - an unknown
    stream, or a connector binding that cannot normalise that stream."""
