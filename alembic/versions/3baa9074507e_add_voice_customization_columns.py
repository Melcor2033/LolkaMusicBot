"""add_voice_customization_columns

Revision ID: 3baa9074507e
Revises: 94268b6c4db8
Create Date: 2026-06-26 22:24:18.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3baa9074507e'
down_revision: Union[str, Sequence[str], None] = '94268b6c4db8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add customization columns to voice_config."""
    op.add_column('voice_config', sa.Column('channel_name_template', sa.String(100), nullable=True))
    op.add_column('voice_config', sa.Column('embed_title', sa.String(256), nullable=True))
    op.add_column('voice_config', sa.Column('embed_description', sa.Text(), nullable=True))
    op.add_column('voice_config', sa.Column('embed_color', sa.Integer(), nullable=True))
    op.add_column('voice_config', sa.Column('mention_user', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Remove customization columns from voice_config."""
    op.drop_column('voice_config', 'mention_user')
    op.drop_column('voice_config', 'embed_color')
    op.drop_column('voice_config', 'embed_description')
    op.drop_column('voice_config', 'embed_title')
    op.drop_column('voice_config', 'channel_name_template')
