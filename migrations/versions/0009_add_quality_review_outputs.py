"""Add quality, review and reusable outputs

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-23 21:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("glossary_json", sa.Text(), nullable=True))
    op.add_column("project_outputs", sa.Column("reviewed_transcript", sa.Text(), nullable=True))
    op.add_column(
        "project_outputs",
        sa.Column("final_dossier_pdf_path", sa.String(length=1024), nullable=True),
    )
    op.add_column("project_outputs", sa.Column("output_variants_json", sa.Text(), nullable=True))
    op.add_column("project_outputs", sa.Column("quality_report_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("project_outputs", "quality_report_json")
    op.drop_column("project_outputs", "output_variants_json")
    op.drop_column("project_outputs", "final_dossier_pdf_path")
    op.drop_column("project_outputs", "reviewed_transcript")
    op.drop_column("projects", "glossary_json")
