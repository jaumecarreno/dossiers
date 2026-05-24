"""Add chronicle-style talk template

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-24 19:10:00.000000

"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TEMPLATE_NAME = "Crónica temática de ponencia"

TEMPLATE_PROMPT = """Genera una crónica-resumen de la ponencia siguiendo este estilo editorial:

Formato general:
- No hagas un dossier corporativo.
- No incluyas secciones genéricas como Objetivos, Resumen ejecutivo, Acciones, Recomendaciones o Preguntas abiertas.
- No uses tablas.
- No uses listas salvo que la transcripción las contenga de forma muy explícita y sean necesarias.
- El resultado debe parecer una pieza final de lectura, no un esquema de trabajo.
- Escribe en español, con tono claro, culto, periodístico y sintético.
- Usa solo hechos, ideas, ejemplos, citas y datos explícitos en la transcripción o en los metadatos del proyecto.
- Si un dato no consta, no lo inventes.

Estructura:
1. Primera línea: lugar y fecha si constan.
   Ejemplo: CaixaForum Girona, 18 de mayo de 2026.
   Si no constan, omite esta línea.

2. Segunda línea: título de la ponencia o título editorial breve.
   Debe ser sobrio y fiel al contenido, no publicitario.

3. Tercer bloque: contexto de la sesión.
   Explica en 1 párrafo quién interviene, su cargo o perfil si consta, el tema de la ponencia, ciclo, entidad organizadora o marco del evento si aparecen en la fuente.

4. Después, desarrolla entre 8 y 14 bloques temáticos.
   Cada bloque debe ser un único párrafo.
   Cada bloque empieza con un título breve en negrita, seguido de punto, y luego el desarrollo.
   Ejemplo:
   **La ciudad como entidad imaginaria.** Nadie puede conocer una ciudad en su totalidad...

Contenido de cada bloque:
- Cada bloque debe recoger una idea central de la ponencia.
- El título debe ser concreto, expresivo y fiel al contenido.
- El desarrollo debe tener entre 3 y 6 frases.
- Combina explicación, ejemplos y citas solo cuando estén en la transcripción.
- Mantén el orden lógico de la intervención, salvo que reorganizar por temas mejore claramente la lectura sin alterar el sentido.

Diálogos o mesas:
- Si la sesión tiene partes claras, puedes introducir separadores breves como:
  **Monólogo inicial de [ponente].**
  **Diálogo entre [ponente 1] y [ponente 2].**
- Hazlo solo si esa división aparece claramente en la transcripción.

Estilo:
- Frases limpias y precisas.
- Evita grandilocuencia, marketing y conclusiones inventadas.
- Evita repetir "la ponencia trata sobre".
- No cierres con una conclusión artificial si la intervención no la tiene.
- Prioriza una crónica fiel y legible antes que un resumen exhaustivo.

Control factual:
- No añadas nombres, cargos, obras, instituciones, fechas, cifras ni citas que no aparezcan en la fuente.
- Si una afirmación es dudosa, rebájala o elimínala.
- Las citas entre comillas deben ser literales o casi literales."""


def upgrade() -> None:
    bind = op.get_bind()
    exists = bind.execute(
        sa.text("SELECT COUNT(*) FROM templates WHERE name = :name"),
        {"name": TEMPLATE_NAME},
    ).scalar()
    if exists:
        return

    templates = sa.table(
        "templates",
        sa.column("name", sa.String(length=255)),
        sa.column("prompt_instructions", sa.Text()),
        sa.column("is_default", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(timezone.utc)
    op.bulk_insert(
        templates,
        [
            {
                "name": TEMPLATE_NAME,
                "prompt_instructions": TEMPLATE_PROMPT,
                "is_default": False,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM templates WHERE name = :name").bindparams(name=TEMPLATE_NAME)
    )
