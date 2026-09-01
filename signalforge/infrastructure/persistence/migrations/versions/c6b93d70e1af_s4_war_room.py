"""s4-t03 war room: rooms, membership, pages and the task board.

war_rooms is the room an incident is run from, unique on the incident so one
declaration produces one room and one set of pages. war_room_members records why
somebody is there, keeping required, paged and present as three separate facts.
incident_pages is the ack board: one row per person asked, carrying its own
stored ack deadline so the countdown survives a restart and agrees between the
screen and the escalation sweep. incident_tasks is the commitment board, with a
nullable owner beside the label a bridge actually said and a unique harvest key
that makes re-reading a transcript produce one task rather than one per sweep
(REQ-045).

Revision ID: c6b93d70e1af
Revises: a4c81e6f27b3
Create Date: 2026-09-01 16:05:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import signalforge.infrastructure.persistence.models


revision: str = 'c6b93d70e1af'
down_revision: Union[str, None] = 'a4c81e6f27b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TZ = signalforge.infrastructure.persistence.models.TZDateTime


def upgrade() -> None:
    op.create_table(
        'war_rooms',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('incident_id', sa.String(length=36), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=False),
        sa.Column('opened_at', TZ(), nullable=False),
        sa.Column('opened_by', sa.String(length=36), nullable=False),
        sa.Column('severity', sa.String(length=4), nullable=False),
        sa.Column('radius', sa.JSON(), nullable=False),
        sa.Column('gaps', sa.JSON(), nullable=False),
        sa.Column('bridge_channel', sa.String(length=200), nullable=False),
        sa.Column('bridge_adapter_id', sa.String(length=64), nullable=False),
        sa.Column('bridge_binding_id', sa.String(length=36), nullable=False),
        sa.Column('closed_at', TZ(), nullable=True),
        sa.Column('last_harvest_at', TZ(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'incident_id', name='uq_war_rooms_incident'),
    )
    op.create_index('ix_war_rooms_tenant_id', 'war_rooms', ['tenant_id'])
    op.create_index(
        'ix_war_rooms_open', 'war_rooms', ['closed_at', 'bridge_binding_id']
    )

    op.create_table(
        'war_room_members',
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('incident_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('role', sa.String(length=30), nullable=True),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('display_name', sa.String(length=200), nullable=False),
        sa.Column('added_at', TZ(), nullable=False),
        sa.Column('required', sa.Boolean(), nullable=False),
        sa.Column('paged', sa.Boolean(), nullable=False),
        sa.Column('present', sa.Boolean(), nullable=False),
        sa.Column('joined_at', TZ(), nullable=True),
        sa.Column('last_seen_at', TZ(), nullable=True),
        sa.PrimaryKeyConstraint('tenant_id', 'incident_id', 'user_id'),
    )
    op.create_index(
        'ix_war_room_members_incident',
        'war_room_members',
        ['tenant_id', 'incident_id'],
    )

    op.create_table(
        'incident_pages',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('incident_id', sa.String(length=36), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=False),
        sa.Column('recipient', sa.String(length=36), nullable=False),
        sa.Column('recipient_display', sa.String(length=200), nullable=False),
        sa.Column('role', sa.String(length=30), nullable=True),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('severity', sa.String(length=4), nullable=False),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('created_at', TZ(), nullable=False),
        sa.Column('ack_timeout_s', sa.Integer(), nullable=False),
        sa.Column('paged_at', TZ(), nullable=True),
        sa.Column('ack_deadline_at', TZ(), nullable=True),
        sa.Column('acked_at', TZ(), nullable=True),
        sa.Column('acked_by', sa.String(length=36), nullable=True),
        sa.Column('ack_note', sa.Text(), nullable=False),
        sa.Column('escalated_at', TZ(), nullable=True),
        sa.Column('escalated_to_page_id', sa.String(length=36), nullable=True),
        sa.Column('escalated_from_page_id', sa.String(length=36), nullable=True),
        sa.Column('hop', sa.Integer(), nullable=False),
        sa.Column('rotation_id', sa.String(length=36), nullable=True),
        sa.Column('policy_id', sa.String(length=36), nullable=True),
        sa.Column('policy_step', sa.Integer(), nullable=False),
        sa.Column('detail', sa.Text(), nullable=False),
        sa.Column('deliveries', sa.JSON(), nullable=False),
        sa.Column('requested_by', sa.String(length=36), nullable=False),
        sa.Column('requested_by_type', sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_incident_pages_tenant_id', 'incident_pages', ['tenant_id'])
    op.create_index(
        'ix_incident_pages_incident',
        'incident_pages',
        ['tenant_id', 'incident_id', 'created_at'],
    )
    op.create_index(
        'ix_incident_pages_recipient',
        'incident_pages',
        ['tenant_id', 'recipient', 'state'],
    )
    # The escalation sweep's cross-tenant work list.
    op.create_index(
        'ix_incident_pages_deadline',
        'incident_pages',
        ['state', 'ack_deadline_at'],
    )
    op.create_index(
        'ix_incident_pages_chain',
        'incident_pages',
        ['tenant_id', 'escalated_from_page_id'],
    )

    op.create_table(
        'incident_tasks',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('incident_id', sa.String(length=36), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('created_at', TZ(), nullable=False),
        sa.Column('created_by', sa.String(length=200), nullable=False),
        sa.Column('owner_user_id', sa.String(length=36), nullable=True),
        sa.Column('owner_label', sa.String(length=200), nullable=False),
        sa.Column('due_at', TZ(), nullable=True),
        sa.Column('due_label', sa.String(length=60), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('evidence_ref', sa.Text(), nullable=False),
        sa.Column('harvest_key', sa.String(length=40), nullable=True),
        sa.Column('completed_at', TZ(), nullable=True),
        sa.Column('completed_by', sa.String(length=36), nullable=True),
        sa.Column('updated_at', TZ(), nullable=True),
        sa.Column('detail', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'incident_id', 'harvest_key', name='uq_incident_tasks_harvest'
        ),
    )
    op.create_index('ix_incident_tasks_tenant_id', 'incident_tasks', ['tenant_id'])
    op.create_index(
        'ix_incident_tasks_incident',
        'incident_tasks',
        ['tenant_id', 'incident_id', 'created_at'],
    )
    op.create_index(
        'ix_incident_tasks_owner',
        'incident_tasks',
        ['tenant_id', 'owner_user_id', 'status'],
    )


def downgrade() -> None:
    op.drop_index('ix_incident_tasks_owner', table_name='incident_tasks')
    op.drop_index('ix_incident_tasks_incident', table_name='incident_tasks')
    op.drop_index('ix_incident_tasks_tenant_id', table_name='incident_tasks')
    op.drop_table('incident_tasks')
    op.drop_index('ix_incident_pages_chain', table_name='incident_pages')
    op.drop_index('ix_incident_pages_deadline', table_name='incident_pages')
    op.drop_index('ix_incident_pages_recipient', table_name='incident_pages')
    op.drop_index('ix_incident_pages_incident', table_name='incident_pages')
    op.drop_index('ix_incident_pages_tenant_id', table_name='incident_pages')
    op.drop_table('incident_pages')
    op.drop_index('ix_war_room_members_incident', table_name='war_room_members')
    op.drop_table('war_room_members')
    op.drop_index('ix_war_rooms_open', table_name='war_rooms')
    op.drop_index('ix_war_rooms_tenant_id', table_name='war_rooms')
    op.drop_table('war_rooms')
