"""s3-t04 onboarding: applications, wizard profiles and topology versions.

applications gives the tenant-scoped app_id every other table already uses a
name, a tier and a base URL. onboarding_profiles is one document per application
holding the map, actions, SLOs, severity matrix, policy defaults and baselines -
one row rather than a table per step, because the aggregate's invariants cross
the steps. topology_versions keeps every imported map forever, unique per
(tenant, app, version) so re-uploading an unchanged document is a no-op rather
than a second version (REQ-036).

Revision ID: d3a17c5be902
Revises: b7d24e0af315
Create Date: 2026-09-01 11:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import signalforge.infrastructure.persistence.models


revision: str = 'd3a17c5be902'
down_revision: Union[str, None] = 'b7d24e0af315'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TZ = signalforge.infrastructure.persistence.models.TZDateTime


def upgrade() -> None:
    op.create_table(
        'applications',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('tier', sa.String(length=20), nullable=False),
        sa.Column('environment', sa.String(length=20), nullable=False),
        sa.Column('base_url', sa.String(length=300), nullable=True),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('owner_team', sa.String(length=200), nullable=False),
        sa.Column('created_at', TZ(), nullable=False),
        sa.Column('created_by', sa.String(length=36), nullable=False),
        sa.Column('updated_at', TZ(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'app_id', name='uq_applications_app_id'),
    )
    op.create_index('ix_applications_tenant', 'applications', ['tenant_id', 'app_id'])
    op.create_index(op.f('ix_applications_tenant_id'), 'applications', ['tenant_id'])

    op.create_table(
        'onboarding_profiles',
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=False),
        sa.Column('signals', sa.JSON(), nullable=False),
        sa.Column('region_label', sa.String(length=64), nullable=True),
        sa.Column('regions', sa.JSON(), nullable=False),
        sa.Column('journeys', sa.JSON(), nullable=False),
        sa.Column('probes', sa.JSON(), nullable=False),
        sa.Column('actions', sa.JSON(), nullable=False),
        sa.Column('slos', sa.JSON(), nullable=False),
        sa.Column('severity_matrix', sa.JSON(), nullable=False),
        sa.Column('policy', sa.JSON(), nullable=False),
        sa.Column('baselines', sa.JSON(), nullable=False),
        sa.Column('topology', sa.JSON(), nullable=True),
        sa.Column('last_test', sa.JSON(), nullable=True),
        sa.Column('updated_at', TZ(), nullable=False),
        sa.PrimaryKeyConstraint('tenant_id', 'app_id'),
    )

    op.create_table(
        'topology_versions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=False),
        sa.Column('version', sa.String(length=120), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('node_count', sa.Integer(), nullable=False),
        sa.Column('edge_count', sa.Integer(), nullable=False),
        sa.Column('imported_at', TZ(), nullable=False),
        sa.Column('imported_by', sa.String(length=36), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('normalized_by', sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'app_id', 'version', name='uq_topology_versions_version'
        ),
    )
    op.create_index(
        'ix_topology_versions_app', 'topology_versions',
        ['tenant_id', 'app_id', 'imported_at'],
    )
    op.create_index(
        op.f('ix_topology_versions_tenant_id'), 'topology_versions', ['tenant_id']
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_topology_versions_tenant_id'), table_name='topology_versions')
    op.drop_index('ix_topology_versions_app', table_name='topology_versions')
    op.drop_table('topology_versions')

    op.drop_table('onboarding_profiles')

    op.drop_index(op.f('ix_applications_tenant_id'), table_name='applications')
    op.drop_index('ix_applications_tenant', table_name='applications')
    op.drop_table('applications')
