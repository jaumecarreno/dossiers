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


def transcribe_audio(file_path: str, language: str | None = None) -> str:
    with Path(file_path).open("rb") as audio_file:
        kwargs = {
            "model": current_app.config["OPENAI_TRANSCRIPTION_MODEL"],
            "file": audio_file,
        }
        if language:
            kwargs["language"] = language
        response = _client().audio.transcriptions.create(**kwargs)
    text = getattr(response, "text", None)
    if text:
        return text.strip()
    if isinstance(response, dict) and response.get("text"):
        return response["text"].strip()
    return str(response).strip()


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
