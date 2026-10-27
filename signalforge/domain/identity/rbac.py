"""RBAC decision service (REQ-012).

Permissions come only from the frozen roles matrix in contracts (CLAUDE.md
rule 3). Bindings are per app: an app-scoped binding never grants access in
another app or org-wide; an org-wide binding covers every app. Every decision
carries the data the audit event needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from signalforge.contracts.roles import ROLE_PERMISSIONS, Permission, Role
from signalforge.domain.identity.entities import RoleBinding


@dataclass(frozen=True)
class Decision:
    allowed: bool
    permission: Permission
    app_id: Optional[str]
    matched_roles: tuple[Role, ...]
    reason: str


class RBACDecisionService:
    @staticmethod
    def visible_apps(bindings: Iterable[RoleBinding]) -> Optional[frozenset[str]]:
        """Which applications a person's bindings *name*, for reads that carry
        no permission.

        `None` means every application in the tenant: at least one binding is
        org-wide, whatever its role. Otherwise the set of app_ids the bindings
        are scoped to. An Incident Commander bound to `payments` can open
        `payments`; an SME holding no permission at all can still open the
        application they were invited for - reading what you are responsible
        for is not a permission in the frozen matrix, it is membership.
        """
        ids: set[str] = set()
        for binding in bindings:
            if binding.app_id is None:
                return None
            ids.add(binding.app_id)
        return frozenset(ids)

    @staticmethod
    def decide(
        bindings: Iterable[RoleBinding],
        permission: Permission,
        app_id: Optional[str] = None,
    ) -> Decision:
        applicable = [
            b for b in bindings
            if b.app_id is None or (app_id is not None and b.app_id == app_id)
        ]
        matched = tuple(
            dict.fromkeys(  # preserve order, dedupe roles bound more than once
                b.role for b in applicable if permission in ROLE_PERMISSIONS[b.role]
            )
        )
        if matched:
            scope = f"app {app_id}" if app_id else "org-wide"
            return Decision(
                allowed=True,
                permission=permission,
                app_id=app_id,
                matched_roles=matched,
                reason=f"granted by {', '.join(r.value for r in matched)} ({scope})",
            )
        held = ", ".join(dict.fromkeys(b.role.value for b in applicable)) or "none"
        return Decision(
            allowed=False,
            permission=permission,
            app_id=app_id,
            matched_roles=(),
            reason=f"no applicable role grants {permission.value} (roles held: {held})",
        )
