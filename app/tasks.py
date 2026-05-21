from __future__ import annotations

from pathlib import Path

from flask import current_app, has_app_context

from app.extensions import db
from app.models import Project, ProjectLog, ProjectOutput, TranscriptChunk
from app.services.export_service import markdown_to_docx
from app.services.media_service import extract_audio, get_media_duration, split_audio
from app.services.openai_service import (
    clean_transcript,
    generate_final_dossier,
    summarize_chunk,
    transcribe_audio,
)
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


def _process_project(project_id: int) -> None:
    project = Project.query.get(project_id)
    if not project:
        return

    try:
        source_path = Path(project.source_file_path)
        audio_path = project_audio_dir(project.id) / "audio.mp3"
        chunk_minutes = current_app.config["TRANSCRIPT_CHUNK_MINUTES"]

        _set_status(project, "extracting_audio", "Extrayendo audio con ffmpeg.")
        project.duration_seconds = get_media_duration(source_path)
        extract_audio(source_path, audio_path)
        db.session.commit()

        _set_status(project, "splitting_audio", "Dividiendo audio en fragmentos.")
        chunk_paths = split_audio(audio_path, project_chunks_dir(project.id), chunk_minutes)
        for index, chunk_path in enumerate(chunk_paths, start=1):
            start = (index - 1) * chunk_minutes * 60
            end = index * chunk_minutes * 60
            if project.duration_seconds:
                end = min(end, project.duration_seconds)
            db.session.add(
                TranscriptChunk(
                    project_id=project.id,
                    chunk_index=index,
                    audio_path=str(chunk_path),
                    start_seconds=start,
                    end_seconds=end,
                    status="pending",
                )
            )
        db.session.commit()

        _set_status(project, "transcribing", "Transcribiendo fragmentos con OpenAI.")
        transcripts: list[str] = []
        for chunk in project.chunks:
            chunk.status = "transcribing"
            db.session.commit()
            try:
                chunk.transcript_text = transcribe_audio(chunk.audio_path, project.language)
                chunk.status = "completed"
                transcripts.append(chunk.transcript_text or "")
                db.session.commit()
            except Exception as exc:
                chunk.status = "failed"
                chunk.error_message = str(exc)
                db.session.commit()
                raise

        full_transcript = "\n\n".join(transcripts).strip()
        output = ProjectOutput.query.filter_by(project_id=project.id).one_or_none()
        if not output:
            output = ProjectOutput(project_id=project.id)
            db.session.add(output)
        output.full_transcript = full_transcript
        db.session.commit()

        _set_status(project, "cleaning_transcript", "Limpiando transcripción.")
        output.cleaned_transcript = clean_transcript(full_transcript, project.language)
        db.session.commit()

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

        _set_status(project, "generating_dossier", "Generando dossier final.")
        output.final_dossier_markdown = generate_final_dossier(
            project,
            output.cleaned_transcript or "",
            output.block_summary or "",
        )
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
            project.error_message = str(exc)
            db.session.add(ProjectLog(project_id=project.id, message=str(exc), level="error"))
            db.session.commit()
        raise
