"""create_yandex_music_config_table

Revision ID: f13e7bd1ea69
Revises: 3baa9074507e
Create Date: 2026-07-02 11:33:11.964938

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f13e7bd1ea69'
down_revision: Union[str, Sequence[str], None] = '3baa9074507e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'yandex_music_config',
        sa.Column('guild_id', sa.BigInteger(), primary_key=True),
        sa.Column('token', sa.Text(), nullable=False),
        sa.Column('session_id', sa.Text(), nullable=True),
        sa.Column('session_id2', sa.Text(), nullable=True),
        sa.Column('username', sa.String(length=100), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('yandex_music_config')
