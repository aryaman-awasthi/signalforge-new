"""S1-T01: identity entities and on-call/escalation value objects (REQ-010, REQ-014)."""

from datetime import datetime, timedelta, timezone

import pytest

from signalforge.contracts.roles import Role
from signalforge.domain.identity.entities import (
    Invite,
    Session,
    Tenant,
    User,
    normalize_email,
)
from signalforge.domain.identity.errors import DomainValidationError
from signalforge.domain.identity.oncall import (
    EscalationPolicy,
    EscalationStep,
    EscalationTargetType,
    OnCallRotation,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


@pytest.mark.req("REQ-010")
def test_email_normalized_and_validated():
    assert normalize_email("  Alice@Example.COM ") == "alice@example.com"
    for bad in ["", "no-at-sign", "a@", "@b", "a b@c.d"]:
        with pytest.raises(DomainValidationError):
            normalize_email(bad)


@pytest.mark.req("REQ-010")
def test_entities_carry_tenant_id():
    tenant = Tenant(id="t1", name="Acme", slug="acme", created_at=NOW)
    user = User(
        id="u1", tenant_id=tenant.id, email="a@b.co", display_name="A",
        password_hash="x", created_at=NOW,
    )
    invite = Invite(
        id="i1", tenant_id=tenant.id, email="c@d.co", roles=(Role.SME,),
        app_id=None, token_hash="h", created_by=user.id, created_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )
    session = Session(
        id="s1", tenant_id=tenant.id, user_id=user.id, family_id="f1",
        refresh_token_hash="h", issued_at=NOW, expires_at=NOW + timedelta(days=14),
    )
    for obj in (user, invite, session):
        assert obj.tenant_id == "t1"


@pytest.mark.req("REQ-010")
def test_invite_lifecycle_windows():
    invite = Invite(
        id="i1", tenant_id="t1", email="c@d.co", roles=(Role.SME,), app_id=None,
        token_hash="h", created_by="u1", created_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )
    assert invite.is_open(NOW)
    assert not invite.is_open(NOW + timedelta(days=8)), "expired invite is closed"
    invite.accepted_at = NOW + timedelta(hours=1)
    assert not invite.is_open(NOW + timedelta(hours=2)), "accepted invite is closed"


@pytest.mark.req("REQ-010")
def test_session_active_until_expiry_or_revocation():
    session = Session(
        id="s1", tenant_id="t1", user_id="u1", family_id="f1",
        refresh_token_hash="h", issued_at=NOW, expires_at=NOW + timedelta(days=14),
    )
    assert session.is_active(NOW)
    assert not session.is_active(NOW + timedelta(days=15))
    session.revoked_at = NOW + timedelta(hours=1)
    session.revoked_reason = "logout"
    assert not session.is_active(NOW + timedelta(hours=2))


@pytest.mark.req("REQ-014")
def test_rotation_preserves_oncall_order_and_rotates():
    rotation = OnCallRotation(
        id="r1", tenant_id="t1", name="primary", user_ids=("u1", "u2", "u3"),
    )
    assert rotation.user_ids == ("u1", "u2", "u3"), "order is the on-call order"
    assert rotation.on_call(shifts_elapsed=0) == "u1"
    assert rotation.on_call(shifts_elapsed=1) == "u2"
    assert rotation.on_call(shifts_elapsed=3) == "u1"
    assert rotation.next_in_rotation("u2") == "u3"
    assert rotation.next_in_rotation("u3") == "u1"


@pytest.mark.req("REQ-014")
def test_rotation_validation():
    with pytest.raises(DomainValidationError):
        OnCallRotation(id="r1", tenant_id="t1", name="empty", user_ids=())
    with pytest.raises(DomainValidationError):
        OnCallRotation(id="r1", tenant_id="t1", name="dup", user_ids=("u1", "u1"))
    with pytest.raises(DomainValidationError):
        OnCallRotation(
            id="r1", tenant_id="t1", name="bad", user_ids=("u1",),
            handoff_interval_hours=0,
        )


@pytest.mark.req("REQ-014")
def test_escalation_policy_timers_are_cumulative():
    policy = EscalationPolicy(
        id="e1", tenant_id="t1", name="sev1",
        steps=(
            EscalationStep(EscalationTargetType.ROTATION, "r1", timeout_s=300),
            EscalationStep(EscalationTargetType.USER, "u9", timeout_s=600),
            EscalationStep(EscalationTargetType.USER, "u10", timeout_s=900),
        ),
    )
    assert policy.page_offsets_s() == (0, 300, 900), (
        "each step pages when the previous step's ack timer elapses"
    )


@pytest.mark.req("REQ-014")
def test_escalation_policy_validation():
    with pytest.raises(DomainValidationError):
        EscalationPolicy(id="e1", tenant_id="t1", name="empty", steps=())
    with pytest.raises(DomainValidationError):
        EscalationStep(EscalationTargetType.USER, "u1", timeout_s=0)
    with pytest.raises(DomainValidationError):
        EscalationStep(EscalationTargetType.USER, "u1", timeout_s=86_401)
