from __future__ import annotations

import io
import json
from pathlib import Path

from app.constants import (
    DEFAULT_TRANSCRIPTION_MODEL,
    LANGUAGE_CHOICES,
    PROJECT_STATUSES,
    SOURCE_KIND_TRANSCRIPT_FILES,
    TRANSCRIPTION_MODEL_COSTS_USD_PER_MINUTE,
    TRANSCRIPTION_MODEL_CHOICES,
    allowed_file,
    allowed_transcript_file,
    estimate_transcription_cost_usd,
    get_project_progress,
)
from app.models import Project, ProjectOutput, Template, TranscriptChunk
from app.extensions import db
from app.services.export_service import get_transcript_content, markdown_to_docx, markdown_to_pdf, text_to_pdf
from app.services.media_service import (
    AudioChunk,
    CHUNK_OVERLAP_SECONDS,
    normalize_audio,
    parse_silencedetect_output,
    plan_audio_chunks,
)
from app.services.openai_service import clean_transcript, transcribe_audio
from app.services.prompts import (
    block_summary_prompt,
    clean_transcript_prompt,
    dossier_grounding_review_prompt,
    final_dossier_prompt,
)
from app.services.quality_service import normalize_grounding_review
from app.services.transcript_import_service import extract_transcript_text
from app.tasks import (
    _build_transcription_prompt,
    _dedupe_overlap,
    _extract_key_terms,
    process_project,
)


