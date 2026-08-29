"""add_customization_and_lofi_tables

Revision ID: 398ce4644bc8
Revises: f13e7bd1ea69
Create Date: 2026-07-03 14:26:56.049751

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '398ce4644bc8'
down_revision: Union[str, Sequence[str], None] = 'f13e7bd1ea69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ym_settings',
        sa.Column('guild_id', sa.BigInteger(), primary_key=True),
        sa.Column('keep_alive', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('logout_on_disconnect', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('like_mode', sa.String(length=20), server_default='owner_only', nullable=False),
        sa.Column('control_mode', sa.String(length=20), server_default='everyone', nullable=False),
        sa.Column('dj_role_ids', sa.Text(), nullable=True),
        sa.Column('last_channel_id', sa.BigInteger(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    )
    
    op.create_table(
        'lofi_config',
        sa.Column('guild_id', sa.BigInteger(), primary_key=True),
        sa.Column('keep_alive', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('control_mode', sa.String(length=20), server_default='everyone', nullable=False),
        sa.Column('dj_role_ids', sa.Text(), nullable=True),
        sa.Column('last_channel_id', sa.BigInteger(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('lofi_config')
    op.drop_table('ym_settings')
