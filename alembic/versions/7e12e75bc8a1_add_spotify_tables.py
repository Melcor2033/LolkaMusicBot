"""add_spotify_tables

Revision ID: 7e12e75bc8a1
Revises: 1fb62708d9f6
Create Date: 2026-07-09 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7e12e75bc8a1'
down_revision: Union[str, Sequence[str], None] = '1fb62708d9f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'spotify_settings',
        sa.Column('guild_id', sa.BigInteger(), primary_key=True),
        sa.Column('keep_alive', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('control_mode', sa.String(length=20), server_default='everyone', nullable=False),
        sa.Column('dj_role_ids', sa.Text(), server_default='', nullable=False),
        sa.Column('last_channel_id', sa.BigInteger(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table(
        'spotify_sessions',
        sa.Column('guild_id', sa.BigInteger(), primary_key=True),
        sa.Column('queue_track_ids', sa.Text(), nullable=False),
        sa.Column('current_index', sa.Integer(), server_default='0', nullable=False),
        sa.Column('playback_position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('is_temporary', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('single_track_mode', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['guild_id'], ['spotify_settings.guild_id'], ondelete='CASCADE'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('spotify_sessions')
    op.drop_table('spotify_settings')
