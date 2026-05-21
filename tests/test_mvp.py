from __future__ import annotations

import io
from pathlib import Path

from app.constants import (
    PROJECT_STATUSES,
    allowed_file,
    get_project_progress,
)
from app.models import Project, Template
from app.extensions import db
from app.services.export_service import markdown_to_docx
from app.services.openai_service import clean_transcript, transcribe_audio


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
        assert enqueued == [project.id]


def test_allowed_file_extensions():
    assert allowed_file("video.mp4")
    assert allowed_file("audio.MP3")
    assert allowed_file("mesa.webm")
    assert not allowed_file("script.sh")
    assert not allowed_file("archivo")


def test_template_crud(app):
    with app.app_context():
        t = Template(name="New Temp", prompt_instructions="Test")
        db.session.add(t)
        db.session.commit()
        assert t.id is not None
        assert Template.query.count() == 1


def test_status_flow_helpers():
    assert get_project_progress("uploaded") == 0
    assert get_project_progress("completed") == 100
    assert get_project_progress("failed") == 100
    for status in PROJECT_STATUSES:
        assert 0 <= get_project_progress(status) <= 100


def test_markdown_to_docx_creates_file(tmp_path):
    output_path = tmp_path / "dossier.docx"
    markdown_to_docx(
        "# Título\n\n## Ideas\n\n- Una idea\n- Otra idea\n\n1. Paso uno\n\nTexto final.",
        output_path,
    )

    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_openai_service_uses_mocked_client(app, tmp_path, monkeypatch):
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"fake")

    class FakeTranscription:
        text = "Transcripción simulada"

    class FakeAudioTranscriptions:
        def create(self, **kwargs):
            assert kwargs["model"] == app.config["OPENAI_TRANSCRIPTION_MODEL"]
            assert kwargs["language"] == "es"
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
        assert transcribe_audio(str(audio_path), "es") == "Transcripción simulada"
        assert clean_transcript("texto", "es") == "Texto limpio"
