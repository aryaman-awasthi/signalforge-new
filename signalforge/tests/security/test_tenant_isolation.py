"""S1-T02/T05: cross-tenant isolation enforced in the repository layer (REQ-013).

API-level isolation tests live in test_authz_matrix.py; this file proves the
repositories themselves refuse to cross tenants, so no handler can forget.
"""

from datetime import datetime, timedelta, timezone

import pytest

from signalforge.contracts.roles import Role
from signalforge.domain.identity.entities import (
    Invite,
    RoleBinding,
    Session,
    Tenant,
    User,
)
from signalforge.domain.identity.oncall import (
    EscalationPolicy,
    EscalationStep,
    EscalationTargetType,
    OnCallRotation,
)
from signalforge.infrastructure.persistence.repositories import (
    SqlEscalationRepository,
    SqlInviteRepository,
    SqlOnCallRepository,
    SqlRoleBindingRepository,
    SqlSessionRepository,
    SqlTenantRepository,
    SqlUserRepository,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


async def seed_two_tenants(session):
    tenants = SqlTenantRepository(session)
    users = SqlUserRepository(session)
    for n in ("1", "2"):
        await tenants.add(Tenant(id=f"t{n}", name=f"Org {n}", slug=f"org-{n}", created_at=NOW))
        await users.add(
            User(
                id=f"u{n}", tenant_id=f"t{n}", email=f"user{n}@example.com",
                display_name=f"User {n}", password_hash="h", created_at=NOW,
            )
        )
    await session.commit()


@pytest.mark.req("REQ-013")
async def test_user_lookup_filters_by_tenant(db_session):
    await seed_two_tenants(db_session)
    users = SqlUserRepository(db_session)
    assert (await users.get("t1", "u1")) is not None
    assert (await users.get("t2", "u1")) is None, "tenant 2 must not see tenant 1's user"
    assert (await users.get_by_email("t2", "user1@example.com")) is None
    listing = await users.list("t2")
    assert [u.id for u in listing] == ["u2"]


@pytest.mark.req("REQ-013")
async def test_role_bindings_scoped_by_tenant(db_session):
    await seed_two_tenants(db_session)
    bindings = SqlRoleBindingRepository(db_session)
    await bindings.replace_for_user(
        "t1", "u1", [RoleBinding(tenant_id="t1", user_id="u1", role=Role.ORG_OWNER)]
    )
    await db_session.commit()
    assert await bindings.list_for_user("t2", "u1") == []


@pytest.mark.req("REQ-013")
async def test_sessions_and_invites_scoped_by_tenant(db_session):
    await seed_two_tenants(db_session)
    sessions = SqlSessionRepository(db_session)
    invites = SqlInviteRepository(db_session)
    await sessions.add(
        Session(
            id="s1", tenant_id="t1", user_id="u1", family_id="f1",
            refresh_token_hash="rth", issued_at=NOW, expires_at=NOW + timedelta(days=14),
        )
    )
    await invites.add(
        Invite(
            id="i1", tenant_id="t1", email="new@example.com", roles=(Role.SME,),
            app_id=None, token_hash="ith", created_by="u1", created_at=NOW,
            expires_at=NOW + timedelta(days=7),
        )
    )
    await db_session.commit()
    assert (await sessions.get("t2", "s1")) is None
    assert (await invites.get("t2", "i1")) is None
    assert await invites.list("t2") == []


@pytest.mark.req("REQ-013")
async def test_oncall_and_escalation_scoped_by_tenant(db_session):
    await seed_two_tenants(db_session)
    oncall = SqlOnCallRepository(db_session)
    escalation = SqlEscalationRepository(db_session)
    await oncall.upsert(
        OnCallRotation(id="r1", tenant_id="t1", name="primary", user_ids=("u1",))
    )
    await escalation.upsert(
        EscalationPolicy(
            id="e1", tenant_id="t1", name="sev1",
            steps=(EscalationStep(EscalationTargetType.USER, "u1", timeout_s=300),),
        )
    )
    await db_session.commit()
    assert (await oncall.get("t2", "r1")) is None
    assert (await escalation.get("t2", "e1")) is None
    assert await oncall.delete("t2", "r1") is False, "cross-tenant delete must be a no-op"
    assert (await oncall.get("t1", "r1")) is not None, "the row must survive the attempt"


@pytest.mark.req("REQ-013")
async def test_session_family_revocation_is_tenant_scoped(db_session):
    await seed_two_tenants(db_session)
    sessions = SqlSessionRepository(db_session)
    await sessions.add(
        Session(
            id="s1", tenant_id="t1", user_id="u1", family_id="shared-family-id",
            refresh_token_hash="a", issued_at=NOW, expires_at=NOW + timedelta(days=14),
        )
    )
    await sessions.add(
        Session(
            id="s2", tenant_id="t2", user_id="u2", family_id="shared-family-id",
            refresh_token_hash="b", issued_at=NOW, expires_at=NOW + timedelta(days=14),
        )
    )
    await db_session.commit()
    revoked = await sessions.revoke_family("t1", "shared-family-id", "reuse", NOW)
    await db_session.commit()
    assert revoked == 1
    assert (await sessions.get("t2", "s2")).revoked_at is None
