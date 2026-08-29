"""Add ducking_enabled and ducking_level to voice_config

Revision ID: e4f5a6b7c8d9
Revises: a1b2c3d4e5f6
Create Date: 2026-07-23 00:22:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4f5a6b7c8d9'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add ducking_enabled and ducking_level columns to voice_config safely."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('voice_config')]
    if 'ducking_enabled' not in columns:
        op.add_column('voice_config', sa.Column('ducking_enabled', sa.Boolean(), server_default='true', nullable=True))
    if 'ducking_level' not in columns:
        op.add_column('voice_config', sa.Column('ducking_level', sa.Float(), server_default='0.35', nullable=True))


def downgrade() -> None:
    """Remove ducking_enabled and ducking_level columns from voice_config."""
    op.drop_column('voice_config', 'ducking_level')
    op.drop_column('voice_config', 'ducking_enabled')
