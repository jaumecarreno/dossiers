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
down_revision: Union[str, None] = '0001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def utcnow():
    return datetime.now(timezone.utc)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    # 1. Create templates table
    if 'templates' not in tables:
        templates_table = op.create_table(
            'templates',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('prompt_instructions', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )
    else:
        templates_table = sa.table(
            'templates',
            sa.column('id', sa.Integer()),
            sa.column('name', sa.String(length=255)),
            sa.column('prompt_instructions', sa.Text()),
            sa.column('created_at', sa.DateTime(timezone=True)),
            sa.column('updated_at', sa.DateTime(timezone=True)),
        )

    # 2. Insert default templates
    template_count = bind.execute(sa.text("SELECT COUNT(*) FROM templates")).scalar()
    if template_count == 0:
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
    project_columns = {column['name'] for column in inspector.get_columns('projects')}
    if 'template_id' not in project_columns:
        op.add_column('projects', sa.Column('template_id', sa.Integer(), nullable=True))

    if bind.dialect.name != 'sqlite':
        foreign_keys = {
            fk['name'] for fk in inspector.get_foreign_keys('projects') if fk.get('name')
        }
        if 'fk_projects_template_id' not in foreign_keys:
            op.create_foreign_key('fk_projects_template_id', 'projects', 'templates', ['template_id'], ['id'])

    # 4. Migrate existing data mapping old template_type string to ID
    if 'template_type' in project_columns:
        op.execute("UPDATE projects SET template_id = 1 WHERE template_type = 'resumen_ejecutivo'")
        op.execute("UPDATE projects SET template_id = 2 WHERE template_type = 'dossier_patrocinadores'")
        op.execute("UPDATE projects SET template_id = 3 WHERE template_type = 'contenido_comunicacion'")

    # 5. Drop old template_type column
    if 'template_type' in project_columns:
        if bind.dialect.name == 'sqlite':
            with op.batch_alter_table('projects') as batch_op:
                batch_op.drop_column('template_type')
        else:
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
