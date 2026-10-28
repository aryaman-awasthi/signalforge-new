"""S0-T02: roles matrix invariants."""

import pytest

from signalforge.contracts.roles import ROLE_PERMISSIONS, Permission, Role, role_has


@pytest.mark.req("REQ-005")
def test_every_role_has_a_grant_set():
    assert set(ROLE_PERMISSIONS) == set(Role)


@pytest.mark.req("REQ-005")
def test_every_permission_is_grantable_somewhere():
    granted = set().union(*ROLE_PERMISSIONS.values())
    assert granted == set(Permission)


@pytest.mark.req("REQ-005")
def test_high_risk_approval_restricted_to_incident_command():
    holders = {r for r in Role if role_has(r, Permission.APPROVE_HIGH_RISK)}
    assert holders == {Role.INCIDENT_COMMANDER, Role.TECH_LEAD}


@pytest.mark.req("REQ-005")
def test_auditor_is_strictly_read_only():
    assert ROLE_PERMISSIONS[Role.AUDITOR] == frozenset({Permission.READ_AUDIT})


@pytest.mark.req("REQ-005")
def test_executive_cannot_run_incidents():
    for p in [
        Permission.DECLARE_INCIDENT,
        Permission.CHANGE_SEVERITY,
        Permission.CLOSE_INCIDENT,
        Permission.APPROVE_HIGH_RISK,
    ]:
        assert not role_has(Role.EXECUTIVE, p)
    assert role_has(Role.EXECUTIVE, Permission.APPROVE_EXEC_COMMS)


@pytest.mark.req("REQ-005")
def test_comms_separation_of_duties():
    assert role_has(Role.COMMS_LEAD, Permission.APPROVE_CUSTOMER_COMMS)
    assert not role_has(Role.COMMS_LEAD, Permission.APPROVE_EXEC_COMMS)
    assert not role_has(Role.EXECUTIVE, Permission.APPROVE_CUSTOMER_COMMS)


@pytest.mark.req("REQ-005")
def test_only_humans_administer_the_org():
    holders = {r for r in Role if role_has(r, Permission.MANAGE_ORG)}
    assert holders == {Role.ORG_OWNER}
