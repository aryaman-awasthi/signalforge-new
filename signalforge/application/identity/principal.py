"""The authenticated caller, resolved once per request from the access token,
the session (revocation store) and the role bindings."""

from __future__ import annotations

from dataclasses import dataclass

from signalforge.domain.identity.entities import RoleBinding


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    session_id: str
    email: str
    display_name: str
    bindings: tuple[RoleBinding, ...]
