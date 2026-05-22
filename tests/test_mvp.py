from __future__ import annotations

import io
import json
from pathlib import Path

from app.constants import (
    DEFAULT_TRANSCRIPTION_MODEL,
    LANGUAGE_CHOICES,
    PROJECT_STATUSES,
    TRANSCRIPTION_MODEL_COSTS_USD_PER_MINUTE,
    TRANSCRIPTION_MODEL_CHOICES,
    allowed_file,
    estimate_transcription_cost_usd,
    get_project_progress,
)
from app.models import Project, ProjectOutput, Template, TranscriptChunk
from app.extensions import db
from app.services.export_service import get_transcript_content, markdown_to_docx, text_to_pdf
from app.services.media_service import AudioChunk, parse_silencedetect_output, plan_audio_chunks
from app.services.openai_service import clean_transcript, transcribe_audio
from app.tasks import _dedupe_overlap, process_project


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
        assert project.transcription_model == DEFAULT_TRANSCRIPTION_MODEL
        assert enqueued == [project.id]


def test_project_creation_from_youtube_url(client, app, tmp_path, monkeypatch):
    enqueued: list[int] = []

    def fake_enqueue(project_id: int):
        enqueued.append(project_id)
        return "job-2"

    def fake_download(url: str, output_dir):
        path = Path(output_dir) / "youtube_source.mp3"
        path.write_bytes(b"fake youtube audio")
        return path

    monkeypatch.setattr("app.routes.enqueue_project_processing", fake_enqueue)
    monkeypatch.setattr("app.routes.download_youtube_media", fake_download)

    with app.app_context():
        t = Template(name="YT Template", prompt_instructions="- Seccion YT")
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
        project = Project.query.one()
        assert project.status == "queued"
        assert project.source_filename == "youtube_source.mp3"
        assert project.transcription_model == "gpt-4o-mini-transcribe"
        assert Path(project.source_file_path).exists()
        assert enqueued == [project.id]


def test_allowed_file_extensions():
    assert allowed_file("video.mp4")
    assert allowed_file("audio.MP3")
    assert allowed_file("mesa.webm")
    assert not allowed_file("script.sh")
    assert not allowed_file("archivo")


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
    assert "0.003" in html


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
        project.output = ProjectOutput(final_dossier_markdown="# Título público")
        db.session.add(project)
        db.session.commit()

    response = client.get("/p/token-publico")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert '<html lang="es" data-theme="dark">' in html
    assert 'localStorage.getItem("dossiers-theme")' in html
    assert 'id="theme-toggle"' in html
    assert "Cambiar a tema claro" in html
    assert "localStorage.setItem(\"dossiers-theme\", nextTheme)" in html


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

    assert chunks == [(0.0, 1203), (1197, 2406), (2400, 3000)]


def test_plan_audio_chunks_falls_back_to_safe_limit_without_silence():
    chunks = plan_audio_chunks(3000, [])

    assert chunks == [(0.0, 1320), (1314, 2634), (2628, 3000)]


def test_plan_audio_chunks_respects_size_limit_and_overlap():
    chunks = plan_audio_chunks(
        900,
        [],
        max_chunk_bytes=5 * 1024 * 1024,
        audio_bitrate_bps=64_000,
    )

    assert chunks == [(0.0, 655), (649, 900)]


def test_dedupe_overlap_removes_repeated_overlap_text():
    previous = "Primero una introduccion clara y luego nombres propios repetidos"
    current = "nombres propios repetidos con el cierre final"

    assert _dedupe_overlap(previous, current) == "con el cierre final"


def test_process_project_uses_project_transcription_model(app, tmp_path, monkeypatch):
    calls: list[dict] = []

    def fake_extract_audio(_source_path, output_path):
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
    monkeypatch.setattr("app.tasks.extract_audio", fake_extract_audio)
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
