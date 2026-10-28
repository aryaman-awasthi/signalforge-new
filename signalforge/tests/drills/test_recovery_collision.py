"""Recovery Collision, run from the file (REQ-071, REQ-070).

The scenario is data. This is the six lines that run it, plus the assertions
that belong to the *suite* rather than to the drill: that the whole thing held
without a model, that the fault never left the drill's own scope, and that the
audit chain came out of it intact.

Deleting drills/ leaves a working product (CLAUDE.md rule 7), so this skips and
says why.
"""

from __future__ import annotations

import pytest

from signalforge.domain.drills.errors import ScenarioError

pytestmark = pytest.mark.anyio

DRILL = "recovery-collision.yaml"


@pytest.fixture
def collision(scenario_file):
    try:
        return scenario_file(DRILL)
    except ScenarioError as exc:  # pragma: no cover - only when drills/ is deleted
        pytest.skip(f"the flagship drill is demo data and is not installed: {exc}")


@pytest.mark.req("REQ-071", "REQ-070")
async def test_recovery_collision_runs_from_the_file(collision, drill_engine, api):
    assert collision.requires_model is False, (
        "the release-critical drill must hold with no gateway at all"
    )
    assert api.container.llm_settings.api_key in (None, ""), (
        "this suite resolves model settings from an empty environment"
    )

    run = await drill_engine.run(collision)

    assert run.passed, run.summary()
    checkpoints = run.checkpoints
    assert len(checkpoints) >= 25, (
        f"a flagship drill that checked {len(checkpoints)} things is a smoke test"
    )
    assert all(record.ok for record in checkpoints)

    # The incident really happened, and it really ended.
    assert run.captures["closed"]["state"] == "closed"
    assert run.captures["incident"]["severity"] == "P0"


@pytest.mark.req("REQ-071", "REQ-033")
async def test_the_drill_never_leaves_its_own_scope(collision, drill_engine, drill_world):
    await drill_engine.run(collision)
    for injection in drill_world.state.injections.values():
        assert injection["scope"] == collision.scope


@pytest.mark.req("REQ-071", "REQ-016")
async def test_the_audit_chain_survives_the_whole_incident(collision, drill_engine, api):
    """Everything above happened; the chain still verifies.

    A game day that produced two hundred audit entries and a broken chain would
    be evidence of a bug, not of a rehearsal.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from signalforge.infrastructure.security.audit import verify_chain

    run = await drill_engine.run(collision)
    tenant_id = run.captures["owner"]["org"]["id"]
    engine = create_async_engine(api.container.settings.db_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            broken = await verify_chain(session, tenant_id)
    finally:
        await engine.dispose()
    assert broken == [], f"audit chain broken at seq {broken}"

    topics = api.audit_topics(tenant_id)
    for expected in (
        "incident.declared",
        "execution.requested",
        "execution.blocked",
        "execution.approval.granted",
        "execution.uncertain",
        "execution.retry.blocked",
        "execution.reconciled",
        "execution.duplicate.prevented",
        "validation.completed",
        "incident.closure.blocked",
        "incident.closed",
    ):
        assert expected in topics, f"{expected} never reached the audit chain"
