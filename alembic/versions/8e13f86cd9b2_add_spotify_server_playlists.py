"""add_spotify_server_playlists

Revision ID: 8e13f86cd9b2
Revises: 7e12e75bc8a1
Create Date: 2026-07-09 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8e13f86cd9b2'
down_revision: Union[str, Sequence[str], None] = '7e12e75bc8a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'spotify_playlists',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('track_ids', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['guild_id'], ['spotify_settings.guild_id'], ondelete='CASCADE'),
    )

    op.add_column('spotify_settings', sa.Column('default_playlist_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_spotify_settings_default_playlist',
        'spotify_settings', 'spotify_playlists',
        ['default_playlist_id'], ['id'],
        ondelete='SET NULL'
    )

    op.add_column('spotify_sessions', sa.Column('source_playlist_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('spotify_sessions', 'source_playlist_id')
    op.drop_constraint('fk_spotify_settings_default_playlist', 'spotify_settings', type_='foreignkey')
    op.drop_column('spotify_settings', 'default_playlist_id')
    op.drop_table('spotify_playlists')
