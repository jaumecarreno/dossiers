from __future__ import annotations

from pathlib import Path

from flask import current_app
from openai import OpenAI

from app.services.prompts import (
    block_summary_prompt,
    clean_transcript_prompt,
    final_dossier_prompt,
)


def _client() -> OpenAI:
    return OpenAI(api_key=current_app.config.get("OPENAI_API_KEY"))


def _text_response(system_prompt: str, user_prompt: str) -> str:
    response = _client().responses.create(
        model=current_app.config["OPENAI_SUMMARY_MODEL"],
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    text = getattr(response, "output_text", None)
    if text:
        return text.strip()
    return str(response).strip()


def transcribe_audio(
    file_path: str,
    language: str | None = None,
    model: str | None = None,
    prompt: str | None = None,
) -> tuple[str, list[dict]]:
    import openai
    with Path(file_path).open("rb") as audio_file:
        selected_model = model or current_app.config["OPENAI_TRANSCRIPTION_MODEL"]
        kwargs = {
            "model": selected_model,
            "file": audio_file,
        }
        if selected_model == "whisper-1":
            kwargs["response_format"] = "verbose_json"
        if language and language != "auto":
            kwargs["language"] = language
        if prompt:
            kwargs["prompt"] = prompt

        for _ in range(3):
            try:
                response = _client().audio.transcriptions.create(**kwargs)
                break
            except openai.BadRequestError as e:
                message = str(e)
                should_retry = False
                if "response_format" in kwargs and (
                    "unsupported_value" in message
                    or "verbose_json" in message
                    or "response_format" in message
                ):
                    # Fallback for models that don't support verbose_json.
                    kwargs.pop("response_format", None)
                    should_retry = True
                if "prompt" in kwargs and "prompt" in message and (
                    "unsupported" in message or "Unknown parameter" in message
                ):
                    kwargs.pop("prompt", None)
                    should_retry = True
                if "language" in kwargs and "language" in message and (
                    "unsupported" in message or "Unknown parameter" in message
                ):
                    kwargs.pop("language", None)
                    should_retry = True
                if not should_retry:
                    raise
                audio_file.seek(0)
        else:  # pragma: no cover - defensive loop guard
            raise RuntimeError("No se pudo transcribir el audio con los parámetros disponibles.")
        
    text = getattr(response, "text", "")
    segments = getattr(response, "segments", [])
    
    if not text and isinstance(response, dict):
        text = response.get("text", "")
        segments = response.get("segments", [])
        
    # Ensure segments are JSON serializable dictionaries
    clean_segments = []
    for s in segments:
        if isinstance(s, dict):
            clean_segments.append(s)
        else:
            # For OpenAI TranscriptionSegment objects
            clean_segments.append({
                "start": getattr(s, "start", 0),
                "end": getattr(s, "end", 0),
                "text": getattr(s, "text", "")
            })
            
    return text.strip(), clean_segments


def clean_transcript(text: str, language: str) -> str:
    return _text_response(clean_transcript_prompt(language), text)


def summarize_chunk(text: str, language: str) -> str:
    return _text_response(block_summary_prompt(language), text)


def generate_final_dossier(project, cleaned_transcript: str, block_summary: str) -> str:
    prompt = final_dossier_prompt(
        project.template.prompt_instructions,
        project.language,
        project.title,
        project.client_name,
        project.event_name,
    )
    user_prompt = f"""Transcripción limpia:

{cleaned_transcript}

Resúmenes parciales:

{block_summary}"""
    return _text_response(prompt, user_prompt)


def clean_json_response(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def align_dossier_blocks_service(blocks: list[str], timestamped_transcript: str, language: str) -> str:
    system_prompt = """Eres un asistente de edición profesional. Tu tarea es alinear los bloques del dossier final con la transcripción original (que tiene marcas de tiempo).
Asocia cada bloque del dossier con el fragmento del audio donde se habla de esa idea.

Reglas importantes:
1. Para cada bloque, identifica el inicio y fin exactos en segundos en el audio original.
2. El fragmento del audio seleccionado para cada bloque debe durar preferiblemente entre 15 y 45 segundos. Busca el intervalo más representativo que explique ese bloque.
3. Extrae la transcripción original limpia (sin marcas de tiempo) correspondiente a esa sección de la transcripción.
4. Devuelve un array JSON en el mismo orden que los bloques recibidos. Cada elemento debe ser un objeto con:
   - "block_text": El texto exacto del bloque del dossier.
   - "start_seconds": El segundo de inicio en el audio (ej. 125) o null si es un título/sección/tabla que no corresponde a una idea concreta del audio.
   - "end_seconds": El segundo de fin en el audio (ej. 165) o null.
   - "original_transcript": La transcripción exacta del audio original correspondiente a esta sección o null.

Devuelve únicamente el array JSON, sin bloques de código ni explicaciones."""

    blocks_formatted = "\n\n".join([f"--- BLOQUE {i+1} ---\n{block}" for i, block in enumerate(blocks)])
    user_prompt = f"""Transcripción original con tiempos:
{timestamped_transcript}

Bloques del dossier a alinear:
{blocks_formatted}"""

    return _text_response(system_prompt, user_prompt)
