from __future__ import annotations

from pathlib import Path
import time

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


def _is_retryable_openai_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {408, 409, 429}:
        return True
    if isinstance(status_code, int) and status_code >= 500:
        return True
    return exc.__class__.__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
    }


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

        max_attempts = 4
        for attempt in range(max_attempts):
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
            except Exception as exc:
                if attempt >= max_attempts - 1 or not _is_retryable_openai_error(exc):
                    raise
                audio_file.seek(0)
                time.sleep(min(20, 2 ** attempt))
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
