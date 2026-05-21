from __future__ import annotations

import subprocess
from pathlib import Path


class MediaProcessingError(RuntimeError):
    pass


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise MediaProcessingError("ffmpeg/ffprobe no está instalado o no está en PATH.") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise MediaProcessingError(detail) from exc


def get_media_duration(input_path: str | Path) -> int | None:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(input_path),
    ]
    result = _run(command)
    value = result.stdout.strip()
    if not value:
        return None
    return int(float(value))


def extract_audio(input_path: str | Path, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-b:a",
        "64k",
        str(output),
    ]
    _run(command)
    return output


def split_audio(
    input_audio_path: str | Path,
    output_dir: str | Path,
    chunk_minutes: int = 20,
) -> list[Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    chunk_seconds = chunk_minutes * 60
    pattern = output / "chunk_%03d.mp3"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_audio_path),
        "-f",
        "segment",
        "-segment_time",
        str(chunk_seconds),
        "-segment_start_number",
        "1",
        "-c",
        "copy",
        str(pattern),
    ]
    _run(command)
    chunks = sorted(output.glob("chunk_*.mp3"))
    if not chunks:
        raise MediaProcessingError("No se generó ningún fragmento de audio.")
    return chunks
