from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import string

from flask import current_app, has_app_context

from app.constants import (
    DEFAULT_TRANSCRIPTION_MODEL,
    LANGUAGE_CHOICES,
    SOURCE_KIND_TRANSCRIPT_FILES,
)
from app.extensions import db
from app.models import Project, ProjectLog, ProjectOutput, TranscriptChunk
from app.services.export_service import markdown_to_docx
from app.services.media_service import (
    ensure_media_tools_available,
    get_media_duration,
    prepare_audio,
    split_audio,
)
from app.services.openai_service import (
    clean_transcript,
    generate_final_dossier,
    summarize_chunk,
    transcribe_audio,
)
from app.services.transcript_import_service import load_transcript_texts_from_manifest
from app.storage import project_audio_dir, project_chunks_dir, project_outputs_dir


def process_project(project_id: int) -> None:
    if has_app_context():
        _process_project(project_id)
        return

    from app import create_app

    app = create_app()
    with app.app_context():
        _process_project(project_id)


def _set_status(project: Project, status: str, message: str | None = None) -> None:
    project.status = status
    db.session.add(project)
    if message:
        db.session.add(ProjectLog(project_id=project.id, message=message, level="info"))
    db.session.commit()


def _add_project_log(project: Project, message: str, level: str = "info") -> None:
    db.session.add(ProjectLog(project_id=project.id, message=message, level=level))


def _format_seconds(seconds: int | float | None) -> str:
    if seconds is None:
        return "--:--"
    total = max(0, int(round(seconds)))
    hours = total // 3600
    minutes = (total % 3600) // 60
    remaining = total % 60
    if hours:
        return f"{hours}:{minutes:02}:{remaining:02}"
    return f"{minutes}:{remaining:02}"


def _chunk_range(chunk: TranscriptChunk) -> str:
    return f"{_format_seconds(chunk.start_seconds)}-{_format_seconds(chunk.end_seconds)}"


def _file_size_mb(path: str | Path) -> float:
    try:
        return Path(path).stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


