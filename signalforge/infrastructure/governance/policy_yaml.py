"""Per-application policy YAML (REQ-060).

The document an operator writes and the document the engine evaluates are the
same object: this module only translates. Every rule that could weaken the
platform floor is refused by `PolicyDocument.__post_init__` and
`assert_against_actions`, not here - a codec that enforced the floor would be a
second implementation of it, and two implementations of a safety floor is one
too many.

What this module *does* refuse is shape: an unknown top-level key, a key whose
presence would imply a capability the platform does not have (`audit: false`),
or a value of the wrong type. Refusing an unknown key rather than ignoring it is
deliberate. An operator who writes `auto_approve: true` and gets silence will
believe it worked.

    approval_ttl_s: 900
    stability_window_s: 900
    stability_samples: 3
    max_action_retries: 1
    revalidation_cycles_before_escalation: 3
    approver_roles: [incident_commander, tech_lead]
    blocked_classes: []
    require_approval_classes: [rollback, restart, failover, traffic_shift,
                               config_change, customer_impacting]
    actions:
      rollback-payments:
        require_approval: true
        approver_roles: [incident_commander]
        approval_ttl_s: 600
        note: "the payments rollback drains the queue first"
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

import yaml

from signalforge.contracts.canonical import ActionClass, RiskLevel
from signalforge.contracts.roles import Role
from signalforge.domain.governance.errors import (
    GovernanceValidationError,
    PolicyFloorViolation,
)
from signalforge.domain.governance.policy import (
    FORBIDDEN_POLICY_KEYS,
    ActionRule,
    PolicyDocument,
)
from signalforge.domain.governance.ports import PolicyCodec

MAX_YAML_BYTES = 64 * 1024

TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "approval_ttl_s",
        "stability_window_s",
        "stability_samples",
        "max_action_retries",
        "revalidation_cycles_before_escalation",
        "approver_roles",
        "blocked_classes",
        "require_approval_classes",
        "actions",
        "note",
    }
)

ACTION_KEYS: frozenset[str] = frozenset(
    {
        "blocked",
        "require_approval",
        "auto_execute",
        "risk_override",
        "approver_roles",
        "approval_ttl_s",
        "note",
    }
)


def parse_policy_yaml(
    text: str,
    *,
    tenant_id: str,
    app_id: str,
    version: int,
    at: datetime,
    author_id: str,
    author_label: str = "",
    note: str = "",
) -> PolicyDocument:
    """YAML text to a validated `PolicyDocument`, or an error a person can act on.

    Raises `PolicyFloorViolation` when the document contradicts the floor and
    `GovernanceValidationError` when it is merely malformed. The distinction
    matters to the caller: the first is audited as a refused unsafe instruction,
    the second is a typo.
    """
    if len(text.encode("utf-8")) > MAX_YAML_BYTES:
        raise GovernanceValidationError(
            f"a policy document may not exceed {MAX_YAML_BYTES} bytes"
        )
    try:
        document = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise GovernanceValidationError(f"the policy is not valid YAML: {exc}") from exc
    if not isinstance(document, Mapping):
        raise GovernanceValidationError(
            "a policy document is a mapping of settings, not a "
            f"{type(document).__name__}"
        )
    return policy_from_mapping(
        document,
        tenant_id=tenant_id,
        app_id=app_id,
        version=version,
        at=at,
        author_id=author_id,
        author_label=author_label,
        note=note or str(document.get("note") or ""),
        source=text,
    )


def policy_from_mapping(
    document: Mapping[str, Any],
    *,
    tenant_id: str,
    app_id: str,
    version: int,
    at: datetime,
    author_id: str,
    author_label: str = "",
    note: str = "",
    source: str = "",
) -> PolicyDocument:
    _assert_keys(document, TOP_LEVEL_KEYS, "policy")
    actions = document.get("actions") or {}
    if not isinstance(actions, Mapping):
        raise GovernanceValidationError(
            "`actions` maps an action id to its rules"
        )
    rules = {
        str(action_id): _rule(str(action_id), payload)
        for action_id, payload in actions.items()
    }
    defaults = PolicyDocument(
        tenant_id=tenant_id, app_id=app_id, version=1, created_at=at, author_id="seed"
    )
    return PolicyDocument(
        tenant_id=tenant_id,
        app_id=app_id,
        version=version,
        created_at=at,
        author_id=author_id,
        author_label=author_label,
        note=note,
        approval_ttl_s=_int(document, "approval_ttl_s", defaults.approval_ttl_s),
        stability_window_s=_int(
            document, "stability_window_s", defaults.stability_window_s
        ),
        stability_samples=_int(
            document, "stability_samples", defaults.stability_samples
        ),
        max_action_retries=_int(
            document, "max_action_retries", defaults.max_action_retries
        ),
        revalidation_cycles_before_escalation=_int(
            document,
            "revalidation_cycles_before_escalation",
            defaults.revalidation_cycles_before_escalation,
        ),
        blocked_classes=_action_classes(document.get("blocked_classes")),
        require_approval_classes=(
            _action_classes(document["require_approval_classes"])
            if "require_approval_classes" in document
            else defaults.require_approval_classes
        ),
        approver_roles=_roles(document.get("approver_roles")),
        action_rules=rules,
        source=source or dump_policy_yaml_from(document),
    )


def dump_policy_yaml(policy: PolicyDocument) -> str:
    """The document as an operator would write it. Used to seed the editor when
    a policy was created from defaults rather than typed."""
    payload: dict[str, Any] = {
        "approval_ttl_s": policy.approval_ttl_s,
        "stability_window_s": policy.stability_window_s,
        "stability_samples": policy.stability_samples,
        "max_action_retries": policy.max_action_retries,
        "revalidation_cycles_before_escalation": (
            policy.revalidation_cycles_before_escalation
        ),
        "approver_roles": [role.value for role in policy.approver_roles],
        "blocked_classes": sorted(c.value for c in policy.blocked_classes),
        "require_approval_classes": sorted(
            c.value for c in policy.require_approval_classes
        ),
        "actions": {
            action_id: {
                key: value
                for key, value in {
                    "blocked": rule.blocked or None,
                    "require_approval": rule.require_approval or None,
                    "auto_execute": rule.auto_execute or None,
                    "risk_override": (
                        rule.risk_override.value if rule.risk_override else None
                    ),
                    "approver_roles": [r.value for r in rule.approver_roles] or None,
                    "approval_ttl_s": rule.approval_ttl_s,
                    "note": rule.note or None,
                }.items()
                if value is not None
            }
            for action_id, rule in sorted(policy.action_rules.items())
        },
    }
    return yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)


def dump_policy_yaml_from(document: Mapping[str, Any]) -> str:
    return yaml.safe_dump(dict(document), sort_keys=False, default_flow_style=False)


class YamlPolicyCodec(PolicyCodec):
    """The shipped codec. Stateless, so the container holds one."""

    def parse(
        self,
        text: str,
        *,
        tenant_id: str,
        app_id: str,
        version: int,
        at: datetime,
        author_id: str,
        author_label: str = "",
        note: str = "",
    ) -> PolicyDocument:
        return parse_policy_yaml(
            text,
            tenant_id=tenant_id,
            app_id=app_id,
            version=version,
            at=at,
            author_id=author_id,
            author_label=author_label,
            note=note,
        )

    def dump(self, policy: PolicyDocument) -> str:
        return policy.source or dump_policy_yaml(policy)


# --- internals -----------------------------------------------------------------


def _assert_keys(document: Mapping[str, Any], allowed: frozenset[str], what: str) -> None:
    forbidden = sorted(set(document) & FORBIDDEN_POLICY_KEYS)
    if forbidden:
        # Refused rather than ignored. An operator who wrote `audit: false` and
        # got silence would believe audit was off.
        raise PolicyFloorViolation(
            f"FLOOR-AUDIT-IMMUTABLE: {forbidden} cannot appear in a policy. The "
            "audit chain cannot be disabled, truncated or bypassed, so there is "
            "no setting for it"
        )
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise GovernanceValidationError(
            f"unknown {what} settings {unknown}; known settings are "
            f"{sorted(allowed)}"
        )


def _rule(action_id: str, payload: Any) -> ActionRule:
    if not isinstance(payload, Mapping):
        raise GovernanceValidationError(
            f"action {action_id!r} must map setting names to values"
        )
    _assert_keys(payload, ACTION_KEYS, f"action {action_id!r}")
    override = payload.get("risk_override")
    return ActionRule(
        action_id=action_id,
        blocked=bool(payload.get("blocked", False)),
        require_approval=bool(payload.get("require_approval", False)),
        auto_execute=bool(payload.get("auto_execute", False)),
        risk_override=_risk(override) if override else None,
        approver_roles=_roles(payload.get("approver_roles")),
        approval_ttl_s=(
            int(payload["approval_ttl_s"])
            if payload.get("approval_ttl_s") is not None
            else None
        ),
        note=str(payload.get("note") or "")[:400],
    )


def _int(document: Mapping[str, Any], key: str, default: int) -> int:
    value = document.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise GovernanceValidationError(
            f"{key} must be a whole number of seconds or a count, got {value!r}"
        ) from exc


def _risk(value: Any) -> RiskLevel:
    try:
        return RiskLevel(str(value))
    except ValueError as exc:
        raise GovernanceValidationError(
            f"{value!r} is not a risk level; one of "
            f"{[level.value for level in RiskLevel]}"
        ) from exc


def _roles(values: Optional[Sequence[Any]]) -> tuple[Role, ...]:
    if not values:
        return ()
    if isinstance(values, str):
        raise GovernanceValidationError("approver_roles is a list of role names")
    out: list[Role] = []
    for value in values:
        try:
            out.append(Role(str(value)))
        except ValueError as exc:
            raise GovernanceValidationError(
                f"{value!r} is not a role; one of {[r.value for r in Role]}"
            ) from exc
    return tuple(dict.fromkeys(out))


def _action_classes(values: Optional[Sequence[Any]]) -> frozenset[ActionClass]:
    if not values:
        return frozenset()
    if isinstance(values, str):
        raise GovernanceValidationError(
            "action-class lists are YAML lists, not a single string"
        )
    out: set[ActionClass] = set()
    for value in values:
        try:
            out.add(ActionClass(str(value)))
        except ValueError as exc:
            raise GovernanceValidationError(
                f"{value!r} is not an action class; one of "
                f"{[c.value for c in ActionClass]}"
            ) from exc
    return frozenset(out)
