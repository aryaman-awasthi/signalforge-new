"""s5-t01 incident brain: versioned graph, confidence history, situation snapshots.

Four tables, and the constraints are the design.

`incident_graph_nodes` is unique on (tenant, incident, derived_key, version):
that is what makes a redelivered event, a restarted worker and two agents racing
produce one version of a belief rather than three, and it is why the version
history is a history rather than a heartbeat (REQ-050, REQ-021).

`incident_graph_edges` is unique on (tenant, incident, source, target, relation):
re-linking the same evidence to the same hypothesis must be a no-op, because a
duplicated edge would double that evidence's weight in the confidence policy and
move a number with no new evidence behind it (REQ-053).

`incident_confidence_history` is append-only with no update path in the
repository at all. It is the answer to "why did hypothesis #2 move from 65% to
32%", and a store that could rewrite it would make the answer worthless.

`incident_situation_snapshots` holds node *ids*, never content: the nodes are
immutable, so the ids reconstruct the view exactly, and a snapshot that copied
content would be a second copy of incident text with its own redaction problem.

Revision ID: a2e7c14b9d38
Revises: f1a4c98d5b62
Create Date: 2026-09-01 20:05:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import signalforge.infrastructure.persistence.models


revision: str = 'a2e7c14b9d38'
down_revision: Union[str, None] = 'f1a4c98d5b62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TZ = signalforge.infrastructure.persistence.models.TZDateTime


def upgrade() -> None:
    op.create_table(
        'incident_graph_nodes',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('incident_id', sa.String(length=100), nullable=False),
        sa.Column('node_type', sa.String(length=20), nullable=False),
        sa.Column('derived_key', sa.String(length=200), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('label', sa.String(length=400), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('untrusted', sa.Boolean(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('confirmation', sa.String(length=20), nullable=True),
        sa.Column('supersedes', sa.String(length=36), nullable=True),
        sa.Column('superseded_by', sa.String(length=36), nullable=True),
        sa.Column('retired_at', TZ(), nullable=True),
        sa.Column('retired_reason', sa.String(length=400), nullable=False),
        sa.Column('created_at', TZ(), nullable=False),
        sa.Column('provenance', sa.JSON(), nullable=False),
        sa.Column('tags', sa.JSON(), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'incident_id', 'derived_key', 'version',
            name='uq_graph_node_version',
        ),
    )
    op.create_index(
        'ix_incident_graph_nodes_tenant_id', 'incident_graph_nodes', ['tenant_id']
    )
    op.create_index(
        'ix_graph_nodes_incident',
        'incident_graph_nodes',
        ['tenant_id', 'incident_id', 'seq'],
    )
    op.create_index(
        'ix_graph_nodes_current',
        'incident_graph_nodes',
        ['tenant_id', 'incident_id', 'status', 'node_type'],
    )
    op.create_index(
        'ix_graph_nodes_key',
        'incident_graph_nodes',
        ['tenant_id', 'incident_id', 'derived_key'],
    )

    op.create_table(
        'incident_graph_edges',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('incident_id', sa.String(length=100), nullable=False),
        sa.Column('source_id', sa.String(length=36), nullable=False),
        sa.Column('target_id', sa.String(length=36), nullable=False),
        sa.Column('relation', sa.String(length=20), nullable=False),
        sa.Column('weight', sa.Float(), nullable=False),
        sa.Column('note', sa.String(length=400), nullable=False),
        sa.Column('created_at', TZ(), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'incident_id', 'source_id', 'target_id', 'relation',
            name='uq_graph_edge',
        ),
    )
    op.create_index(
        'ix_incident_graph_edges_tenant_id', 'incident_graph_edges', ['tenant_id']
    )
    op.create_index(
        'ix_graph_edges_target',
        'incident_graph_edges',
        ['tenant_id', 'incident_id', 'target_id'],
    )
    op.create_index(
        'ix_graph_edges_source',
        'incident_graph_edges',
        ['tenant_id', 'incident_id', 'source_id'],
    )

    op.create_table(
        'incident_confidence_history',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('incident_id', sa.String(length=100), nullable=False),
        sa.Column('hypothesis_key', sa.String(length=200), nullable=False),
        sa.Column('at', TZ(), nullable=False),
        sa.Column('previous', sa.Float(), nullable=True),
        sa.Column('current', sa.Float(), nullable=False),
        sa.Column('net_weight', sa.Float(), nullable=False),
        sa.Column('supporting', sa.JSON(), nullable=False),
        sa.Column('refuting', sa.JSON(), nullable=False),
        sa.Column('added', sa.JSON(), nullable=False),
        sa.Column('removed', sa.JSON(), nullable=False),
        sa.Column('trigger', sa.String(length=120), nullable=False),
        sa.Column('narrative', sa.Text(), nullable=False),
        sa.Column('narrative_degraded', sa.Boolean(), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_incident_confidence_history_tenant_id',
        'incident_confidence_history',
        ['tenant_id'],
    )
    op.create_index(
        'ix_confidence_history_hypothesis',
        'incident_confidence_history',
        ['tenant_id', 'incident_id', 'hypothesis_key', 'id'],
    )

    op.create_table(
        'incident_situation_snapshots',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('incident_id', sa.String(length=100), nullable=False),
        sa.Column('taken_at', TZ(), nullable=False),
        sa.Column('reason', sa.String(length=200), nullable=False),
        sa.Column('node_ids', sa.JSON(), nullable=False),
        sa.Column('hypotheses', sa.JSON(), nullable=False),
        sa.Column('topology_version', sa.String(length=120), nullable=False),
        sa.Column('max_seq', sa.Integer(), nullable=False),
        sa.Column('degraded', sa.Boolean(), nullable=False),
        sa.Column('fingerprint', sa.String(length=64), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_incident_situation_snapshots_tenant_id',
        'incident_situation_snapshots',
        ['tenant_id'],
    )
    op.create_index(
        'ix_situation_snapshots_incident',
        'incident_situation_snapshots',
        ['tenant_id', 'incident_id', 'taken_at'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_situation_snapshots_incident', table_name='incident_situation_snapshots'
    )
    op.drop_index(
        'ix_incident_situation_snapshots_tenant_id',
        table_name='incident_situation_snapshots',
    )
    op.drop_table('incident_situation_snapshots')
    op.drop_index(
        'ix_confidence_history_hypothesis', table_name='incident_confidence_history'
    )
    op.drop_index(
        'ix_incident_confidence_history_tenant_id',
        table_name='incident_confidence_history',
    )
    op.drop_table('incident_confidence_history')
    op.drop_index('ix_graph_edges_source', table_name='incident_graph_edges')
    op.drop_index('ix_graph_edges_target', table_name='incident_graph_edges')
    op.drop_index('ix_incident_graph_edges_tenant_id', table_name='incident_graph_edges')
    op.drop_table('incident_graph_edges')
    op.drop_index('ix_graph_nodes_key', table_name='incident_graph_nodes')
    op.drop_index('ix_graph_nodes_current', table_name='incident_graph_nodes')
    op.drop_index('ix_graph_nodes_incident', table_name='incident_graph_nodes')
    op.drop_index('ix_incident_graph_nodes_tenant_id', table_name='incident_graph_nodes')
    op.drop_table('incident_graph_nodes')
