"""Create blend_user_tokens table

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-07-23 20:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create blend_user_tokens table for encrypted user tokens."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if 'blend_user_tokens' not in tables:
        op.create_table(
            'blend_user_tokens',
            sa.Column('user_id', sa.BigInteger(), nullable=False),
            sa.Column('guild_id', sa.BigInteger(), nullable=False),
            sa.Column('oauth_token', sa.Text(), nullable=False),
            sa.Column('username', sa.String(255), nullable=True),
            sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
            sa.Column('forget_on_disconnect', sa.Boolean(), nullable=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
            sa.PrimaryKeyConstraint('user_id', 'guild_id')
        )
        op.create_index('idx_blend_tokens_guild', 'blend_user_tokens', ['guild_id'])


def downgrade() -> None:
    """Drop blend_user_tokens table."""
    op.drop_index('idx_blend_tokens_guild', table_name='blend_user_tokens')
    op.drop_table('blend_user_tokens')
