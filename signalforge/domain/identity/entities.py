"""Identity entities: tenant, user, role binding, invite, session (REQ-010).

Pure domain (CLAUDE.md rule 1): dataclasses only, no ORM, no framework imports.
password_hash and token_hash are opaque strings produced by infrastructure ports;
the domain never sees plaintext secrets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from signalforge.contracts.roles import Role
from signalforge.domain.identity.errors import DomainValidationError

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def normalize_email(raw: str) -> str:
    email = raw.strip().lower()
    if not _EMAIL_PATTERN.match(email):
        raise DomainValidationError("invalid email address")
    return email


def validate_slug(slug: str) -> str:
    if not _SLUG_PATTERN.match(slug):
        raise DomainValidationError("slug must be lowercase kebab-case")
    return slug


@dataclass
class Tenant:
    id: str
    name: str
    slug: str
    created_at: datetime
    is_active: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise DomainValidationError("tenant name must not be empty")
        validate_slug(self.slug)


@dataclass
class User:
    id: str
    tenant_id: str
    email: str
    display_name: str
    password_hash: str
    created_at: datetime
    is_active: bool = True

    def __post_init__(self) -> None:
        self.email = normalize_email(self.email)
        if not self.display_name.strip():
            raise DomainValidationError("display name must not be empty")


@dataclass(frozen=True)
class RoleBinding:
    """A user holds a role, org-wide (app_id None) or for one app (REQ-012)."""

    tenant_id: str
    user_id: str
    role: Role
    app_id: Optional[str] = None


@dataclass
class Invite:
    id: str
    tenant_id: str
    email: str
    roles: tuple[Role, ...]
    app_id: Optional[str]
    token_hash: str
    created_by: str
    created_at: datetime
    expires_at: datetime
    accepted_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        self.email = normalize_email(self.email)
        if not self.roles:
            raise DomainValidationError("an invite must grant at least one role")

    def is_open(self, now: datetime) -> bool:
        return (
            self.accepted_at is None
            and self.revoked_at is None
            and now < self.expires_at
        )


@dataclass
class Session:
    """A refresh-token session. The access JWT references it by id, so revoking
    the session invalidates both token kinds (the revocation store, REQ-010)."""

    id: str
    tenant_id: str
    user_id: str
    family_id: str
    refresh_token_hash: str
    issued_at: datetime
    expires_at: datetime
    prev_token_hash: Optional[str] = None
    rotated_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    revoked_reason: Optional[str] = None

    def is_active(self, now: datetime) -> bool:
        return self.revoked_at is None and now < self.expires_at
