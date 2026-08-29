"""Add send_welcome to voice_config

Revision ID: a1b2c3d4e5f6
Revises: d8e13f86cd9c
Create Date: 2026-07-22 16:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'd8e13f86cd9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add send_welcome column to voice_config."""
    op.add_column('voice_config', sa.Column('send_welcome', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Remove send_welcome column from voice_config."""
    op.drop_column('voice_config', 'send_welcome')
