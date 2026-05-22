"""Remove paragraphs_metadata_json from project_outputs

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-23 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use batch_alter_table for SQLite compatibility just in case, though this is likely Postgres
    with op.batch_alter_table("project_outputs") as batch_op:
        batch_op.drop_column("paragraphs_metadata_json")


def downgrade() -> None:
    with op.batch_alter_table("project_outputs") as batch_op:
        batch_op.add_column(sa.Column("paragraphs_metadata_json", sa.Text(), nullable=True))
