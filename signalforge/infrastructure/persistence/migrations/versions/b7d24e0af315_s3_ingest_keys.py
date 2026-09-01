"""s3-t03 push ingestion: ingest keys and the idempotency receipts.

ingest_keys holds one sending application's credential for one stream - the
sha256 of its bearer token and a display prefix, never the token itself; the
HMAC shared secret lives in `secrets` under ingest/<key_id>/secret.
ingest_receipts is the idempotency ledger the frozen contract's event_id names,
unique per (tenant, event_id) so a retry after a lost 202 is acknowledged
rather than applied twice (REQ-032, REQ-017).

Revision ID: b7d24e0af315
Revises: 4b8f2c1d7a90
Create Date: 2026-09-01 09:40:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import signalforge.infrastructure.persistence.models


revision: str = 'b7d24e0af315'
down_revision: Union[str, None] = '4b8f2c1d7a90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TZ = signalforge.infrastructure.persistence.models.TZDateTime


def upgrade() -> None:
    op.create_table(
        'ingest_keys',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=False),
        sa.Column('stream', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('token_prefix', sa.String(length=20), nullable=False),
        sa.Column('binding_id', sa.String(length=36), nullable=True),
        sa.Column('rotated_from', sa.String(length=36), nullable=True),
        sa.Column('created_at', TZ(), nullable=False),
        sa.Column('created_by', sa.String(length=36), nullable=False),
        sa.Column('revoked_at', TZ(), nullable=True),
        sa.Column('revoked_by', sa.String(length=36), nullable=True),
        sa.Column('last_used_at', TZ(), nullable=True),
        sa.Column('last_event_at', TZ(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('accepted_count', sa.Integer(), nullable=False),
        sa.Column('duplicate_count', sa.Integer(), nullable=False),
        sa.Column('rejected_count', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index('ix_ingest_keys_tenant_app', 'ingest_keys', ['tenant_id', 'app_id', 'stream'])
    op.create_index('ix_ingest_keys_binding', 'ingest_keys', ['tenant_id', 'binding_id'])
    op.create_index(op.f('ix_ingest_keys_tenant_id'), 'ingest_keys', ['tenant_id'])

    op.create_table(
        'ingest_receipts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('event_id', sa.String(length=36), nullable=False),
        sa.Column('key_id', sa.String(length=36), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=False),
        sa.Column('stream', sa.String(length=20), nullable=False),
        sa.Column('source_system', sa.String(length=100), nullable=False),
        sa.Column('occurred_at', TZ(), nullable=False),
        sa.Column('received_at', TZ(), nullable=False),
        sa.Column('platform_event_id', sa.String(length=36), nullable=False),
        sa.Column('event_seq', sa.Integer(), nullable=False),
        sa.Column('event_count', sa.Integer(), nullable=False),
        sa.Column('simulated', sa.Boolean(), nullable=False),
        sa.Column('normalized_by', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'event_id', name='uq_ingest_receipts_event'),
    )
    op.create_index(
        'ix_ingest_receipts_key', 'ingest_receipts', ['tenant_id', 'key_id', 'received_at']
    )
    op.create_index(
        'ix_ingest_receipts_stream', 'ingest_receipts', ['tenant_id', 'stream', 'received_at']
    )
    op.create_index(op.f('ix_ingest_receipts_tenant_id'), 'ingest_receipts', ['tenant_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_ingest_receipts_tenant_id'), table_name='ingest_receipts')
    op.drop_index('ix_ingest_receipts_stream', table_name='ingest_receipts')
    op.drop_index('ix_ingest_receipts_key', table_name='ingest_receipts')
    op.drop_table('ingest_receipts')

    op.drop_index(op.f('ix_ingest_keys_tenant_id'), table_name='ingest_keys')
    op.drop_index('ix_ingest_keys_binding', table_name='ingest_keys')
    op.drop_index('ix_ingest_keys_tenant_app', table_name='ingest_keys')
    op.drop_table('ingest_keys')
