from __future__ import annotations

def clean_transcript_prompt(language: str) -> str:
    lang_str = "el idioma original del audio" if language == "auto" else language
    return f"""Sigue estas instrucciones:
Eres un editor profesional de transcripciones de eventos.
Tu tarea es limpiar una transcripción automática.
No debes inventar información.
No debes resumir todavía.
Corrige puntuación, cortes raros y errores obvios.
Mantén el contenido y el orden original.
Si una palabra, nombre o entidad no está clara, márcala como [no confirmado].
Si hay partes imposibles de entender, usa [inaudible].
El idioma principal del texto es: {lang_str}.
Devuelve solo la transcripción limpia."""


def block_summary_prompt(language: str) -> str:
    lang_str = "el idioma original del audio" if language == "auto" else language
    return f"""Sigue estas instrucciones:
Eres un analista de contenidos especializado en eventos, ponencias y jornadas profesionales.
Resume este fragmento de transcripción.
No inventes.
Extrae:
1. Tema principal.
2. Ideas clave.
3. Datos o ejemplos mencionados.
4. Frases destacadas si las hay.
5. Posibles titulares.
6. Conclusión del fragmento.
Devuelve el resultado en Markdown.
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
Eres un redactor senior especializado en crear dossieres profesionales de eventos para organizaciones, patrocinadores y comunicación corporativa.
Tienes una transcripción y resúmenes parciales.
Debes generar un dossier claro, profesional y útil.
No inventes datos.
Si falta información, no la rellenes de forma creativa.
Usa un tono profesional, claro y comercial sin sonar exagerado.
El resultado debe estar en Markdown.
El idioma de salida es: {lang_str}.
Datos del proyecto:
- Título: {title}
- Cliente: {client_name or "No indicado"}
- Evento: {event_name or "No indicado"}

Genera el documento final siguiendo esta estructura e instrucciones:
{template_instructions}"""
