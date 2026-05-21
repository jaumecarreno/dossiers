"""Add segments_json to transcript chunks

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-22 01:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('transcript_chunks', sa.Column('segments_json', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('transcript_chunks', 'segments_json')
