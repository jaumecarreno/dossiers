"""Add share_token to projects

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-22 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('share_token', sa.String(length=64), nullable=True))
    op.create_index(op.f('ix_projects_share_token'), 'projects', ['share_token'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_projects_share_token'), table_name='projects')
    op.drop_column('projects', 'share_token')
