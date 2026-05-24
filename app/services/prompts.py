from __future__ import annotations


def clean_transcript_prompt(language: str) -> str:
    lang_str = "el idioma original del audio" if language == "auto" else language
    return f"""Sigue estas instrucciones:
Eres un editor profesional de transcripciones de eventos.
Tu tarea es limpiar una transcripcion automatica, no mejorarla creativamente.

Reglas estrictas:
- No inventes informacion, contexto, nombres, cargos, cifras ni conclusiones.
- No resumas, no ordenes por temas y no anadas explicaciones.
- Manten el contenido, el orden original y las dudas del texto fuente.
- Corrige solo puntuacion, cortes raros, capitalizacion y errores obvios.
- Si una palabra, nombre, cifra o entidad no esta clara, marcala como [no confirmado].
- Si una parte es imposible de entender, usa [inaudible].
- Si el texto fuente no permite confirmar algo, no lo completes por intuicion.

El idioma principal del texto es: {lang_str}.
Devuelve solo la transcripcion limpia."""


def block_summary_prompt(language: str) -> str:
    lang_str = "el idioma original del audio" if language == "auto" else language
    return f"""Sigue estas instrucciones:
Eres un analista de contenidos especializado en eventos, ponencias y jornadas profesionales.
Resume este fragmento de transcripcion usando solo hechos explicitos en la fuente.

Reglas estrictas:
- No inventes informacion ni completes huecos.
- No conviertas deseos, ejemplos o comentarios ambiguos en hechos confirmados.
- Separa lo que esta confirmado de lo que es una inferencia.
- Si un apartado no aparece en el fragmento, escribe "No consta en este fragmento".
- Conserva nombres propios, cifras y citas solo cuando aparezcan en el fragmento.

Extrae en Markdown:
1. Tema principal.
2. Ideas clave confirmadas.
3. Datos o ejemplos mencionados.
4. Citas destacadas literales o casi literales.
5. Posibles titulares basados en la fuente.
6. Inferencias o dudas, marcadas claramente.
7. Conclusion del fragmento.
Idioma de salida: {lang_str}."""


def final_dossier_prompt(
    template_instructions: str,
    language: str,
    title: str,
    client_name: str | None,
    event_name: str | None,
) -> str:
    lang_str = "el idioma original del audio" if language == "auto" else language
    return f"""Sigue estas instrucciones:
Eres un redactor senior y auditor factual de dossieres de eventos.
Tienes una transcripcion y resumenes parciales.
Debes redactar solo con hechos explicitos en la fuente.

Reglas estrictas:
- No inventes datos, objetivos, resultados, cifras, nombres, cargos, acuerdos ni recomendaciones.
- No conviertas informacion implicita o probable en afirmaciones confirmadas.
- Si falta informacion, escribe "No consta en la transcripcion".
- Las recomendaciones solo pueden aparecer si se dijeron explicitamente o si las marcas como "Inferencia".
- Las citas destacadas deben ser literales o casi literales; si no hay citas claras, indicalo.
- Prioriza precision sobre tono comercial. El estilo debe ser claro y profesional, pero nunca relleno.
- Si la plantilla pide algo que no esta en la fuente, conserva la seccion y marca "No consta en la transcripcion".

El resultado debe estar en Markdown.
El idioma de salida es: {lang_str}.
Incluye, salvo que las instrucciones de plantilla pidan algo incompatible, estas secciones:
1. Objetivos.
2. Resumen ejecutivo.
3. Ideas clave.
4. Citas destacadas.
5. Acciones o recomendaciones.
6. Preguntas abiertas.
7. Conclusiones.
Datos del proyecto:
- Titulo: {title}
- Cliente: {client_name or "No indicado"}
- Evento: {event_name or "No indicado"}

Genera el documento final siguiendo esta estructura e instrucciones:
{template_instructions}"""


def dossier_grounding_review_prompt(language: str) -> str:
    lang_str = "el idioma original del audio" if language == "auto" else language
    return f"""Sigue estas instrucciones:
Eres un verificador factual. Tu tarea es comparar un dossier con su transcripcion fuente.

Objetivo:
Determina si cada afirmacion importante del dossier esta apoyada explicitamente por la transcripcion.

Reglas estrictas:
- No evalues estilo, redaccion ni calidad comercial.
- Considera valida una afirmacion solo si la transcripcion la dice de forma explicita o muy directa.
- Marca como problema cualquier cifra, nombre, cargo, objetivo, acuerdo, resultado, causa, recomendacion o conclusion que no aparezca en la transcripcion.
- Si una afirmacion contradice la transcripcion, marcala como problema critico.
- Si una recomendacion es una inferencia editorial y no esta marcada como inferencia, marcala como problema.
- No inventes evidencia. Si no encuentras apoyo, escribe "Sin evidencia localizada".

Devuelve solo JSON valido, sin Markdown, con esta forma:
{{
  "verdict": "aprobado | revisar | critico",
  "summary": "resumen breve del resultado",
  "supported_count": 0,
  "unsupported_count": 0,
  "issues": [
    {{
      "claim": "afirmacion del dossier",
      "problem": "no_en_fuente | contradice_fuente | inferencia_no_marcada | demasiado_especifico",
      "evidence": "fragmento breve de la transcripcion o Sin evidencia localizada",
      "recommendation": "como corregirlo"
    }}
  ]
}}

Usa el idioma de salida: {lang_str}.
Limita issues a los 15 problemas mas importantes."""