def _friendly_error(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    first_line = text.splitlines()[0].strip()
    return first_line[:1200]


def _split_text(text: str, max_chars: int = 12000) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    paragraphs = text.split("\n\n")
    current: list[str] = []
    current_size = 0
    for paragraph in paragraphs:
        paragraph_size = len(paragraph) + 2
        if current and current_size + paragraph_size > max_chars:
            parts.append("\n\n".join(current))
            current = []
            current_size = 0
        current.append(paragraph)
        current_size += paragraph_size
    if current:
        parts.append("\n\n".join(current))
    return parts


def _last_words(text: str, max_words: int = 300) -> str:
    words = text.split()
    return " ".join(words[-max_words:])


def _extract_key_terms(text: str, max_terms: int = 30) -> list[str]:
    """Extract frequently-occurring capitalised words (likely proper nouns / jargon)."""
    words = text.split()
    capitalized = [
        w.strip(string.punctuation + "¿¡\u201c\u201d\u2018\u2019\u00ab\u00bb")
        for w in words
        if w[0:1].isupper() and len(w) > 2
    ]
    counts = Counter(capitalized)
    common_starts = {
        "El", "La", "Los", "Las", "Un", "Una", "En", "De", "Del",
        "Por", "Para", "Con", "Sin", "Que", "No", "Es", "Se", "The",
        "And", "But", "This", "That", "Als", "Amb", "Per", "Com",
    }
    return [
        term
        for term, _ in counts.most_common(max_terms + len(common_starts))
        if term not in common_starts
    ][:max_terms]


def _build_transcription_prompt(project: Project, previous_text: str = "") -> str:
    language = LANGUAGE_CHOICES.get(project.language, project.language)
    context_parts = [
        "Transcribe el audio con precisión y conserva nombres propios, cifras y terminología.",
        f"Idioma esperado: {language}.",
        f"Título: {project.title}.",
    ]
    if project.client_name:
        context_parts.append(f"Cliente u organización: {project.client_name}.")
    if project.event_name:
        context_parts.append(f"Evento: {project.event_name}.")
    if previous_text:
        key_terms = _extract_key_terms(previous_text)
        if key_terms:
            context_parts.append(
                f"Vocabulario recurrente (conservar grafía): {', '.join(key_terms)}"
            )
        context_parts.append(
            "Contexto inmediatamente anterior para continuidad, no lo repitas si ya aparece: "
            f"{_last_words(previous_text)}"
        )
    return "\n".join(context_parts)


def _normalize_word(word: str) -> str:
    return word.strip(string.punctuation + "¿¡“”‘’«»").casefold()


def _dedupe_overlap(
    previous_text: str,
    current_text: str,
    max_words: int = 120,
    min_words: int = 3,
) -> str:
    previous_words = previous_text.split()
    current_words = current_text.split()
    if not previous_words or not current_words:
        return current_text

    max_overlap = min(max_words, len(previous_words), len(current_words))
    previous_norm = [_normalize_word(word) for word in previous_words]
    current_norm = [_normalize_word(word) for word in current_words]
    for overlap in range(max_overlap, min_words - 1, -1):
        if previous_norm[-overlap:] == current_norm[:overlap]:
            return " ".join(current_words[overlap:]).strip()
    return current_text


def _append_transcript(transcripts: list[str], text: str) -> None:
    cleaned = (text or "").strip()
    if not cleaned:
        return
    if transcripts:
        cleaned = _dedupe_overlap("\n\n".join(transcripts), cleaned)
    if cleaned:
        transcripts.append(cleaned)


def _build_full_transcript_from_transcript_files(project: Project) -> str:
    _set_status(project, "importing_transcript", "Importando transcripciones subidas.")
    transcripts: list[str] = []
    for filename, text in load_transcript_texts_from_manifest(project.source_file_path):
        _append_transcript(transcripts, text)
        db.session.add(
            ProjectLog(
                project_id=project.id,
                message=f"Transcripcion importada: {filename}",
                level="info",
            )
        )
    full_transcript = "\n\n".join(transcripts).strip()
    if not full_transcript:
        raise ValueError("Las transcripciones subidas no contienen texto util.")
    return full_transcript


def _complete_project_from_transcript(project: Project, full_transcript: str) -> None:
    output = ProjectOutput.query.filter_by(project_id=project.id).one_or_none()
    if not output:
        output = ProjectOutput(project_id=project.id)
        db.session.add(output)
    output.full_transcript = full_transcript
    db.session.commit()

    if not output.cleaned_transcript:
        _set_status(project, "cleaning_transcript", "Limpiando transcripcion.")
        output.cleaned_transcript = clean_transcript(full_transcript, project.language)
        db.session.commit()

    if not output.block_summary:
        _set_status(project, "summarizing", "Generando resumen por bloques.")
        text_blocks = _split_text(output.cleaned_transcript or "")
        block_summaries = []
        for index, block in enumerate(text_blocks, start=1):
            summary = summarize_chunk(block, project.language)
            block_summaries.append(f"## Bloque {index}\n\n{summary}")
        if len(block_summaries) > 1:
            global_summary = summarize_chunk("\n\n".join(block_summaries), project.language)
            block_summaries.append(f"## Resumen global\n\n{global_summary}")
        output.block_summary = "\n\n".join(block_summaries)
        db.session.commit()

    if not output.final_dossier_markdown:
        _set_status(project, "generating_dossier", "Generando dossier final.")
        output.final_dossier_markdown = generate_final_dossier(
            project,
            output.cleaned_transcript or "",
            output.block_summary or "",
        )
        db.session.commit()

    outputs_dir = project_outputs_dir(project.id)
    markdown_path = outputs_dir / "dossier.md"
    markdown_path.write_text(output.final_dossier_markdown or "", encoding="utf-8")
    docx_path = outputs_dir / "dossier.docx"
    markdown_to_docx(output.final_dossier_markdown or "", docx_path)
    output.final_dossier_docx_path = str(docx_path)
    project.status = "completed"
    db.session.add(ProjectLog(project_id=project.id, message="Dossier completado."))
    db.session.commit()


def _process_project(project_id: int) -> None:
    project = Project.query.get(project_id)
    if not project:
        return

    try:
        if project.source_kind == SOURCE_KIND_TRANSCRIPT_FILES:
            full_transcript = _build_full_transcript_from_transcript_files(project)
            _complete_project_from_transcript(project, full_transcript)
            return

        source_path = Path(project.source_file_path)
        audio_path = project_audio_dir(project.id) / "audio.mp3"
        chunk_minutes = current_app.config["TRANSCRIPT_CHUNK_MINUTES"]

        _set_status(project, "extracting_audio", "Preparando audio con ffmpeg.")
        ensure_media_tools_available()
        project.duration_seconds = get_media_duration(source_path)
        _add_project_log(
            project,
            f"Duracion detectada: {_format_seconds(project.duration_seconds)}.",
        )
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            prepare_audio(source_path, audio_path)
            _add_project_log(
                project,
                f"Audio preparado para transcripcion ({_file_size_mb(audio_path):.1f} MB).",
            )
        db.session.commit()

        _set_status(project, "splitting_audio", "Dividiendo audio en fragmentos.")
        if not project.chunks:
            chunk_specs = split_audio(audio_path, project_chunks_dir(project.id), chunk_minutes)
            for index, chunk in enumerate(chunk_specs, start=1):
                db.session.add(
                    TranscriptChunk(
                        project_id=project.id,
                        chunk_index=index,
                        audio_path=str(chunk.path),
                        start_seconds=int(round(chunk.start_seconds)),
                        end_seconds=int(round(chunk.end_seconds)),
                        status="pending",
                    )
                )
            db.session.commit()
            _add_project_log(project, f"Audio dividido en {len(chunk_specs)} fragmentos.")
            db.session.commit()

        _set_status(project, "transcribing", "Transcribiendo fragmentos con OpenAI.")
        transcripts: list[str] = []
        transcription_model = (
            project.transcription_model
            or current_app.config.get("OPENAI_TRANSCRIPTION_MODEL")
            or DEFAULT_TRANSCRIPTION_MODEL
        )
        total_chunks = len(project.chunks)
        for chunk in project.chunks:
            if chunk.status == "completed" and chunk.transcript_text:
                _append_transcript(transcripts, chunk.transcript_text)
                continue
                
            chunk.status = "transcribing"
            chunk_size_mb = _file_size_mb(chunk.audio_path)
            _add_project_log(
                project,
                (
                    f"Transcribiendo fragmento {chunk.chunk_index}/{total_chunks} "
                    f"({_chunk_range(chunk)}, {chunk_size_mb:.1f} MB) con {transcription_model}."
                ),
            )
            db.session.commit()
            try:
                prompt = _build_transcription_prompt(project, "\n\n".join(transcripts))
                text, segments = transcribe_audio(
                    chunk.audio_path,
                    project.language,
                    model=transcription_model,
                    prompt=prompt,
                )
                chunk.transcript_text = text
                chunk.segments_json = json.dumps(segments) if segments else None
                chunk.status = "completed"
                _append_transcript(transcripts, chunk.transcript_text or "")
                _add_project_log(
                    project,
                    f"Fragmento {chunk.chunk_index}/{total_chunks} transcrito correctamente.",
                )
                db.session.commit()
            except Exception as exc:
                message = (
                    f"Fallo al transcribir el fragmento {chunk.chunk_index}/{total_chunks} "
                    f"({_chunk_range(chunk)}, {chunk_size_mb:.1f} MB): {_friendly_error(exc)}"
                )
                chunk.status = "failed"
                chunk.error_message = message
                _add_project_log(project, message, level="error")
                db.session.commit()
                raise RuntimeError(message) from exc

        full_transcript = "\n\n".join(transcripts).strip()
        output = ProjectOutput.query.filter_by(project_id=project.id).one_or_none()
        if not output:
            output = ProjectOutput(project_id=project.id)
            db.session.add(output)
        output.full_transcript = full_transcript
        db.session.commit()

        if not output.cleaned_transcript:
            _set_status(project, "cleaning_transcript", "Limpiando transcripcion.")
            output.cleaned_transcript = clean_transcript(full_transcript, project.language)
            db.session.commit()

        if not output.block_summary:
            _set_status(project, "summarizing", "Generando resumen por bloques.")
            text_blocks = _split_text(output.cleaned_transcript or "")
            block_summaries = []
            for index, block in enumerate(text_blocks, start=1):
                summary = summarize_chunk(block, project.language)
                block_summaries.append(f"## Bloque {index}\n\n{summary}")
            if len(block_summaries) > 1:
                global_summary = summarize_chunk("\n\n".join(block_summaries), project.language)
                block_summaries.append(f"## Resumen global\n\n{global_summary}")
            output.block_summary = "\n\n".join(block_summaries)
            db.session.commit()

        if not output.final_dossier_markdown:
            _set_status(project, "generating_dossier", "Generando dossier final.")
            output.final_dossier_markdown = generate_final_dossier(
                project,
                output.cleaned_transcript or "",
                output.block_summary or "",
            )
            db.session.commit()
            
        outputs_dir = project_outputs_dir(project.id)
        markdown_path = outputs_dir / "dossier.md"
        markdown_path.write_text(output.final_dossier_markdown or "", encoding="utf-8")
        docx_path = outputs_dir / "dossier.docx"
        markdown_to_docx(output.final_dossier_markdown or "", docx_path)
        output.final_dossier_docx_path = str(docx_path)
        project.status = "completed"
        db.session.add(ProjectLog(project_id=project.id, message="Dossier completado."))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        project = Project.query.get(project_id)
        if project:
            project.status = "failed"
            project.error_message = _friendly_error(exc)
            db.session.add(
                ProjectLog(
                    project_id=project.id,
                    message=f"Proyecto fallido: {project.error_message}",
                    level="error",
                )
            )
            db.session.commit()
        raise
