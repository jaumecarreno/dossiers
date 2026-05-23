from __future__ import annotations

import json
import re
from pathlib import Path

from docx import Document

from app.constants import allowed_transcript_file


_TIMESTAMP_RE = re.compile(
    r"^(?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{1,3}\s+-->\s+"
    r"(?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{1,3}"
)


def extract_transcript_text(file_path: str | Path) -> str:
    path = Path(file_path)
    if not allowed_transcript_file(path.name):
        raise ValueError(f"Formato de transcripcion no permitido: {path.name}")

    suffix = path.suffix.lower().lstrip(".")
    if suffix in {"txt", "md"}:
        text = path.read_text(encoding="utf-8-sig")
    elif suffix == "docx":
        text = _extract_docx_text(path)
    elif suffix in {"srt", "vtt"}:
        text = _extract_caption_text(path)
    else:  # pragma: no cover - guarded by allowed_transcript_file
        raise ValueError(f"Formato de transcripcion no permitido: {path.name}")

    text = _normalize_text(text)
    if not text:
        raise ValueError(f"La transcripcion esta vacia: {path.name}")
    return text


def load_manifest(manifest_path: str | Path) -> dict:
    path = Path(manifest_path)
    return json.loads(path.read_text(encoding="utf-8"))


def load_transcript_texts_from_manifest(manifest_path: str | Path) -> list[tuple[str, str]]:
    manifest = load_manifest(manifest_path)
    files = manifest.get("files", [])
    texts: list[tuple[str, str]] = []
    for item in sorted(files, key=lambda entry: entry.get("order", 0)):
        original_filename = (
            item.get("original_filename")
            or item.get("stored_filename")
            or "transcripcion"
        )
        file_path = item.get("path")
        if not file_path:
            raise ValueError(f"Falta la ruta de {original_filename} en el manifest.")
        texts.append((original_filename, extract_transcript_text(file_path)))
    if not texts:
        raise ValueError("No hay archivos de transcripcion en el manifest.")
    return texts


def _extract_docx_text(path: Path) -> str:
    document = Document(path)
    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs]
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph)


def _extract_caption_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    lines: list[str] = []
    skipping_note = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            skipping_note = False
            continue
        upper = line.upper()
        if skipping_note:
            continue
        if upper.startswith(("WEBVTT", "STYLE", "REGION")):
            continue
        if upper.startswith("NOTE"):
            skipping_note = True
            continue
        if line.isdigit() or _TIMESTAMP_RE.match(line):
            continue
        lines.append(line)

    return "\n".join(lines)


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()
