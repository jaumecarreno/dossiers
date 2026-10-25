from __future__ import annotations

import math
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.media_service import detect_silences, extract_audio_range
from app.services.transcription_guard import EmptyTranscription, IncompleteTranscription, validate_transcription
from app.services.transcript_join_service import dedupe_overlap


def transcribe_complete(file_path, language, *, model, prompt, duration_seconds,
                        transcriber, on_retry=None):
    """Accept only checked leaves; retry just suspect audio, at most three levels.

    Timings on synthetic segments describe the submitted audio interval, not word
    alignment. Markers let the worker reuse checked chunks on an explicit retry.
    """
    duration = float(duration_seconds or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise IncompleteTranscription("No se pudo comprobar la duracion del fragmento.")

    def visit(path, length, offset, depth, folder):
        reason = "Fragmento antiguo demasiado largo para transcribir con seguridad."
        if length <= 181:
            try:
                text, segments = transcriber(str(path), language, model=model, prompt=prompt)
                validate_transcription(text, model)
                if length >= 60 and len(text.split()) < length * 0.35:
                    raise IncompleteTranscription("Muy poco texto para este intervalo; comprobando en partes menores.")
                # Only use model timings when they account for all returned words.
                timed_text = " ".join(s.get("text", "").strip() for s in segments)
                if segments and timed_text.split() == text.split():
                    checked = [dict(s, start=float(s.get("start", 0)) + offset,
                                    end=float(s.get("end", length)) + offset,
                                    transcription_checked=1) for s in segments]
                else:
                    checked = [dict(start=offset, end=offset + length, text=text,
                                    timing="audio_interval", transcription_checked=1)]
                return text, checked
            except IncompleteTranscription as exc:
                reason = str(exc)
                # Empty output is accepted only for locally verified silence. Quiet
                # speech/music are not assumed to be silence merely because ASR is empty.
                if isinstance(exc, EmptyTranscription):
                    silences = detect_silences(path, silence_db="-50dB")
                    if any(start <= 0.1 and end >= length - 0.2 for start, end in silences):
                        return "", [dict(start=offset, end=offset + length, text="",
                                         timing="audio_interval", silence=True,
                                         transcription_checked=1)]
                if depth >= 3 or length <= 30:
                    raise IncompleteTranscription(
                        f"Transcripcion incompleta ({offset:.1f}-{offset + length:.1f}s): "
                        f"{reason} Se detuvo para revision, sin aceptar texto parcial."
                    ) from exc
        if on_retry:
            on_retry(reason)
        # Five seconds shared between children. Only exact boundary text is removed.
        midpoint = length / 2
        overlap = min(2.5, length / 10)
        ranges = [(0, midpoint + overlap), (midpoint - overlap, length)]
        texts, all_segments = [], []
        for index, (start, end) in enumerate(ranges):
            child = Path(folder) / f"{offset:.3f}-{length:.3f}-{depth}-{index}.mp3"
            extract_audio_range(path, child, start, end)
            text, checked = visit(child, end - start, offset + start,
                                  depth + (length <= 181), folder)
            if texts and text:
                text = dedupe_overlap(texts[-1], text, max_words=40)
            if text:
                texts.append(text)
            all_segments.extend(checked)
        return "\n\n".join(texts), all_segments

    with TemporaryDirectory(prefix="dossiers-transcription-") as folder:
        return visit(Path(file_path), duration, 0, 0, folder)
