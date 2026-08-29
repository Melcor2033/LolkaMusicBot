"""add_volume_to_settings

Revision ID: d8e13f86cd9c
Revises: 8e13f86cd9b2
Create Date: 2026-07-09 18:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8e13f86cd9c'
down_revision: Union[str, Sequence[str], None] = '8e13f86cd9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('lofi_config', sa.Column('volume', sa.Float(), server_default='0.5', nullable=False))
    op.add_column('ym_settings', sa.Column('volume', sa.Float(), server_default='0.5', nullable=False))
    op.add_column('spotify_settings', sa.Column('volume', sa.Float(), server_default='0.5', nullable=False))
    op.add_column('rutube_settings', sa.Column('volume', sa.Float(), server_default='0.5', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('rutube_settings', 'volume')
    op.drop_column('spotify_settings', 'volume')
    op.drop_column('ym_settings', 'volume')
    op.drop_column('lofi_config', 'volume')
