"""Add paragraphs_metadata_json to project_outputs

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-23 01:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "project_outputs",
        sa.Column(
            "paragraphs_metadata_json",
            sa.Text(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("project_outputs", "paragraphs_metadata_json")
