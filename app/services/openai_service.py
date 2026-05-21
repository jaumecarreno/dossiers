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


def transcribe_audio(file_path: str, language: str | None = None) -> tuple[str, list[dict]]:
    import openai
    with Path(file_path).open("rb") as audio_file:
        kwargs = {
            "model": current_app.config["OPENAI_TRANSCRIPTION_MODEL"],
            "file": audio_file,
            "response_format": "verbose_json",
        }
        if language:
            kwargs["language"] = language
            
        try:
            response = _client().audio.transcriptions.create(**kwargs)
        except openai.BadRequestError as e:
            if "unsupported_value" in str(e) or "verbose_json" in str(e):
                # Fallback for models that don't support verbose_json (like gpt-4o-mini-transcribe)
                kwargs.pop("response_format", None)
                audio_file.seek(0)
                response = _client().audio.transcriptions.create(**kwargs)
            else:
                raise
        
    text = getattr(response, "text", "")
    segments = getattr(response, "segments", [])
    
    if not text and isinstance(response, dict):
        text = response.get("text", "")
        segments = response.get("segments", [])
        
    return text.strip(), segments


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
