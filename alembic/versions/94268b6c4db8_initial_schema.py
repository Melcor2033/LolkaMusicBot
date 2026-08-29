"""initial_schema

Revision ID: 94268b6c4db8
Revises: 
Create Date: 2026-06-26 15:47:04.718233

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '94268b6c4db8'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'voice_config',
        sa.Column('master_channel_id', sa.BigInteger(), primary_key=True),
        sa.Column('guild_id', sa.BigInteger(), nullable=False, index=True),
        sa.Column('category_id', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False)
    )

    op.create_table(
        'dynamic_channels',
        sa.Column('channel_id', sa.BigInteger(), primary_key=True),
        sa.Column('guild_id', sa.BigInteger(), nullable=False, index=True),
        sa.Column('owner_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('dynamic_channels')
    op.drop_table('voice_config')
