"""s5-t02 llm cache: model answers already paid for, keyed by input hash.

One table. The primary key is (tenant_id, cache_key) rather than the hash alone:
the hash is a pure function of the input, so two tenants asking the identical
question produce the identical key, and a shared row would let one tenant be
served an answer generated inside another's incident (REQ-013). The payload
holds the answer and its provenance and never the prompt, so a warm cache does
not become a second, unredacted copy of everything sent to a model (REQ-051).

Revision ID: f1a4c98d5b62
Revises: c6b93d70e1af
Create Date: 2026-09-01 18:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import signalforge.infrastructure.persistence.models


revision: str = 'f1a4c98d5b62'
down_revision: Union[str, None] = 'c6b93d70e1af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TZ = signalforge.infrastructure.persistence.models.TZDateTime


def upgrade() -> None:
    op.create_table(
        'llm_cache',
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('cache_key', sa.String(length=64), nullable=False),
        sa.Column('provider', sa.String(length=40), nullable=False),
        sa.Column('model', sa.String(length=120), nullable=False),
        sa.Column('purpose', sa.String(length=80), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False),
        sa.Column('completion_tokens', sa.Integer(), nullable=False),
        sa.Column('hits', sa.Integer(), nullable=False),
        sa.Column('created_at', TZ(), nullable=False),
        sa.PrimaryKeyConstraint('tenant_id', 'cache_key'),
    )
    op.create_index(
        'ix_llm_cache_tenant_created', 'llm_cache', ['tenant_id', 'created_at']
    )


def downgrade() -> None:
    op.drop_index('ix_llm_cache_tenant_created', table_name='llm_cache')
    op.drop_table('llm_cache')
