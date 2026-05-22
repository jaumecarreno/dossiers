"""Add transcription model to projects

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-23 00:00:00.000000

"""
from typing import Sequence, Union
import os

from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    default_model = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-transcribe")
    op.add_column(
        "projects",
        sa.Column(
            "transcription_model",
            sa.String(length=64),
            nullable=False,
            server_default=default_model,
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "transcription_model")
