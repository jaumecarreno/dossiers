"""Add source kind to projects

Revision ID: 0008
Revises: 5155baa3c4d9
Create Date: 2026-05-23 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008"
down_revision: Union[str, None] = "5155baa3c4d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "source_kind",
            sa.String(length=32),
            nullable=False,
            server_default="media",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "source_kind")
