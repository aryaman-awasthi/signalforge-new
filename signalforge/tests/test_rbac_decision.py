"""S1-T01: RBAC decision service (REQ-012). Written failing-first per session protocol."""

import pytest

from signalforge.contracts.roles import ROLE_PERMISSIONS, Permission, Role
from signalforge.domain.identity.entities import RoleBinding
from signalforge.domain.identity.rbac import RBACDecisionService


def binding(role: Role, app_id: str | None = None) -> RoleBinding:
    return RoleBinding(tenant_id="t1", user_id="u1", role=role, app_id=app_id)


@pytest.mark.req("REQ-012")
def test_permissions_come_from_the_frozen_roles_matrix():
    for role in Role:
        for permission in Permission:
            decision = RBACDecisionService.decide([binding(role)], permission)
            assert decision.allowed == (permission in ROLE_PERMISSIONS[role]), (
                f"{role} x {permission} must match the contracts matrix"
            )


@pytest.mark.req("REQ-012")
def test_executive_denied_incident_command_permissions():
    for permission in (
        Permission.DECLARE_INCIDENT,
        Permission.APPROVE_HIGH_RISK,
        Permission.RUN_DRILLS,
        Permission.CLOSE_INCIDENT,
    ):
        decision = RBACDecisionService.decide([binding(Role.EXECUTIVE)], permission)
        assert not decision.allowed
        assert decision.reason, "denial must carry a reason for the audit event"


@pytest.mark.req("REQ-012")
def test_no_bindings_means_denied():
    decision = RBACDecisionService.decide([], Permission.READ_AUDIT)
    assert not decision.allowed
    assert decision.matched_roles == ()


@pytest.mark.req("REQ-012")
def test_org_wide_binding_applies_to_any_app():
    b = binding(Role.INCIDENT_COMMANDER, app_id=None)
    assert RBACDecisionService.decide([b], Permission.DECLARE_INCIDENT, app_id="app-a").allowed
    assert RBACDecisionService.decide([b], Permission.DECLARE_INCIDENT, app_id=None).allowed


@pytest.mark.req("REQ-012")
def test_role_bindings_are_per_app():
    b = binding(Role.INCIDENT_COMMANDER, app_id="app-a")
    assert RBACDecisionService.decide([b], Permission.DECLARE_INCIDENT, app_id="app-a").allowed
    denied = RBACDecisionService.decide([b], Permission.DECLARE_INCIDENT, app_id="app-b")
    assert not denied.allowed, "an app-scoped binding must not leak into another app"
    denied_global = RBACDecisionService.decide([b], Permission.DECLARE_INCIDENT, app_id=None)
    assert not denied_global.allowed, "an app-scoped binding must not grant org-wide access"


@pytest.mark.req("REQ-012")
def test_decision_names_the_matching_roles():
    bindings = [binding(Role.EXECUTIVE), binding(Role.INCIDENT_COMMANDER)]
    decision = RBACDecisionService.decide(bindings, Permission.APPROVE_HIGH_RISK)
    assert decision.allowed
    assert Role.INCIDENT_COMMANDER in decision.matched_roles
    assert Role.EXECUTIVE not in decision.matched_roles
