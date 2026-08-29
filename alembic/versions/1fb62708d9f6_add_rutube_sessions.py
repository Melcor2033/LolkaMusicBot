"""add_rutube_sessions

Revision ID: 1fb62708d9f6
Revises: b2e4f6a8c1d3
Create Date: 2026-07-07 10:24:28.878197

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1fb62708d9f6'
down_revision: Union[str, Sequence[str], None] = 'b2e4f6a8c1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'rutube_sessions',
        sa.Column('guild_id', sa.BigInteger(), primary_key=True),
        sa.Column('queue_video_ids', sa.Text(), nullable=False),
        sa.Column('current_index', sa.Integer(), server_default='0', nullable=False),
        sa.Column('playback_position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('source_playlist_id', sa.Integer(), nullable=True),
        sa.Column('is_temporary', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('single_track_mode', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['guild_id'], ['rutube_settings.guild_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_playlist_id'], ['rutube_playlists.id'], ondelete='SET NULL'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('rutube_sessions')

