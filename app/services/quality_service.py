from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

from app.constants import DEFAULT_TRANSCRIPTION_MODEL, estimate_transcription_cost_usd


EXPECTED_DOSSIER_SECTIONS = [
    "Objetivos",
    "Resumen ejecutivo",
    "Ideas clave",
    "Citas destacadas",
    "Acciones",
    "Preguntas",
    "Conclusiones",
]


def json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def json_loads_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def parse_glossary_terms(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    terms: list[str] = []
    for item in re.split(r"[\n,;]", raw_value):
        term = item.strip()
        if term and term not in terms:
            terms.append(term)
    return terms


def glossary_terms_from_project(project) -> list[str]:
    data = json_loads_object(getattr(project, "glossary_json", None))
    terms = data.get("terms", [])
    if not isinstance(terms, list):
        return []
    return [str(term).strip() for term in terms if str(term).strip()]


def glossary_text_from_project(project) -> str:
    return "\n".join(glossary_terms_from_project(project))


def serialize_glossary_terms(terms: list[str]) -> str | None:
    cleaned = [term.strip() for term in terms if term.strip()]
    if not cleaned:
        return None
    return json_dumps({"version": 1, "terms": cleaned})


def active_transcript(output) -> str:
    if not output:
        return ""
    return (
        (getattr(output, "reviewed_transcript", None) or "").strip()
        or (getattr(output, "cleaned_transcript", None) or "").strip()
        or (getattr(output, "full_transcript", None) or "").strip()
    )


def build_output_variants(final_markdown: str | None, block_summary: str | None) -> dict[str, Any]:
    dossier = (final_markdown or "").strip()
    summary = (block_summary or "").strip()
    executive_summary = _extract_markdown_section(dossier, "Resumen ejecutivo")
    if not executive_summary:
        executive_summary = _trim_text(summary or dossier, 2400)

    return {
        "version": 1,
        "dossier_largo": dossier,
        "resumen_ejecutivo": executive_summary,
        "posts_redes": _build_social_posts(dossier or summary),
    }


def build_quality_report(project, output, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    metrics = metrics or {}
    transcript = active_transcript(output)
    final_markdown = (getattr(output, "final_dossier_markdown", None) or "").strip()
    block_summary = (getattr(output, "block_summary", None) or "").strip()
    failed_chunks = [chunk for chunk in getattr(project, "chunks", []) if chunk.status == "failed"]
    completed_chunks = [chunk for chunk in getattr(project, "chunks", []) if chunk.status == "completed"]
    segments = _segment_stats(getattr(project, "chunks", []))

    section_checks = [
        {"name": section, "present": _contains_heading(final_markdown, section)}
        for section in EXPECTED_DOSSIER_SECTIONS
    ]
    warnings = []
    if not transcript:
        warnings.append("Transcripcion vacia.")
    if failed_chunks:
        warnings.append(f"{len(failed_chunks)} fragmento(s) fallido(s).")
    if final_markdown and not all(item["present"] for item in section_checks):
        warnings.append("Faltan secciones esperadas del dossier profesional.")
    if getattr(project, "chunks", []) and segments["total"] == 0:
        warnings.append("No hay segmentos con marcas de tiempo.")

    model = (
        getattr(project, "transcription_model", None)
        or metrics.get("transcription_model")
        or DEFAULT_TRANSCRIPTION_MODEL
    )
    cost = estimate_transcription_cost_usd(getattr(project, "duration_seconds", None), model)

    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {
            **metrics,
            "duration_seconds": getattr(project, "duration_seconds", None),
            "source_kind": getattr(project, "source_kind", None),
            "transcription_model": model,
            "estimated_transcription_cost_usd": cost,
            "chunk_count": len(getattr(project, "chunks", [])),
            "completed_chunk_count": len(completed_chunks),
            "failed_chunk_count": len(failed_chunks),
        },
        "content": {
            "transcript_word_count": _word_count(transcript),
            "summary_word_count": _word_count(block_summary),
            "dossier_word_count": _word_count(final_markdown),
            "has_reviewed_transcript": bool(getattr(output, "reviewed_transcript", None)),
            "glossary_term_count": len(glossary_terms_from_project(project)),
        },
        "segments": segments,
        "checks": {
            "has_transcript": bool(transcript),
            "has_block_summary": bool(block_summary),
            "has_final_dossier": bool(final_markdown),
            "expected_sections": section_checks,
        },
        "warnings": warnings,
    }


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or "", flags=re.UNICODE))


def _contains_heading(markdown_text: str, heading: str) -> bool:
    needle = _normalize(heading)
    for line in markdown_text.splitlines():
        stripped = line.strip("#* -:\t ").strip()
        if needle in _normalize(stripped):
            return True
    return False


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _extract_markdown_section(markdown_text: str, heading: str) -> str:
    lines = markdown_text.splitlines()
    start: int | None = None
    heading_level = 0
    needle = _normalize(heading)
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("#"):
            continue
        level = len(line) - len(line.lstrip("#"))
        title = line.strip("# ").strip()
        if needle in _normalize(title):
            start = index + 1
            heading_level = level
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start, len(lines)):
        line = lines[index]
        if line.lstrip().startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            if level <= heading_level:
                end = index
                break
    return "\n".join(lines[start:end]).strip()


def _trim_text(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _build_social_posts(text: str) -> list[str]:
    source = re.sub(r"#+\s*", "", text or "")
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[.!?])\s+", source)
        if len(item.strip()) > 35
    ]
    if not sentences:
        sentences = [_trim_text(source, 220)] if source.strip() else []
    posts = []
    for index, sentence in enumerate(sentences[:3], start=1):
        posts.append(f"Post {index}: {_trim_text(sentence, 260)}")
    return posts


def _segment_stats(chunks) -> dict[str, Any]:
    total = 0
    speaker_tagged = 0
    for chunk in chunks:
        raw = getattr(chunk, "segments_json", None)
        if not raw:
            continue
        try:
            segments = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(segments, list):
            continue
        total += len(segments)
        speaker_tagged += sum(
            1
            for segment in segments
            if isinstance(segment, dict) and segment.get("speaker")
        )
    return {
        "total": total,
        "speaker_tagged": speaker_tagged,
        "speaker_support_ready": True,
    }
