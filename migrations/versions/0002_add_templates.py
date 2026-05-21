"""Add Template model

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-21 22:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def utcnow():
    return datetime.now(timezone.utc)


def upgrade() -> None:
    # 1. Create templates table
    templates_table = op.create_table(
        'templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('prompt_instructions', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # 2. Insert default templates
    op.bulk_insert(
        templates_table,
        [
            {
                "id": 1,
                "name": "Resumen ejecutivo",
                "prompt_instructions": "- Título\n- Contexto de la ponencia\n- Ideas principales\n- Conclusiones\n- Frases destacadas\n- Posibles acciones posteriores",
                "created_at": utcnow(),
                "updated_at": utcnow(),
            },
            {
                "id": 2,
                "name": "Dossier para patrocinadores",
                "prompt_instructions": "- Título del evento\n- Descripción breve de la sesión\n- Valor aportado por la ponencia\n- Temas tratados\n- Mensajes clave\n- Impacto para asistentes/público\n- Frases aprovechables en comunicación\n- Conclusiones para patrocinadores\n- Recomendaciones de uso comunicativo",
                "created_at": utcnow(),
                "updated_at": utcnow(),
            },
            {
                "id": 3,
                "name": "Contenido para comunicación",
                "prompt_instructions": "- Resumen web\n- Nota breve para newsletter\n- 5 publicaciones para LinkedIn\n- 5 titulares posibles\n- 10 frases destacadas\n- Ideas para carrusel de redes",
                "created_at": utcnow(),
                "updated_at": utcnow(),
            }
        ]
    )

    # 3. Add template_id to projects
    op.add_column('projects', sa.Column('template_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_projects_template_id', 'projects', 'templates', ['template_id'], ['id'])

    # 4. Migrate existing data mapping old template_type string to ID
    # This works for postgresql
    op.execute("""
        UPDATE projects SET template_id = 1 WHERE template_type = 'resumen_ejecutivo';
        UPDATE projects SET template_id = 2 WHERE template_type = 'dossier_patrocinadores';
        UPDATE projects SET template_id = 3 WHERE template_type = 'contenido_comunicacion';
    """)

    # 5. Drop old template_type column
    op.drop_column('projects', 'template_type')


def downgrade() -> None:
    # 1. Add back template_type
    op.add_column('projects', sa.Column('template_type', sa.String(length=64), nullable=True))

    # 2. Map back to strings
    op.execute("""
        UPDATE projects SET template_type = 'resumen_ejecutivo' WHERE template_id = 1;
        UPDATE projects SET template_type = 'dossier_patrocinadores' WHERE template_id = 2;
        UPDATE projects SET template_type = 'contenido_comunicacion' WHERE template_id = 3;
    """)

    # 3. Drop template_id and foreign key
    op.drop_constraint('fk_projects_template_id', 'projects', type_='foreignkey')
    op.drop_column('projects', 'template_id')

    # 4. Drop templates table
    op.drop_table('templates')
