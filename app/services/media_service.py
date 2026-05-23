from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class MediaProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioChunk:
    path: Path
    start_seconds: float
    end_seconds: float


SILENCE_DB = "-35dB"
MIN_SILENCE_SECONDS = 0.7
TARGET_CHUNK_SECONDS = 20 * 60
MIN_CHUNK_SECONDS = 16 * 60
MAX_CHUNK_SECONDS = 22 * 60
CHUNK_OVERLAP_SECONDS = 15
MAX_CHUNK_BYTES = 23 * 1024 * 1024
TARGET_AUDIO_BITRATE_BPS = 64_000
SPEECH_FILTERS = ",".join(
    [
        "highpass=f=80",
        "lowpass=f=8000",
        "afftdn=nf=-20",
        "loudnorm=I=-16:TP=-1.5:LRA=11",
    ]
)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        tool = command[0] if command else "ffmpeg/ffprobe"
        raise MediaProcessingError(
            f"{tool} no esta instalado o no esta en PATH. "
            "Ejecuta la app con Docker o instala ffmpeg y ffprobe en el servidor."
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise MediaProcessingError(detail) from exc


def ensure_media_tools_available() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    if missing:
        raise MediaProcessingError(
            "Faltan herramientas de audio: "
            f"{', '.join(missing)}. Ejecuta la app con Docker o instalalas en el servidor."
        )


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


def prepare_audio(input_path: str | Path, output_path: str | Path) -> Path:
    """Create a normalized mp3 ready for chunking and transcription in one pass."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-af",
        SPEECH_FILTERS,
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


def normalize_audio(input_path: str | Path, output_path: str | Path) -> Path:
    """Normalize volume and reduce background noise for better transcription."""
    return prepare_audio(input_path, output_path)


def detect_silences(
    input_audio_path: str | Path,
    silence_db: str = SILENCE_DB,
    min_silence_seconds: float = MIN_SILENCE_SECONDS,
) -> list[tuple[float, float]]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(input_audio_path),
        "-af",
        f"silencedetect=noise={silence_db}:d={min_silence_seconds}",
        "-f",
        "null",
        "-",
    ]
    result = _run(command)
    return parse_silencedetect_output(result.stderr)


def parse_silencedetect_output(output: str) -> list[tuple[float, float]]:
    silences: list[tuple[float, float]] = []
    current_start: float | None = None
    for line in output.splitlines():
        start_match = re.search(r"silence_start:\s*([0-9.]+)", line)
        if start_match:
            current_start = float(start_match.group(1))
            continue

        end_match = re.search(r"silence_end:\s*([0-9.]+)", line)
        if end_match and current_start is not None:
            silence_end = float(end_match.group(1))
            if silence_end > current_start:
                silences.append((current_start, silence_end))
            current_start = None
    return silences


def plan_audio_chunks(
    duration_seconds: float,
    silences: list[tuple[float, float]],
    target_seconds: int = TARGET_CHUNK_SECONDS,
    min_seconds: int = MIN_CHUNK_SECONDS,
    max_seconds: int = MAX_CHUNK_SECONDS,
    overlap_seconds: int = CHUNK_OVERLAP_SECONDS,
    max_chunk_bytes: int = MAX_CHUNK_BYTES,
    audio_bitrate_bps: int = TARGET_AUDIO_BITRATE_BPS,
) -> list[tuple[float, float]]:
    if duration_seconds <= 0:
        return []

    size_limited_seconds = int((max_chunk_bytes * 8) / audio_bitrate_bps)
    hard_max_seconds = max(60, min(max_seconds, size_limited_seconds))
    plans: list[tuple[float, float]] = []
    start = 0.0

    while start < duration_seconds:
        remaining = duration_seconds - start
        if remaining <= hard_max_seconds:
            plans.append((start, duration_seconds))
            break

        min_cut = min(duration_seconds, start + min_seconds)
        target_cut = min(duration_seconds, start + target_seconds)
        max_cut = min(duration_seconds, start + hard_max_seconds)
        candidates = [
            silence_end
            for _, silence_end in silences
            if min_cut <= silence_end <= max_cut
        ]
        cut = min(candidates, key=lambda value: abs(value - target_cut)) if candidates else max_cut
        plans.append((start, cut))

        next_start = max(0.0, cut - overlap_seconds)
        if next_start <= start:
            next_start = cut
        start = next_start

    return plans


def split_audio(
    input_audio_path: str | Path,
    output_dir: str | Path,
    chunk_minutes: int = 20,
    silence_db: str = SILENCE_DB,
    min_silence_seconds: float = MIN_SILENCE_SECONDS,
) -> list[AudioChunk]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    duration_seconds = get_media_duration(input_audio_path)
    if not duration_seconds:
        raise MediaProcessingError("No se pudo detectar la duracion del audio.")

    try:
        silences = detect_silences(input_audio_path, silence_db, min_silence_seconds)
    except MediaProcessingError:
        silences = []

    plans = plan_audio_chunks(
        duration_seconds,
        silences,
        target_seconds=chunk_minutes * 60,
    )
    chunks: list[AudioChunk] = []
    for index, (start, end) in enumerate(plans, start=1):
        chunk_path = output / f"chunk_{index:03d}.mp3"
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(input_audio_path),
            "-vn",
            "-acodec",
            "libmp3lame",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-b:a",
            "64k",
            str(chunk_path),
        ]
        _run(command)
        if chunk_path.stat().st_size > MAX_CHUNK_BYTES:
            raise MediaProcessingError(
                f"El fragmento {chunk_path.name} supera el limite seguro de 23 MB."
            )
        chunks.append(AudioChunk(chunk_path, start, end))

    if not chunks:
        raise MediaProcessingError("No se genero ningun fragmento de audio.")
    return chunks


def is_youtube_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower()
    return any(domain in host for domain in ("youtube.com", "youtu.be"))


def download_youtube_media(url: str, output_dir: str | Path) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    template = output / "youtube_source.%(ext)s"
    command = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "-f",
        "bestaudio/best",
        "--extract-audio",
        "--audio-format",
        "mp3",
        "-o",
        str(template),
        url.strip(),
    ]
    _run(command)
    files = sorted(output.glob("youtube_source.*"))
    if not files:
        raise MediaProcessingError("No se pudo descargar el contenido de YouTube.")
    return files[0]
