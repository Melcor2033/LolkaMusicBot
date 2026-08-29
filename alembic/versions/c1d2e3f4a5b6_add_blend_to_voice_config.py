"""Add blend_enabled to voice_config

Revision ID: c1d2e3f4a5b6
Revises: f5e6d7c8b9a0
Create Date: 2026-07-23 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'f5e6d7c8b9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add blend_enabled column to voice_config safely."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('voice_config')]
    if 'blend_enabled' not in columns:
        op.add_column('voice_config', sa.Column('blend_enabled', sa.Boolean(), server_default='true', nullable=True))


def downgrade() -> None:
    """Remove blend_enabled column from voice_config."""
    op.drop_column('voice_config', 'blend_enabled')
