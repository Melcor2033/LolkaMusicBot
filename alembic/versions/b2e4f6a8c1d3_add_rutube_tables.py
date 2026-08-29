"""add_rutube_tables

Revision ID: b2e4f6a8c1d3
Revises: a7b3c1d2e4f5
Create Date: 2026-07-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2e4f6a8c1d3'
down_revision: Union[str, Sequence[str], None] = 'a7b3c1d2e4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'rutube_settings',
        sa.Column('guild_id', sa.BigInteger(), primary_key=True),
        sa.Column('keep_alive', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('control_mode', sa.String(length=20), server_default='everyone', nullable=False),
        sa.Column('dj_role_ids', sa.Text(), server_default='', nullable=False),
        sa.Column('last_channel_id', sa.BigInteger(), nullable=True),
        sa.Column('default_playlist_id', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table(
        'rutube_playlists',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('video_ids', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['guild_id'], ['rutube_settings.guild_id'], ondelete='CASCADE'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('rutube_playlists')
    op.drop_table('rutube_settings')
