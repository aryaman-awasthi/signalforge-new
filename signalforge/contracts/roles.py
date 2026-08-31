"""Roles matrix: roles, permissions, and the default grant matrix.

Frozen contract (CLAUDE.md rule 3). Role bindings are per app (Sprint 1); this
matrix is the default an org starts from. Invariants the tests pin down:
- approve_high_risk belongs only to incident command roles, never to
  Executive, Comms Lead, SME or Auditor.
- Auditor is strictly read-only (read_audit and nothing else).
- Only humans hold roles; agents act through the Tool Gateway under policy.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    ORG_OWNER = "org_owner"
    INCIDENT_COMMANDER = "incident_commander"
    TECH_LEAD = "tech_lead"
    SME = "sme"
    COMMS_LEAD = "comms_lead"
    EXECUTIVE = "executive"
    AUDITOR = "auditor"


class Permission(str, Enum):
    DECLARE_INCIDENT = "declare_incident"
    CHANGE_SEVERITY = "change_severity"
    APPROVE_HIGH_RISK = "approve_high_risk"
    APPROVE_EXEC_COMMS = "approve_exec_comms"
    APPROVE_CUSTOMER_COMMS = "approve_customer_comms"
    CLOSE_INCIDENT = "close_incident"
    MANAGE_ORG = "manage_org"
    MANAGE_APPS = "manage_apps"
    EDIT_POLICIES = "edit_policies"
    RUN_DRILLS = "run_drills"
    READ_AUDIT = "read_audit"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ORG_OWNER: frozenset(
        {
            Permission.MANAGE_ORG,
            Permission.MANAGE_APPS,
            Permission.EDIT_POLICIES,
            Permission.RUN_DRILLS,
            Permission.READ_AUDIT,
        }
    ),
    Role.INCIDENT_COMMANDER: frozenset(
        {
            Permission.DECLARE_INCIDENT,
            Permission.CHANGE_SEVERITY,
            Permission.APPROVE_HIGH_RISK,
            Permission.CLOSE_INCIDENT,
            Permission.RUN_DRILLS,
        }
    ),
    Role.TECH_LEAD: frozenset(
        {
            Permission.DECLARE_INCIDENT,
            Permission.CHANGE_SEVERITY,
            Permission.APPROVE_HIGH_RISK,
        }
    ),
    Role.SME: frozenset(),
    Role.COMMS_LEAD: frozenset({Permission.APPROVE_CUSTOMER_COMMS}),
    Role.EXECUTIVE: frozenset({Permission.APPROVE_EXEC_COMMS}),
    Role.AUDITOR: frozenset({Permission.READ_AUDIT}),
}


def role_has(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
