"""s3 adapter registry: connector_bindings, one app wired to one adapter at one
credential scope, with the health columns the integration health screen reads
(REQ-030). Credentials are not here - they stay Fernet-encrypted in `secrets`.

Revision ID: 9eb3b6913aa0
Revises: c7ed709bb625
Create Date: 2026-09-01 03:40:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import signalforge.infrastructure.persistence.models


revision: str = '9eb3b6913aa0'
down_revision: Union[str, None] = 'c7ed709bb625'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'connector_bindings',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=False),
        sa.Column('adapter_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('credential_scope', sa.String(length=20), nullable=False),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('credential_fields', sa.JSON(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column(
            'created_at',
            signalforge.infrastructure.persistence.models.TZDateTime(),
            nullable=False,
        ),
        sa.Column('created_by', sa.String(length=36), nullable=False),
        sa.Column(
            'updated_at',
            signalforge.infrastructure.persistence.models.TZDateTime(),
            nullable=False,
        ),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('status_detail', sa.Text(), nullable=True),
        sa.Column(
            'last_checked_at',
            signalforge.infrastructure.persistence.models.TZDateTime(),
            nullable=True,
        ),
        sa.Column(
            'last_ok_at',
            signalforge.infrastructure.persistence.models.TZDateTime(),
            nullable=True,
        ),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('consecutive_failures', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'app_id', 'adapter_id', 'credential_scope',
            name='uq_connector_bindings_scope',
        ),
    )
    op.create_index(
        'ix_connector_bindings_enabled', 'connector_bindings', ['enabled'], unique=False
    )
    op.create_index(
        'ix_connector_bindings_tenant_app',
        'connector_bindings',
        ['tenant_id', 'app_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_connector_bindings_tenant_id'),
        'connector_bindings',
        ['tenant_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_connector_bindings_tenant_id'), table_name='connector_bindings')
    op.drop_index('ix_connector_bindings_tenant_app', table_name='connector_bindings')
    op.drop_index('ix_connector_bindings_enabled', table_name='connector_bindings')
    op.drop_table('connector_bindings')
