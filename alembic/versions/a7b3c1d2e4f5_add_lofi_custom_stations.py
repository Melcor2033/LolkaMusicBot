"""add_lofi_custom_stations

Revision ID: a7b3c1d2e4f5
Revises: 398ce4644bc8
Create Date: 2026-07-04 14:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b3c1d2e4f5'
down_revision: Union[str, Sequence[str], None] = '398ce4644bc8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Добавляем last_station_name в lofi_config
    op.add_column(
        'lofi_config',
        sa.Column('last_station_name', sa.String(length=100), nullable=True),
    )

    # 2. Таблица кастомных станций
    op.create_table(
        'lofi_custom_stations',
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('emoji', sa.String(length=10), server_default='🎵', nullable=False),
        sa.Column('genre', sa.String(length=100), server_default='Custom', nullable=False),
        sa.Column('added_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('guild_id', 'name'),
    )

    # 3. Таблица скрытых предустановленных станций
    op.create_table(
        'lofi_hidden_stations',
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('station_name', sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint('guild_id', 'station_name'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('lofi_hidden_stations')
    op.drop_table('lofi_custom_stations')
    op.drop_column('lofi_config', 'last_station_name')
