"""s4-t02 incident lifecycle: incidents, transitions and change windows.

incidents is the record of a decision a person made - declared, re-graded,
recovered, closed - with the detection proposal that was on screen at the moment
Declare was pressed copied onto the row, and the ITSM link's five-valued sync
state beside it. incident_transitions is append-only: who moved it, when, and
what they said. change_windows is the projection of deployment.* events, keyed
on (tenant, app, change_id) so a re-triggered pipeline is one window rather than
two, and it is what answers "is a change in flight right now" (REQ-043, REQ-044).

Revision ID: a4c81e6f27b3
Revises: d3a17c5be902
Create Date: 2026-09-01 14:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import signalforge.infrastructure.persistence.models


revision: str = 'a4c81e6f27b3'
down_revision: Union[str, None] = 'd3a17c5be902'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TZ = signalforge.infrastructure.persistence.models.TZDateTime


def upgrade() -> None:
    op.create_table(
        'incidents',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=False),
        sa.Column('reference', sa.String(length=40), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('severity', sa.String(length=4), nullable=False),
        sa.Column('severity_source', sa.String(length=10), nullable=False),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('stance_at_declare', sa.String(length=20), nullable=False),
        sa.Column('declared_at', TZ(), nullable=False),
        sa.Column('declared_by', sa.String(length=36), nullable=False),
        sa.Column('declared_by_display', sa.String(length=200), nullable=False),
        sa.Column('updated_at', TZ(), nullable=False),
        sa.Column('recovering_at', TZ(), nullable=True),
        sa.Column('closed_at', TZ(), nullable=True),
        sa.Column('closed_by', sa.String(length=36), nullable=True),
        sa.Column('closure_reason', sa.Text(), nullable=False),
        sa.Column('proposal', sa.JSON(), nullable=True),
        sa.Column('change_windows', sa.JSON(), nullable=False),
        sa.Column('ticket_state', sa.String(length=20), nullable=False),
        sa.Column('ticket_id', sa.String(length=120), nullable=True),
        sa.Column('ticket_reference', sa.String(length=120), nullable=True),
        sa.Column('ticket_status', sa.String(length=20), nullable=True),
        sa.Column('ticket_adapter_id', sa.String(length=64), nullable=True),
        sa.Column('ticket_binding_id', sa.String(length=36), nullable=True),
        sa.Column('ticket_attempts', sa.Integer(), nullable=False),
        sa.Column('ticket_detail', sa.Text(), nullable=False),
        sa.Column('ticket_last_attempt_at', TZ(), nullable=True),
        sa.Column('ticket_synced_at', TZ(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'reference', name='uq_incidents_reference'),
    )
    op.create_index('ix_incidents_app_state', 'incidents', ['tenant_id', 'app_id', 'state'])
    op.create_index(
        'ix_incidents_tenant_declared', 'incidents', ['tenant_id', 'declared_at']
    )
    op.create_index('ix_incidents_ticket_state', 'incidents', ['ticket_state'])
    op.create_index(op.f('ix_incidents_tenant_id'), 'incidents', ['tenant_id'])

    op.create_table(
        'incident_transitions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('incident_id', sa.String(length=36), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=False),
        sa.Column('from_state', sa.String(length=20), nullable=False),
        sa.Column('to_state', sa.String(length=20), nullable=False),
        sa.Column('at', TZ(), nullable=False),
        sa.Column('actor_type', sa.String(length=20), nullable=False),
        sa.Column('actor_id', sa.String(length=200), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('severity_before', sa.String(length=4), nullable=True),
        sa.Column('severity_after', sa.String(length=4), nullable=True),
        sa.Column('detail', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_incident_transitions_incident',
        'incident_transitions',
        ['tenant_id', 'incident_id', 'id'],
    )
    op.create_index(
        op.f('ix_incident_transitions_tenant_id'), 'incident_transitions', ['tenant_id']
    )

    op.create_table(
        'change_windows',
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=False),
        sa.Column('change_id', sa.String(length=120), nullable=False),
        sa.Column('change_type', sa.String(length=20), nullable=False),
        sa.Column('service', sa.String(length=200), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('started_at', TZ(), nullable=False),
        sa.Column('completed_at', TZ(), nullable=True),
        sa.Column('first_seen_at', TZ(), nullable=False),
        sa.Column('last_event_at', TZ(), nullable=False),
        sa.Column('version', sa.String(length=120), nullable=True),
        sa.Column('actor', sa.String(length=200), nullable=True),
        sa.Column('diff_ref', sa.String(length=300), nullable=True),
        sa.Column('source_system', sa.String(length=100), nullable=False),
        sa.Column('simulated', sa.Boolean(), nullable=False),
        sa.Column('announced_started', sa.Boolean(), nullable=False),
        sa.Column('announced_completed', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('tenant_id', 'app_id', 'change_id'),
    )
    op.create_index(
        'ix_change_windows_app_started',
        'change_windows',
        ['tenant_id', 'app_id', 'started_at'],
    )
    op.create_index('ix_change_windows_open', 'change_windows', ['announced_completed'])


def downgrade() -> None:
    op.drop_index('ix_change_windows_open', table_name='change_windows')
    op.drop_index('ix_change_windows_app_started', table_name='change_windows')
    op.drop_table('change_windows')

    op.drop_index(
        op.f('ix_incident_transitions_tenant_id'), table_name='incident_transitions'
    )
    op.drop_index('ix_incident_transitions_incident', table_name='incident_transitions')
    op.drop_table('incident_transitions')

    op.drop_index(op.f('ix_incidents_tenant_id'), table_name='incidents')
    op.drop_index('ix_incidents_ticket_state', table_name='incidents')
    op.drop_index('ix_incidents_tenant_declared', table_name='incidents')
    op.drop_index('ix_incidents_app_state', table_name='incidents')
    op.drop_table('incidents')
