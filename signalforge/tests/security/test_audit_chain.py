"""S1-T02: immutable audit hash chain (REQ-016)."""

import pytest
from sqlalchemy import select, update

from signalforge.infrastructure.persistence.models import AuditLogRow
from signalforge.infrastructure.security.audit import AuditChainWriter, verify_chain

pytestmark = pytest.mark.anyio


async def write_three(session, clock, tenant_id="t1"):
    writer = AuditChainWriter(session, clock=clock)
    for i in range(3):
        await writer.append(
            tenant_id=tenant_id, topic="audit.test.event",
            actor_type="human", actor_id="u1", payload={"n": i},
        )
    await session.commit()
    return writer


@pytest.mark.req("REQ-016")
async def test_chain_links_and_verifies(db_session, clock):
    await write_three(db_session, clock)
    rows = (
        await db_session.execute(
            select(AuditLogRow).where(AuditLogRow.tenant_id == "t1").order_by(AuditLogRow.seq)
        )
    ).scalars().all()
    assert len(rows) == 3
    assert rows[0].prev_hash is None, "genesis entry has no predecessor"
    assert rows[1].prev_hash == rows[0].payload_hash
    assert rows[2].prev_hash == rows[1].payload_hash
    assert await verify_chain(db_session, "t1") == []


@pytest.mark.req("REQ-016")
async def test_payload_tampering_is_detected(db_session, clock):
    await write_three(db_session, clock)
    target = (
        await db_session.execute(
            select(AuditLogRow).where(AuditLogRow.tenant_id == "t1").order_by(AuditLogRow.seq)
        )
    ).scalars().all()[1]
    await db_session.execute(
        update(AuditLogRow)
        .where(AuditLogRow.seq == target.seq)
        .values(payload='{"n": 99}')
    )
    await db_session.commit()
    broken = await verify_chain(db_session, "t1")
    assert target.seq in broken


@pytest.mark.req("REQ-016")
async def test_rewriting_hash_breaks_the_link_to_the_next_entry(db_session, clock):
    from signalforge.contracts.envelope import canonical_payload_hash

    await write_three(db_session, clock)
    target = (
        await db_session.execute(
            select(AuditLogRow).where(AuditLogRow.tenant_id == "t1").order_by(AuditLogRow.seq)
        )
    ).scalars().all()[1]
    forged_payload = '{"n":99}'
    await db_session.execute(
        update(AuditLogRow)
        .where(AuditLogRow.seq == target.seq)
        .values(payload=forged_payload, payload_hash=canonical_payload_hash({"n": 99}))
    )
    await db_session.commit()
    broken = await verify_chain(db_session, "t1")
    assert broken, "a consistent-looking rewrite still breaks the next entry's prev link"


@pytest.mark.req("REQ-016")
async def test_chains_are_per_tenant(db_session, clock):
    await write_three(db_session, clock, tenant_id="t1")
    await write_three(db_session, clock, tenant_id="t2")
    assert await verify_chain(db_session, "t1") == []
    assert await verify_chain(db_session, "t2") == []
    first_t2 = (
        await db_session.execute(
            select(AuditLogRow).where(AuditLogRow.tenant_id == "t2").order_by(AuditLogRow.seq)
        )
    ).scalars().first()
    assert first_t2.prev_hash is None, "tenant chains never cross"


@pytest.mark.req("REQ-016")
def test_audit_cannot_be_disabled():
    """The policy floor: there is no off switch to find."""
    import inspect

    signature = inspect.signature(AuditChainWriter.__init__)
    assert "enabled" not in signature.parameters
    assert "disabled" not in signature.parameters
    source = inspect.getsource(AuditChainWriter)
    assert "enabled" not in source, "no enable/disable flag may exist in the writer"
