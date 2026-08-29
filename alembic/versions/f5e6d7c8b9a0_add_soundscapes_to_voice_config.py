"""Add soundscapes_enabled to voice_config

Revision ID: f5e6d7c8b9a0
Revises: e4f5a6b7c8d9
Create Date: 2026-07-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5e6d7c8b9a0'
down_revision: Union[str, Sequence[str], None] = 'e4f5a6b7c8d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add soundscapes_enabled column to voice_config safely."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('voice_config')]
    if 'soundscapes_enabled' not in columns:
        op.add_column('voice_config', sa.Column('soundscapes_enabled', sa.Boolean(), server_default='true', nullable=True))


def downgrade() -> None:
    """Remove soundscapes_enabled column from voice_config."""
    op.drop_column('voice_config', 'soundscapes_enabled')
