"""s3-t02 shipped adapters: what the generic integrations remember.

sim_tickets (itsm-lite), sim_messages (teams-sim bridge and DMs),
notification_deliveries (every notification channel's receipt) and
knowledge_documents (kb-index). Simulated means the integrated *system* is
faked; the record of what SignalForge did is durable, or a rehearsal proves
nothing (REQ-031, REQ-033, REQ-035).

Revision ID: 4b8f2c1d7a90
Revises: 9eb3b6913aa0
Create Date: 2026-09-01 06:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import signalforge.infrastructure.persistence.models


revision: str = '4b8f2c1d7a90'
down_revision: Union[str, None] = '9eb3b6913aa0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TZ = signalforge.infrastructure.persistence.models.TZDateTime


def upgrade() -> None:
    op.create_table(
        'sim_tickets',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=False),
        sa.Column('ticket_id', sa.String(length=100), nullable=False),
        sa.Column('reference', sa.String(length=50), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=True),
        sa.Column('assignee', sa.String(length=200), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('links', sa.JSON(), nullable=False),
        sa.Column('created_at', TZ(), nullable=False),
        sa.Column('updated_at', TZ(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'app_id', 'ticket_id', name='uq_sim_tickets_id'),
    )
    op.create_index('ix_sim_tickets_tenant_app', 'sim_tickets', ['tenant_id', 'app_id'])
    op.create_index(op.f('ix_sim_tickets_tenant_id'), 'sim_tickets', ['tenant_id'])

    op.create_table(
        'sim_messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=True),
        sa.Column('channel', sa.String(length=200), nullable=False),
        sa.Column('message_id', sa.String(length=100), nullable=False),
        sa.Column('thread_id', sa.String(length=100), nullable=True),
        sa.Column('author_type', sa.String(length=20), nullable=False),
        sa.Column('author_id', sa.String(length=200), nullable=False),
        sa.Column('author_display', sa.String(length=200), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('sent_at', TZ(), nullable=False),
        sa.Column('scripted', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'message_id', name='uq_sim_messages_id'),
    )
    op.create_index(
        'ix_sim_messages_channel_time', 'sim_messages', ['tenant_id', 'channel', 'sent_at']
    )
    op.create_index(op.f('ix_sim_messages_tenant_id'), 'sim_messages', ['tenant_id'])

    op.create_table(
        'notification_deliveries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('notification_id', sa.String(length=100), nullable=False),
        sa.Column('adapter_id', sa.String(length=64), nullable=False),
        sa.Column('channel', sa.String(length=20), nullable=False),
        sa.Column('recipient', sa.String(length=200), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('at', TZ(), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=True),
        sa.Column('incident_id', sa.String(length=100), nullable=True),
        sa.Column('requires_ack', sa.Boolean(), nullable=False),
        sa.Column('simulated', sa.Boolean(), nullable=False),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('acked_by', sa.String(length=200), nullable=True),
        sa.Column('acked_at', TZ(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'notification_id', name='uq_notification_deliveries_id'
        ),
    )
    op.create_index(
        'ix_notification_deliveries_recipient',
        'notification_deliveries',
        ['tenant_id', 'recipient', 'at'],
    )
    op.create_index(
        'ix_notification_deliveries_incident',
        'notification_deliveries',
        ['tenant_id', 'incident_id'],
    )
    op.create_index(
        op.f('ix_notification_deliveries_tenant_id'),
        'notification_deliveries',
        ['tenant_id'],
    )

    op.create_table(
        'knowledge_documents',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('doc_id', sa.String(length=100), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=True),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('doc_type', sa.String(length=20), nullable=False),
        sa.Column('tags', sa.JSON(), nullable=False),
        sa.Column('tokens', sa.JSON(), nullable=False),
        sa.Column('embedding', sa.JSON(), nullable=False),
        sa.Column('embedding_model', sa.String(length=100), nullable=True),
        sa.Column('redaction_policy_id', sa.String(length=100), nullable=True),
        sa.Column('updated_at', TZ(), nullable=False),
        sa.Column('indexed_at', TZ(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'doc_id', name='uq_knowledge_documents_id'),
    )
    op.create_index(
        'ix_knowledge_documents_tenant_app', 'knowledge_documents', ['tenant_id', 'app_id']
    )
    op.create_index(
        op.f('ix_knowledge_documents_tenant_id'), 'knowledge_documents', ['tenant_id']
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_knowledge_documents_tenant_id'), table_name='knowledge_documents')
    op.drop_index('ix_knowledge_documents_tenant_app', table_name='knowledge_documents')
    op.drop_table('knowledge_documents')

    op.drop_index(
        op.f('ix_notification_deliveries_tenant_id'), table_name='notification_deliveries'
    )
    op.drop_index(
        'ix_notification_deliveries_incident', table_name='notification_deliveries'
    )
    op.drop_index(
        'ix_notification_deliveries_recipient', table_name='notification_deliveries'
    )
    op.drop_table('notification_deliveries')

    op.drop_index(op.f('ix_sim_messages_tenant_id'), table_name='sim_messages')
    op.drop_index('ix_sim_messages_channel_time', table_name='sim_messages')
    op.drop_table('sim_messages')

    op.drop_index(op.f('ix_sim_tickets_tenant_id'), table_name='sim_tickets')
    op.drop_index('ix_sim_tickets_tenant_app', table_name='sim_tickets')
    op.drop_table('sim_tickets')
