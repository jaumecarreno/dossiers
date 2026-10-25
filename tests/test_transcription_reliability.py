from pathlib import Path
from types import SimpleNamespace
import json
import os
import shutil
import subprocess
import wave
import math
from array import array

import pytest

from app.services.media_service import plan_audio_chunks, get_media_duration
from app.services.openai_service import clean_transcript, transcribe_audio
from app.services.reliable_transcription_service import transcribe_complete
from app.services.transcription_guard import IncompleteTranscription, tokenizer, validate_transcription
from app.services.export_service import get_transcript_content
from app.tasks import _check_audio_coverage, _store_full_transcript, _build_transcription_prompt, process_project
from app.models import Project, ProjectOutput, TranscriptChunk, Template
from app.extensions import db


@pytest.fixture
def extraction(monkeypatch):
    calls = []
    def extract(source, dest, start, end):
        calls.append((start, end))
        Path(dest).write_bytes(b"audio")
        return dest
    monkeypatch.setattr("app.services.reliable_transcription_service.extract_audio_range", extract)
    return calls


def run(transcriber, duration=180, **kwargs):
    return transcribe_complete("source.mp3", "auto", model="gpt-4o-transcribe",
                               prompt="Transcribe todo.", duration_seconds=duration,
                               transcriber=transcriber, **kwargs)


def test_old_20_minute_configuration_cannot_bypass_safe_limit():
    spans = plan_audio_chunks(5960.75, [], target_seconds=1200,
                              min_seconds=960, max_seconds=1320)
    assert spans[0][0] == 0
    assert spans[-1][1] == 5960.75
    assert all(0 < end-start <= 180 for start, end in spans)
    assert all(right[0] <= left[1] for left, right in zip(spans, spans[1:]))
    assert sum(end-start for start, end in spans) / 5960.75 < 1.03


def test_shorter_config_does_not_create_seconds_of_hop_over_audio():
    spans = plan_audio_chunks(1000, [], target_seconds=60)
    assert spans[-1][1] == 1000
    assert all(end-start <= 60 for start, end in spans)


def test_duration_keeps_fractional_final_second(monkeypatch):
    monkeypatch.setattr("app.services.media_service._run", lambda _: SimpleNamespace(stdout="5960.75\n"))
    assert get_media_duration("source.mp3") == 5960.75


def test_incident_shape_2047_tokens_is_rejected_without_usage():
    text = " palabra" * 2047
    assert len(tokenizer().encode(text)) == 2047
    with pytest.raises(IncompleteTranscription, match="limite"):
        validate_transcription(text, "gpt-4o-transcribe")


def test_near_limit_usage_and_empty_are_rejected():
    with pytest.raises(IncompleteTranscription):
        validate_transcription("texto", "gpt-4o-mini-transcribe", 2000)
    with pytest.raises(IncompleteTranscription):
        validate_transcription(" ", "whisper-1")


def test_recovery_discards_truncated_parent_keeps_both_children(extraction):
    calls = []
    left = "Inicio. " + "contenido " * 50 + "frase del solapamiento"
    right = "frase del solapamiento " + "continuacion " * 50 + "DESPEDIDA FINAL."
    def transcriber(*a, **kw):
        calls.append(kw)
        if len(calls) == 1:
            return " palabra" * 2047, []
        return (left if len(calls) == 2 else right), []
    text, segments = run(transcriber)
    assert len(calls) == 3
    assert extraction == [(0, 92.5), (87.5, 180)]
    assert text.startswith("Inicio.") and text.endswith("DESPEDIDA FINAL.")
    assert text.count("frase del solapamiento") == 1
    assert "palabra" not in text
    assert segments[0]["start"] == 0 and segments[-1]["end"] == 180
    assert all(s["transcription_checked"] == 1 for s in segments)


def test_normal_response_uses_one_request_and_no_extractions(extraction):
    calls = []
    def transcriber(*a, **kw):
        calls.append(1)
        return "contenido " * 100, []
    text, segments = run(transcriber)
    assert len(calls) == 1 and extraction == []
    assert text.split() == ["contenido"] * 100
    assert segments[0]["timing"] == "audio_interval"