def test_project_creation(client, app, monkeypatch):
    enqueued: list[int] = []

    def fake_enqueue(project_id: int):
        enqueued.append(project_id)
        return "job-1"

    monkeypatch.setattr("app.routes.enqueue_project_processing", fake_enqueue)

    with app.app_context():
        t = Template(name="Test Template", prompt_instructions="- Seccion 1")
        db.session.add(t)
        db.session.commit()
        t_id = t.id

    response = client.post(
        "/projects",
        data={
            "title": "Jornada test",
            "client_name": "Cliente",
            "event_name": "Evento",
            "language": "es",
            "template_id": str(t_id),
            "glossary": "Acme Corp\nProducto X",
            "source_file": (io.BytesIO(b"fake audio"), "evento.mp3"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        project = Project.query.one()
        assert project.status == "queued"
        assert project.source_filename == "evento.mp3"
        assert Path(project.source_file_path).exists()
        assert project.template_id == t_id
        assert json.loads(project.glossary_json)["terms"] == ["Acme Corp", "Producto X"]
        assert project.transcription_model == DEFAULT_TRANSCRIPTION_MODEL
        assert enqueued == [project.id]


def test_project_creation_rejects_youtube_url_without_file(client, app, monkeypatch):
    enqueued: list[int] = []

    def fake_enqueue(project_id: int):
        enqueued.append(project_id)
        return "job-youtube"

    monkeypatch.setattr("app.routes.enqueue_project_processing", fake_enqueue)

    with app.app_context():
        t = Template(name="No YouTube", prompt_instructions="- Seccion")
        db.session.add(t)
        db.session.commit()
        t_id = t.id

    response = client.post(
        "/projects",
        data={
            "title": "Desde YouTube",
            "language": "es",
            "transcription_model": "gpt-4o-mini-transcribe",
            "template_id": str(t_id),
            "youtube_url": "https://www.youtube.com/watch?v=abc123",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        assert Project.query.count() == 0
        assert enqueued == []


def test_project_creation_from_transcript_files(client, app, monkeypatch):
    enqueued: list[int] = []

    def fake_enqueue(project_id: int):
        enqueued.append(project_id)
        return "job-3"

    monkeypatch.setattr("app.routes.enqueue_project_processing", fake_enqueue)

    with app.app_context():
        t = Template(name="Transcript Template", prompt_instructions="- Seccion TXT")
        db.session.add(t)
        db.session.commit()
        t_id = t.id

    response = client.post(
        "/projects",
        data={
            "title": "Desde transcripciones",
            "language": "es",
            "source_mode": SOURCE_KIND_TRANSCRIPT_FILES,
            "template_id": str(t_id),
            "transcript_files": [
                (io.BytesIO("Primera parte".encode("utf-8")), "parte-1.txt"),
                (io.BytesIO("Segunda parte".encode("utf-8")), "parte-2.md"),
                (io.BytesIO("3\n00:00:00,000 --> 00:00:02,000\nTercera parte".encode("utf-8")), "parte-3.srt"),
            ],
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        project = Project.query.one()
        assert project.status == "queued"
        assert project.source_kind == SOURCE_KIND_TRANSCRIPT_FILES
        assert "3 transcripciones" in project.source_filename
        manifest_path = Path(project.source_file_path)
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert [item["original_filename"] for item in manifest["files"]] == [
            "parte-1.txt",
            "parte-2.md",
            "parte-3.srt",
        ]
        assert enqueued == [project.id]


def test_allowed_file_extensions():
    assert allowed_file("video.mp4")
    assert allowed_file("audio.MP3")
    assert allowed_file("mesa.webm")
    assert not allowed_file("script.sh")
    assert not allowed_file("archivo")


def test_allowed_transcript_file_extensions():
    assert allowed_transcript_file("parte.txt")
    assert allowed_transcript_file("parte.MD")
    assert allowed_transcript_file("parte.docx")
    assert allowed_transcript_file("parte.srt")
    assert allowed_transcript_file("parte.vtt")
    assert not allowed_transcript_file("parte.pdf")
    assert not allowed_transcript_file("archivo")


def test_prompts_prioritize_explicit_source_facts():
    clean_prompt = clean_transcript_prompt("es")
    summary_prompt = block_summary_prompt("es")
    dossier_prompt = final_dossier_prompt("- Secciones", "es", "Titulo", None, None)
    review_prompt = dossier_grounding_review_prompt("es")

    assert "No inventes informacion" in clean_prompt
    assert "solo hechos explicitos en la fuente" in summary_prompt
    assert "Debes redactar solo con hechos explicitos en la fuente" in dossier_prompt
    assert "No consta en la transcripcion" in dossier_prompt
    assert "Devuelve solo JSON valido" in review_prompt


def test_normalize_grounding_review_handles_json_fences():
    report = normalize_grounding_review(
        """```json
        {
          "verdict": "critico",
          "summary": "Hay afirmaciones sin evidencia.",
          "supported_count": 2,
          "unsupported_count": 1,
          "issues": [
            {
              "claim": "Se aprobo un plan anual.",
              "problem": "no_en_fuente",
              "evidence": "Sin evidencia localizada",
              "recommendation": "Eliminar la afirmacion."
            }
          ]
        }
        ```"""
    )

    assert report["verdict"] == "critico"
    assert report["supported_count"] == 2
    assert report["unsupported_count"] == 1
    assert report["issues"][0]["problem"] == "no_en_fuente"


def test_transcription_cost_estimate_helpers():
    assert TRANSCRIPTION_MODEL_COSTS_USD_PER_MINUTE == {
        "gpt-4o-transcribe": 0.006,
        "gpt-4o-mini-transcribe": 0.003,
        "whisper-1": 0.006,
    }
    assert round(estimate_transcription_cost_usd(3600, "gpt-4o-transcribe"), 3) == 0.36
    assert round(estimate_transcription_cost_usd(3600, "gpt-4o-mini-transcribe"), 3) == 0.18
    assert estimate_transcription_cost_usd(None, "whisper-1") is None


def test_new_project_defaults_language_to_spanish(client):
    response = client.get("/projects/new")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert list(LANGUAGE_CHOICES.items()) == [
        ("ca", "Catalán"),
        ("en", "Inglés"),
        ("es", "Español"),
        ("auto", "Detectar automáticamente"),
    ]
    assert list(TRANSCRIPTION_MODEL_CHOICES) == [
        "gpt-4o-transcribe",
        "gpt-4o-mini-transcribe",
        "whisper-1",
    ]
    assert '<option value="es" selected>Español</option>' in html
    assert '<option value="gpt-4o-transcribe" selected>Alta calidad</option>' in html
    assert 'id="transcription-cost-estimate"' in html
    assert "Solo transcripción; no incluye limpieza, resumen ni dossier." in html
    assert 'name="source_mode" value="transcript_files"' in html
    assert 'name="transcript_files"' in html
    assert ".txt,.md,.docx,.srt,.vtt" in html
    assert 'id="upload-progress-bar"' in html
    assert 'id="upload-error"' in html
    assert "XMLHttpRequest" in html
    assert "Fallo tras" in html
    assert "0.003" in html


def test_project_creation_ajax_returns_json_validation_error(client):
    response = client.post(
        "/projects",
        data={},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert response.status_code == 400
    assert response.json == {"ok": False, "error": "El titulo es obligatorio."}


def test_project_creation_ajax_reports_upload_too_large(client, app):
    app.config["MAX_CONTENT_LENGTH"] = 1
    app.config["MAX_UPLOAD_MB"] = 0

    response = client.post(
        "/projects",
        data={"title": "Archivo grande", "source_file": (io.BytesIO(b"too-large"), "evento.mp3")},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert response.status_code == 413
    assert response.json["ok"] is False
    assert "limite de subida" in response.json["error"]


def test_project_creation_rejects_invalid_transcription_model(client, app, monkeypatch):
    monkeypatch.setattr("app.routes.enqueue_project_processing", lambda project_id: "job-1")
    with app.app_context():
        t = Template(name="Test Template", prompt_instructions="- Seccion 1")
        db.session.add(t)
        db.session.commit()
        t_id = t.id

    response = client.post(
        "/projects",
        data={
            "title": "Jornada test",
            "language": "es",
            "transcription_model": "modelo-inventado",
            "template_id": str(t_id),
            "source_file": (io.BytesIO(b"fake audio"), "evento.mp3"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        assert Project.query.count() == 0


def test_project_creation_rejects_invalid_transcript_file(client, app, monkeypatch):
    monkeypatch.setattr("app.routes.enqueue_project_processing", lambda project_id: "job-1")
    with app.app_context():
        t = Template(name="Test Template", prompt_instructions="- Seccion 1")
        db.session.add(t)
        db.session.commit()
        t_id = t.id

    response = client.post(
        "/projects",
        data={
            "title": "Jornada test",
            "language": "es",
            "source_mode": SOURCE_KIND_TRANSCRIPT_FILES,
            "template_id": str(t_id),
            "transcript_files": (io.BytesIO(b"fake"), "parte.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        assert Project.query.count() == 0


def test_project_creation_rejects_empty_transcript_file(client, app, monkeypatch):
    monkeypatch.setattr("app.routes.enqueue_project_processing", lambda project_id: "job-1")
    with app.app_context():
        t = Template(name="Test Template", prompt_instructions="- Seccion 1")
        db.session.add(t)
        db.session.commit()
        t_id = t.id

    response = client.post(
        "/projects",
        data={
            "title": "Jornada test",
            "language": "es",
            "source_mode": SOURCE_KIND_TRANSCRIPT_FILES,
            "template_id": str(t_id),
            "transcript_files": (io.BytesIO(b""), "parte.txt"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        assert Project.query.count() == 0


def test_project_status_shows_selected_transcription_model(client, app):
    with app.app_context():
        project = Project(
            title="Jornada en proceso",
            source_filename="evento.mp3",
            source_file_path="evento.mp3",
            language="es",
            transcription_model="gpt-4o-mini-transcribe",
            status="transcribing",
        )
        db.session.add(project)
        db.session.commit()
        project_id = project.id

    response = client.get(f"/projects/{project_id}/status")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Modelo" in html
    assert "Equilibrado" in html


def test_project_status_shows_transcript_file_source(client, app):
    with app.app_context():
        project = Project(
            title="Jornada con transcripciones",
            source_filename="2 transcripciones: parte-1.txt, parte-2.txt",
            source_file_path="manifest.json",
            source_kind=SOURCE_KIND_TRANSCRIPT_FILES,
            language="es",
            status="importing_transcript",
        )
        db.session.add(project)
        db.session.commit()
        project_id = project.id

    response = client.get(f"/projects/{project_id}/status")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Importar" in html
    assert "Archivos" in html
    assert "No aplica" in html
    assert "2 transcripciones" in html


def test_template_crud(app):
    with app.app_context():
        t = Template(name="New Temp", prompt_instructions="Test")
        db.session.add(t)
        db.session.commit()
        assert t.id is not None
        assert Template.query.count() == 1


def test_template_create_route(client, app):
    response = client.get("/templates/new")
    assert response.status_code == 200

    response = client.post(
        "/templates",
        data={"name": "Plantilla ruta", "prompt_instructions": "- Sección"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/templates"
    with app.app_context():
        template = Template.query.filter_by(name="Plantilla ruta").one()
        assert template.prompt_instructions == "- Sección"


def test_status_flow_helpers():
    assert get_project_progress("uploaded") == 0
    assert get_project_progress("completed") == 100
    assert get_project_progress("failed") == 100
    for status in PROJECT_STATUSES:
        assert 0 <= get_project_progress(status) <= 100


def test_project_detail_in_progress_has_public_link_placeholder(client, app):
    with app.app_context():
        project = Project(
            title="Jornada en proceso",
            source_filename="evento.mp3",
            source_file_path="evento.mp3",
            language="es",
            status="transcribing",
            share_token="token-en-proceso",
        )
        db.session.add(project)
        db.session.commit()
        project_id = project.id

    response = client.get(f"/projects/{project_id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="public-link-action"' in html
    assert "Ver Enlace Público" not in html


def test_project_detail_completed_shows_public_link(client, app):
    with app.app_context():
        project = Project(
            title="Jornada completada",
            source_filename="evento.mp3",
            source_file_path="evento.mp3",
            language="es",
            status="completed",
            share_token="token-completado",
        )
        db.session.add(project)
        db.session.commit()
        project_id = project.id

    response = client.get(f"/projects/{project_id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="public-link-action"' in html
    assert "Ver Enlace Público" in html
    assert "/p/token-completado" in html


def test_project_detail_completed_shows_ai_verification_button(client, app):
    with app.app_context():
        project = Project(
            title="Jornada verificable",
            source_filename="evento.mp3",
            source_file_path="evento.mp3",
            language="es",
            status="completed",
        )
        project.output = ProjectOutput(
            full_transcript="La ponente explico el plan.",
            cleaned_transcript="La ponente explico el plan.",
            final_dossier_markdown="# Dossier\n\nLa ponente explico el plan.",
        )
        db.session.add(project)
        db.session.commit()
        project_id = project.id

    response = client.get(f"/projects/{project_id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Verificar con IA" in html
    assert f"/projects/{project_id}/verify-dossier" in html


def test_verify_dossier_grounding_route_stores_quality_review(client, app, monkeypatch):
    monkeypatch.setattr(
        "app.routes.verify_dossier_against_transcript",
        lambda _transcript, _dossier, _language: json.dumps(
            {
                "verdict": "revisar",
                "summary": "Una afirmacion no aparece en la transcripcion.",
                "supported_count": 3,
                "unsupported_count": 1,
                "issues": [
                    {
                        "claim": "El equipo aprobo un plan anual.",
                        "problem": "no_en_fuente",
                        "evidence": "Sin evidencia localizada",
                        "recommendation": "Eliminar o marcar como No consta.",
                    }
                ],
            }
        ),
    )
    with app.app_context():
        project = Project(
            title="Jornada verificable",
            source_filename="evento.mp3",
            source_file_path="evento.mp3",
            language="es",
            status="completed",
        )
        project.output = ProjectOutput(
            full_transcript="La ponente explico el plan.",
            cleaned_transcript="La ponente explico el plan.",
            final_dossier_markdown="# Dossier\n\nEl equipo aprobo un plan anual.",
        )
        db.session.add(project)
        db.session.commit()
        project_id = project.id

    response = client.post(f"/projects/{project_id}/verify-dossier", follow_redirects=False)

    assert response.status_code == 302
    with app.app_context():
        project = Project.query.get(project_id)
        quality = json.loads(project.output.quality_report_json)
        review = quality["grounding_review"]
        assert review["verdict"] == "revisar"
        assert review["unsupported_count"] == 1
        assert review["issues"][0]["claim"] == "El equipo aprobo un plan anual."
        assert any("Verificacion factual completada: revisar" in log.message for log in project.logs)

    response = client.get(f"/projects/{project_id}")
    html = response.get_data(as_text=True)
    assert "Verificacion factual" in html
    assert "El equipo aprobo un plan anual." in html


def test_project_status_htmx_includes_public_link_oob_when_completed(client, app):
    with app.app_context():
        project = Project(
            title="Jornada completada",
            source_filename="evento.mp3",
            source_file_path="evento.mp3",
            language="es",
            status="completed",
            share_token="token-htmx",
        )
        db.session.add(project)
        db.session.commit()
        project_id = project.id

    response = client.get(
        f"/projects/{project_id}/status",
        headers={"HX-Request": "true"},
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="public-link-action"' in html
    assert 'hx-swap-oob="true"' in html
    assert "Ver Enlace Público" in html
    assert "/p/token-htmx" in html


def test_shared_dossier_supports_dark_theme_toggle(client, app):
    with app.app_context():
        project = Project(
            title="Dossier público",
            source_filename="evento.mp3",
            source_file_path="evento.mp3",
            language="es",
            status="completed",
            share_token="token-publico",
        )
        project.output = ProjectOutput(
            final_dossier_markdown="# Título público",
            full_transcript="Transcripcion visible",
        )
        db.session.add(project)
        db.session.commit()

    response = client.get("/p/token-publico")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert '<html lang="es" data-theme="dark">' in html
    assert 'localStorage.getItem("dossiers-theme")' in html
    assert 'id="theme-toggle"' in html
    assert "Cambiar a tema claro" in html
    assert "Markdown" in html
    assert "Ver transcripcion" in html
    assert "Transcripcion visible" in html
    assert "localStorage.setItem(\"dossiers-theme\", nextTheme)" in html

    response = client.get("/p/token-publico/download/markdown")
    assert response.status_code == 200
    assert "# Título público" in response.get_data(as_text=True)


def test_markdown_to_docx_creates_file(tmp_path):
    output_path = tmp_path / "dossier.docx"
    markdown_to_docx(
        "# Título\n\n## Ideas\n\n- Una idea\n- Otra idea\n\n1. Paso uno\n\nTexto final.",
        output_path,
    )

    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_transcript_content_uses_time_ranges_before_paragraphs(app):
    with app.app_context():
        project = Project(
            title="Jornada test",
            source_filename="evento.mp3",
            source_file_path="evento.mp3",
            language="es",
            status="completed",
        )
        project.output = ProjectOutput(full_transcript="Texto sin marcas")
        project.chunks.append(
            TranscriptChunk(
                chunk_index=1,
                audio_path="chunk.mp3",
                start_seconds=0,
                end_seconds=90,
                status="completed",
                transcript_text="Texto con marcas",
                segments_json=json.dumps(
                    [
                        {"start": 0.0, "end": 39.0, "text": "Primer bloque."},
                        {"start": 39.0, "end": 80.0, "text": "Segundo bloque."},
                    ]
                ),
            )
        )
        db.session.add(project)
        db.session.commit()

        content = get_transcript_content(project, include_timestamps=True)

    assert content == (
        "(0:00 - 0:39)\n"
        "Primer bloque.\n\n"
        "(0:39 - 1:20)\n"
        "Segundo bloque."
    )


def test_text_to_pdf_creates_pdf_file(tmp_path):
    output_path = tmp_path / "transcripcion.pdf"
    text_to_pdf("(0:00 - 0:11)\nTexto con acentos y preguntas: ¿qué tal?", output_path)

    assert output_path.exists()
    assert output_path.read_bytes().startswith(b"%PDF-1.4")
    assert output_path.stat().st_size > 0


def test_markdown_to_pdf_creates_dossier_pdf(tmp_path):
    output_path = tmp_path / "dossier.pdf"
    markdown_to_pdf("# Titulo\n\n## Resumen ejecutivo\n\n**Texto** final.", output_path)

    assert output_path.exists()
    assert output_path.read_bytes().startswith(b"%PDF-1.4")
    assert output_path.stat().st_size > 0


def test_extract_transcript_text_reads_supported_formats(tmp_path):
    txt_path = tmp_path / "parte.txt"
    md_path = tmp_path / "parte.md"
    srt_path = tmp_path / "parte.srt"
    vtt_path = tmp_path / "parte.vtt"
    docx_path = tmp_path / "parte.docx"

    txt_path.write_text("Texto plano", encoding="utf-8")
    md_path.write_text("# Titulo\n\nTexto markdown", encoding="utf-8")
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nTexto subtitulo\n\n",
        encoding="utf-8",
    )
    vtt_path.write_text(
        "WEBVTT\n\n00:00.000 --> 00:02.000\nTexto webvtt\n\n",
        encoding="utf-8",
    )

    from docx import Document

    document = Document()
    document.add_paragraph("Texto docx")
    document.save(docx_path)

    assert extract_transcript_text(txt_path) == "Texto plano"
    assert "Texto markdown" in extract_transcript_text(md_path)
    assert extract_transcript_text(srt_path) == "Texto subtitulo"
    assert extract_transcript_text(vtt_path) == "Texto webvtt"
    assert extract_transcript_text(docx_path) == "Texto docx"


def test_extract_transcript_text_rejects_empty_and_unsupported(tmp_path):
    empty_path = tmp_path / "vacia.txt"
    pdf_path = tmp_path / "parte.pdf"
    empty_path.write_text("", encoding="utf-8")
    pdf_path.write_bytes(b"fake")

    import pytest

    with pytest.raises(ValueError):
        extract_transcript_text(empty_path)
    with pytest.raises(ValueError):
        extract_transcript_text(pdf_path)


def test_parse_silencedetect_output_reads_ranges():
    output = """
    [silencedetect @ 000] silence_start: 1000.12
    [silencedetect @ 000] silence_end: 1001.02 | silence_duration: 0.90
    [silencedetect @ 000] silence_start: 1200
    [silencedetect @ 000] silence_end: 1202.50 | silence_duration: 2.50
    """

    assert parse_silencedetect_output(output) == [(1000.12, 1001.02), (1200.0, 1202.5)]


def test_plan_audio_chunks_prefers_silences_inside_window():
    chunks = plan_audio_chunks(
        3000,
        [(1000, 1203), (2390, 2406)],
    )

    assert chunks == [(0.0, 1203), (1188, 2406), (2391, 3000)]


def test_plan_audio_chunks_falls_back_to_safe_limit_without_silence():
    chunks = plan_audio_chunks(3000, [])

    assert chunks == [(0.0, 1320.0), (1305.0, 2625.0), (2610.0, 3000)]


def test_plan_audio_chunks_respects_size_limit_and_overlap():
    chunks = plan_audio_chunks(
        900,
        [],
        max_chunk_bytes=5 * 1024 * 1024,
        audio_bitrate_bps=64_000,
    )

    assert chunks == [(0.0, 655.0), (640.0, 900)]


def test_dedupe_overlap_removes_repeated_overlap_text():
    previous = "Primero una introduccion clara y luego nombres propios repetidos"
    current = "nombres propios repetidos con el cierre final"

    assert _dedupe_overlap(previous, current) == "con el cierre final"


def test_chunk_overlap_increased_to_15():
    assert CHUNK_OVERLAP_SECONDS == 15


def test_dedupe_overlap_handles_longer_overlap():
    previous = ("palabra " * 50).strip() + " final compartido entre chunks"
    current = "final compartido entre chunks y aquí sigue el nuevo contenido"
    result = _dedupe_overlap(previous, current)
    assert result == "y aquí sigue el nuevo contenido"


def test_normalize_audio_applies_correct_filters(monkeypatch):
    commands: list[list[str]] = []

    def fake_run(command):
        commands.append(command)

    monkeypatch.setattr("app.services.media_service._run", fake_run)

    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        inp = Path(tmpdir) / "input.mp3"
        out = Path(tmpdir) / "output.mp3"
        inp.write_bytes(b"fake")
        normalize_audio(inp, out)

    assert len(commands) == 1
    cmd = commands[0]
    assert "-af" in cmd
    af_value = cmd[cmd.index("-af") + 1]
    assert "highpass=f=80" in af_value
    assert "lowpass=f=8000" in af_value
    assert "afftdn=nf=-20" in af_value
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in af_value


def test_extract_key_terms_finds_proper_nouns():
    text = (
        "María presentó el proyecto. Juan revisó los datos. "
        "María y Juan colaboraron con Pedro. María mencionó a Pedro."
    )
    terms = _extract_key_terms(text)
    assert "María" in terms
    assert "Juan" in terms
    assert "Pedro" in terms


def test_extract_key_terms_filters_common_words():
    text = "El gato. La casa. Los perros. En Madrid hay sol. Madrid es grande. Madrid tiene metro."
    terms = _extract_key_terms(text)
    assert "Madrid" in terms
    assert "El" not in terms
    assert "La" not in terms
    assert "Los" not in terms
    assert "En" not in terms


def test_build_transcription_prompt_includes_key_terms(app):
    with app.app_context():
        project = Project(
            title="Jornada Innovation",
            client_name="Acme Corp",
            event_name="Summit 2025",
            source_filename="evento.mp3",
            source_file_path="evento.mp3",
            language="es",
            glossary_json=json.dumps({"terms": ["Producto Atlas", "Fundacion Norte"]}),
            status="transcribing",
        )
        previous = (
            "Martínez explicó la estrategia. Luego García detalló el plan. "
            "Martínez reafirmó la visión. García insistió en el calendario."
        )
        prompt = _build_transcription_prompt(project, previous)

    assert "Vocabulario recurrente" in prompt
    assert "Martínez" in prompt
    assert "García" in prompt
    assert "Producto Atlas" in prompt
    assert "Fundacion Norte" in prompt
    assert "Jornada Innovation" in prompt
    assert "Acme Corp" in prompt


def test_process_project_imports_transcript_files_without_transcribing(app, tmp_path, monkeypatch):
    def fail_media_step(*_args, **_kwargs):
        raise AssertionError("media processing should not run")

    monkeypatch.setattr("app.tasks.get_media_duration", fail_media_step)
    monkeypatch.setattr("app.tasks.prepare_audio", fail_media_step)
    monkeypatch.setattr("app.tasks.split_audio", fail_media_step)
    monkeypatch.setattr("app.tasks.transcribe_audio", fail_media_step)
    monkeypatch.setattr("app.tasks.clean_transcript", lambda text, _language: text)
    monkeypatch.setattr("app.tasks.summarize_chunk", lambda text, _language: "Resumen")
    monkeypatch.setattr("app.tasks.generate_final_dossier", lambda *_args: "# Dossier")
    monkeypatch.setattr(
        "app.tasks.markdown_to_docx",
        lambda _markdown, output_path: Path(output_path).write_bytes(b"docx"),
    )

    with app.app_context():
        source_dir = tmp_path / "transcripts"
        source_dir.mkdir()
        part_1 = source_dir / "01-parte.txt"
        part_2 = source_dir / "02-parte.txt"
        part_1.write_text("apertura con nombres propios repetidos", encoding="utf-8")
        part_2.write_text("nombres propios repetidos y cierre final", encoding="utf-8")
        manifest_path = source_dir / "transcript_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "files": [
                        {
                            "order": 1,
                            "original_filename": "parte-1.txt",
                            "stored_filename": part_1.name,
                            "path": str(part_1),
                            "size": part_1.stat().st_size,
                        },
                        {
                            "order": 2,
                            "original_filename": "parte-2.txt",
                            "stored_filename": part_2.name,
                            "path": str(part_2),
                            "size": part_2.stat().st_size,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        template = Template(name="Plantilla", prompt_instructions="- Seccion")
        project = Project(
            title="Jornada de producto",
            source_filename="2 transcripciones: parte-1.txt, parte-2.txt",
            source_file_path=str(manifest_path),
            source_kind=SOURCE_KIND_TRANSCRIPT_FILES,
            language="es",
            template=template,
            status="queued",
        )
        db.session.add(project)
        db.session.commit()
        project_id = project.id

        process_project(project_id)

        project = Project.query.get(project_id)
        assert project.status == "completed"
        assert project.chunks == []
        assert (
            project.output.full_transcript
            == "apertura con nombres propios repetidos\n\ny cierre final"
        )
        assert project.output.final_dossier_docx_path
        assert project.output.final_dossier_pdf_path
        assert Path(project.output.final_dossier_pdf_path).exists()
        quality = json.loads(project.output.quality_report_json)
        assert quality["metrics"]["source_kind"] == SOURCE_KIND_TRANSCRIPT_FILES
        assert quality["content"]["transcript_word_count"] > 0
        variants = json.loads(project.output.output_variants_json)
        assert variants["dossier_largo"] == "# Dossier"
        assert "posts_redes" in variants


def test_process_project_reports_failed_chunk_context(app, tmp_path, monkeypatch):
    def fake_prepare_audio(_source_path, output_path):
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return path

    def fake_split_audio(_audio_path, output_dir, _chunk_minutes):
        output = Path(output_dir)
        chunk_path = output / "chunk_001.mp3"
        chunk_path.parent.mkdir(parents=True, exist_ok=True)
        chunk_path.write_bytes(b"chunk-audio")
        return [AudioChunk(chunk_path, 0, 1200)]

    def fake_transcribe_audio(*_args, **_kwargs):
        raise RuntimeError("OpenAI timeout")

    monkeypatch.setattr("app.tasks.ensure_media_tools_available", lambda: None)
    monkeypatch.setattr("app.tasks.get_media_duration", lambda _path: 1200)
    monkeypatch.setattr("app.tasks.prepare_audio", fake_prepare_audio)
    monkeypatch.setattr("app.tasks.split_audio", fake_split_audio)
    monkeypatch.setattr("app.tasks.transcribe_audio", fake_transcribe_audio)

    with app.app_context():
        source_path = tmp_path / "source.mp3"
        source_path.write_bytes(b"audio")
        template = Template(name="Plantilla", prompt_instructions="- Seccion")
        project = Project(
            title="Jornada fallida",
            source_filename=source_path.name,
            source_file_path=str(source_path),
            language="es",
            template=template,
            status="queued",
        )
        db.session.add(project)
        db.session.commit()
        project_id = project.id

        import pytest

        with pytest.raises(RuntimeError):
            process_project(project_id)

        project = Project.query.get(project_id)
        assert project.status == "failed"
        assert "Fallo al transcribir el fragmento 1/1" in project.error_message
        assert "OpenAI timeout" in project.error_message
        assert project.chunks[0].status == "failed"
        assert "0:00-20:00" in project.chunks[0].error_message
        assert any(
            "Fallo al transcribir el fragmento 1/1" in log.message
            for log in project.logs
        )


def test_process_project_uses_project_transcription_model(app, tmp_path, monkeypatch):
    calls: list[dict] = []

    def fake_prepare_audio(_source_path, output_path):
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return path

    def fake_split_audio(_audio_path, output_dir, _chunk_minutes):
        output = Path(output_dir)
        return [
            AudioChunk(output / "chunk_001.mp3", 0, 1200),
            AudioChunk(output / "chunk_002.mp3", 1194, 1800),
        ]

    def fake_transcribe_audio(file_path, language, model=None, prompt=None):
        calls.append(
            {
                "file_path": file_path,
                "language": language,
                "model": model,
                "prompt": prompt,
            }
        )
        if len(calls) == 1:
            return "apertura con nombres propios repetidos", []
        return "nombres propios repetidos y cierre final", []

    monkeypatch.setattr("app.tasks.get_media_duration", lambda _path: 1800)
    monkeypatch.setattr("app.tasks.ensure_media_tools_available", lambda: None)
    monkeypatch.setattr("app.tasks.prepare_audio", fake_prepare_audio)
    monkeypatch.setattr("app.tasks.split_audio", fake_split_audio)
    monkeypatch.setattr("app.tasks.transcribe_audio", fake_transcribe_audio)
    monkeypatch.setattr("app.tasks.clean_transcript", lambda text, _language: text)
    monkeypatch.setattr("app.tasks.summarize_chunk", lambda text, _language: "Resumen")
    monkeypatch.setattr("app.tasks.generate_final_dossier", lambda *_args: "# Dossier")
    monkeypatch.setattr(
        "app.tasks.markdown_to_docx",
        lambda _markdown, output_path: Path(output_path).write_bytes(b"docx"),
    )

    with app.app_context():
        source_path = tmp_path / "source.mp4"
        source_path.write_bytes(b"video")
        template = Template(name="Plantilla", prompt_instructions="- Seccion")
        project = Project(
            title="Jornada de producto",
            client_name="Cliente Uno",
            event_name="Evento anual",
            source_filename=source_path.name,
            source_file_path=str(source_path),
            language="es",
            transcription_model="whisper-1",
            template=template,
            status="queued",
        )
        db.session.add(project)
        db.session.commit()
        project_id = project.id

        process_project(project_id)

        project = Project.query.get(project_id)
        assert project.status == "completed"
        assert [call["model"] for call in calls] == ["whisper-1", "whisper-1"]
        assert calls[0]["language"] == "es"
        assert "Jornada de producto" in calls[0]["prompt"]
        assert "Cliente Uno" in calls[0]["prompt"]
        assert "apertura con nombres propios repetidos" in calls[1]["prompt"]
        assert [chunk.start_seconds for chunk in project.chunks] == [0, 1194]
        assert [chunk.end_seconds for chunk in project.chunks] == [1200, 1800]
        assert (
            project.output.full_transcript
            == "apertura con nombres propios repetidos\n\ny cierre final"
        )


def test_reviewed_transcript_regeneration_uses_reviewed_text(client, app, tmp_path, monkeypatch):
    monkeypatch.setattr("app.tasks.summarize_chunk", lambda text, _language: f"Resumen de {text}")
    monkeypatch.setattr(
        "app.tasks.generate_final_dossier",
        lambda _project, transcript, _summary: (
            "# Dossier\n\n## Resumen ejecutivo\n\n"
            f"{transcript}\n\n## Ideas clave\n\n- Idea"
        ),
    )
    monkeypatch.setattr(
        "app.tasks.markdown_to_docx",
        lambda _markdown, output_path: Path(output_path).write_bytes(b"docx"),
    )
    monkeypatch.setattr(
        "app.tasks.markdown_to_pdf",
        lambda _markdown, output_path: Path(output_path).write_bytes(b"%PDF-1.4\n"),
    )

    with app.app_context():
        template = Template(name="Plantilla", prompt_instructions="- Seccion")
        project = Project(
            title="Jornada revisable",
            source_filename="evento.mp3",
            source_file_path="evento.mp3",
            language="es",
            template=template,
            status="completed",
        )
        project.output = ProjectOutput(
            full_transcript="Texto original",
            cleaned_transcript="Texto limpio",
            block_summary="Resumen antiguo",
            final_dossier_markdown="# Antiguo",
        )
        db.session.add(project)
        db.session.commit()
        project_id = project.id

    response = client.post(
        f"/projects/{project_id}/transcript/review",
        data={
            "reviewed_transcript": "(0:00 - 0:05)\nTexto revisado con Speaker 1",
            "glossary": "Speaker 1\nProducto Alfa",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        project = Project.query.get(project_id)
        assert project.status == "reviewing_transcript"
        assert "Texto revisado" in project.output.reviewed_transcript
        assert json.loads(project.glossary_json)["terms"] == ["Speaker 1", "Producto Alfa"]

    response = client.post(
        f"/projects/{project_id}/regenerate",
        data={"target": "all"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        project = Project.query.get(project_id)
        assert project.status == "completed"
        assert "Texto revisado con Speaker 1" in project.output.final_dossier_markdown
        assert Path(project.output.final_dossier_docx_path).exists()
        assert Path(project.output.final_dossier_pdf_path).exists()
        assert json.loads(project.output.quality_report_json)["content"]["has_reviewed_transcript"]
        assert json.loads(project.output.output_variants_json)["resumen_ejecutivo"]

    response = client.get(f"/projects/{project_id}/download/pdf")
    assert response.status_code == 200
    assert response.data.startswith(b"%PDF-1.4")


def test_openai_service_uses_mocked_client(app, tmp_path, monkeypatch):
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"fake")

    class FakeTranscription:
        text = "Transcripción simulada"
        segments = [{"start": 0.0, "end": 2.0, "text": "Transcripción simulada"}]

    class FakeAudioTranscriptions:
        def create(self, **kwargs):
            assert kwargs["model"] == app.config["OPENAI_TRANSCRIPTION_MODEL"]
            assert kwargs["language"] == "es"
            assert "response_format" not in kwargs
            return FakeTranscription()

    class FakeAudio:
        transcriptions = FakeAudioTranscriptions()

    class FakeTextResponse:
        output_text = "Texto limpio"

    class FakeResponses:
        def create(self, **kwargs):
            assert kwargs["model"] == app.config["OPENAI_SUMMARY_MODEL"]
            return FakeTextResponse()

    class FakeClient:
        audio = FakeAudio()
        responses = FakeResponses()

    monkeypatch.setattr("app.services.openai_service._client", lambda: FakeClient())

    with app.app_context():
        assert transcribe_audio(str(audio_path), "es") == ("Transcripción simulada", [{"start": 0.0, "end": 2.0, "text": "Transcripción simulada"}])
        assert clean_transcript("texto", "es") == "Texto limpio"


def test_openai_transcription_retries_transient_errors(app, tmp_path, monkeypatch):
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"fake")
    calls = {"count": 0}

    class TemporaryOpenAIError(Exception):
        status_code = 429

    class FakeTranscription:
        text = "Transcripcion tras reintento"
        segments = []

    class FakeAudioTranscriptions:
        def create(self, **_kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise TemporaryOpenAIError("rate limited")
            return FakeTranscription()

    class FakeAudio:
        transcriptions = FakeAudioTranscriptions()

    class FakeClient:
        audio = FakeAudio()

    monkeypatch.setattr("app.services.openai_service._client", lambda: FakeClient())
    monkeypatch.setattr("app.services.openai_service.time.sleep", lambda _seconds: None)

    with app.app_context():
        assert transcribe_audio(str(audio_path), "es") == (
            "Transcripcion tras reintento",
            [],
        )
        assert calls["count"] == 2


def test_openai_service_keeps_verbose_json_for_whisper(app, tmp_path, monkeypatch):
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"fake")

    class FakeTranscription:
        text = "Transcripcion whisper"
        segments = [{"start": 0.0, "end": 2.0, "text": "Transcripcion whisper"}]

    class FakeAudioTranscriptions:
        def create(self, **kwargs):
            assert kwargs["model"] == "whisper-1"
            assert kwargs["language"] == "es"
            assert kwargs["prompt"] == "Contexto"
            assert kwargs["response_format"] == "verbose_json"
            return FakeTranscription()

    class FakeAudio:
        transcriptions = FakeAudioTranscriptions()

    class FakeClient:
        audio = FakeAudio()

    monkeypatch.setattr("app.services.openai_service._client", lambda: FakeClient())

    with app.app_context():
        assert transcribe_audio(
            str(audio_path),
            "es",
            model="whisper-1",
            prompt="Contexto",
        ) == (
            "Transcripcion whisper",
            [{"start": 0.0, "end": 2.0, "text": "Transcripcion whisper"}],
        )