def test_repeated_truncation_stops_after_bounded_cost(extraction):
    calls = []
    def transcriber(*a, **kw):
        calls.append(1)
        raise IncompleteTranscription("recortada")
    with pytest.raises(IncompleteTranscription, match="sin aceptar texto parcial"):
        run(transcriber)
    assert len(calls) == 4
    assert len(extraction) == 3


def test_failed_second_child_does_not_return_first_as_complete(extraction):
    calls = []
    def transcriber(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise IncompleteTranscription("recortada")
        if len(calls) == 2:
            return "primera parte " * 50, []
        raise RuntimeError("API unavailable")
    with pytest.raises(RuntimeError, match="API unavailable"):
        run(transcriber)
    assert len(calls) == 3


def test_empty_response_requires_confirmed_silence(monkeypatch, extraction):
    monkeypatch.setattr("app.services.reliable_transcription_service.detect_silences",
                        lambda *a, **kw: [(0, 180)])
    text, segments = run(lambda *a, **kw: ("", []))
    assert text == "" and segments[0]["silence"] is True
    assert extraction == []


def test_empty_speech_cannot_be_accepted(monkeypatch, extraction):
    monkeypatch.setattr("app.services.reliable_transcription_service.detect_silences", lambda *a, **kw: [])
    with pytest.raises(IncompleteTranscription):
        run(lambda *a, **kw: ("", []))


def test_sparse_response_is_checked_in_smaller_intervals(extraction):
    calls = []
    def transcriber(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            return "Gracias.", []
        return "discurso recuperado " * 30, []
    text, _ = run(transcriber)
    assert len(calls) == 3 and "recuperado" in text


def test_cleaning_is_lossless_multilingual_and_never_calls_ai(monkeypatch):
    def forbidden():
        raise AssertionError("Cleaning must not call API")
    monkeypatch.setattr("app.services.openai_service._client", forbidden)
    text = "Bon dia.\n\n" + "Importe 25,8 %. Nombre propio. " * 4000 + "\nMoltes gràcies."
    cleaned = clean_transcript(text, "es")
    assert cleaned == text


@pytest.mark.parametrize("segments", [[], [{"start": 0, "end": 10, "text": "Solo el inicio"}]])
def test_timestamp_export_does_not_drop_words_missing_from_segments(segments):
    project = SimpleNamespace(
        output=SimpleNamespace(reviewed_transcript=None, full_transcript="Solo el inicio y todo el final"),
        chunks=[SimpleNamespace(start_seconds=0, end_seconds=180,
                                transcript_text="Solo el inicio y todo el final",
                                segments_json=json.dumps(segments))])
    assert "Solo el inicio y todo el final" in get_transcript_content(project, True)


def test_api_usage_guard_is_applied_before_return(app, tmp_path, monkeypatch):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return {"text": "Respuesta corta", "usage": {"output_tokens": 2048}}
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    monkeypatch.setattr("app.services.openai_service._client", lambda **_kwargs: client)
    with pytest.raises(IncompleteTranscription):
        transcribe_audio(str(audio), "auto")
    assert len(calls) == 1  # content failure is subdivided, not retried unchanged
    assert "language" not in calls[0]


def test_coverage_rejects_missing_middle_or_final_audio():
    def span(a, b):
        return SimpleNamespace(start_seconds=a, end_seconds=b)
    with pytest.raises(RuntimeError, match="sin cubrir"):
        _check_audio_coverage([span(0, 180), span(190, 350)], 350)
    with pytest.raises(RuntimeError, match="final"):
        _check_audio_coverage([span(0, 180)], 350)
    _check_audio_coverage([span(0, 180), span(175, 350)], 350.4)


def test_recovered_source_invalidates_old_dossier_without_deleting_review():
    output = ProjectOutput(full_transcript="recortado", cleaned_transcript="recortado",
                           block_summary="viejo", final_dossier_markdown="viejo",
                           reviewed_transcript="revision humana")
    _store_full_transcript(output, "texto recuperado")
    assert output.full_transcript == "texto recuperado"
    assert output.cleaned_transcript is None
    assert output.block_summary is None and output.final_dossier_markdown is None
    assert output.reviewed_transcript == "revision humana"


def test_prompt_cost_stays_bounded_for_large_glossary(app):
    project = Project(title="Evento", language="es", glossary_json=json.dumps({"terms": ["terminologia"] * 10000}))
    prompt = _build_transcription_prompt(project, "contexto " * 10000)
    assert len(tokenizer().encode(prompt)) <= 384
    assert "sin resumir ni traducir" in prompt


def test_worker_never_completes_partial_audio_and_reuses_checked_chunks(app, tmp_path, monkeypatch, extraction):
    source = tmp_path / "source.mp3"
    source.write_bytes(b"audio")
    template = Template(name="test", prompt_instructions="test")
    project = Project(title="test", language="es", source_filename=source.name,
                      source_file_path=str(source), status="queued", template=template)
    checked = [{"start": 0, "end": 180, "text": "inicio verificado", "transcription_checked": 1}]
    project.chunks.extend([
        TranscriptChunk(chunk_index=1, start_seconds=0, end_seconds=180,
                        audio_path="first.mp3", status="completed", transcript_text="inicio verificado",
                        segments_json=json.dumps(checked)),
        TranscriptChunk(chunk_index=2, start_seconds=175, end_seconds=355,
                        audio_path="second.mp3", status="pending"),
    ])
    db.session.add(project)
    db.session.commit()
    monkeypatch.setattr("app.tasks.ensure_media_tools_available", lambda: None)
    monkeypatch.setattr("app.tasks.prepare_audio", lambda *a: None)
    monkeypatch.setattr("app.tasks.get_media_duration", lambda p: 355 if Path(p).name in {"source.mp3", "audio.mp3"} else 180)
    calls = []
    def broken(path, *a, **kw):
        calls.append(path)
        return " palabra" * 2047, []
    monkeypatch.setattr("app.tasks.transcribe_audio", broken)
    def forbidden(*a, **kw):
        raise AssertionError("No summary should be generated for partial transcription")
    monkeypatch.setattr("app.tasks.summarize_chunk", forbidden)
    with pytest.raises(RuntimeError, match="sin aceptar texto parcial"):
        process_project(project.id)
    db.session.refresh(project)
    assert project.status == "failed"
    assert project.chunks[0].status == "completed"
    assert project.chunks[1].status == "failed"
    assert project.chunks[1].transcript_text is None
    assert not any(Path(p).name == "first.mp3" for p in calls)
    assert len(calls) == 4
    assert project.output is None


@pytest.mark.skipif(not (os.environ.get("FFMPEG_TEST_BINARY") or shutil.which("ffmpeg")), reason="ffmpeg not installed")
def test_real_audio_splitting_and_recovery_preserve_final_fractional_second(tmp_path, monkeypatch):
    from app.services import media_service as media
    binary = os.environ.get("FFMPEG_TEST_BINARY") or shutil.which("ffmpeg")
    source = tmp_path / "source.wav"
    rate, duration = 16000, 181.75
    # The only signal is a tone in the last quarter-second. Flooring duration
    # to 181 seconds would remove it completely, which this test detects.
    samples = array("h", [0]) * int(rate * (duration - .25))
    samples.extend(int(12000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(rate // 4))
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(samples.tobytes())
    original_run = media._run
    def ffmpeg_run(command):
        return original_run([binary, *command[1:]])
    monkeypatch.setattr(media, "_run", ffmpeg_run)
    monkeypatch.setattr(media, "get_media_duration", lambda _: duration)
    monkeypatch.setattr(media, "detect_silences", lambda *a: [])
    chunks = media.split_audio(source, tmp_path / "chunks", chunk_minutes=20)
    assert [(c.start_seconds, c.end_seconds) for c in chunks] == [(0, 180), (175, 181.75)]
    retry = media.extract_audio_range(chunks[-1].path, tmp_path / "retry.mp3", 3, 6.75)
    decoded = subprocess.run([binary, "-v", "error", "-i", str(retry), "-f", "s16le", "-ac", "1", "-ar", str(rate), "-"],
                             check=True, capture_output=True).stdout
    pcm = array("h")
    pcm.frombytes(decoded)
    assert abs(len(pcm) / rate - 3.75) < .15
    assert max(abs(s) for s in pcm[-rate // 2:]) > 5000
