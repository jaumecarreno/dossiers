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

GROUNDING_VERDICTS = {"aprobado", "revisar", "critico"}
GROUNDING_PROBLEMS = {
    "no_en_fuente",
    "contradice_fuente",
    "inferencia_no_marcada",
    "demasiado_especifico",
}


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


def normalize_grounding_review(raw_value: str | None) -> dict[str, Any]:
    raw_text = (raw_value or "").strip()
    data = json_loads_object(raw_text)
    if not data:
        data = json_loads_object(_extract_json_object(raw_text))

    if not data:
        return {
            "version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "verdict": "revisar",
            "summary": "La verificacion se ejecuto, pero la respuesta no se pudo interpretar como JSON.",
            "supported_count": 0,
            "unsupported_count": 0,
            "issues": [],
            "raw_response": _trim_text(raw_text, 4000),
        }

    issues = _normalize_grounding_issues(data.get("issues"))
    verdict = str(data.get("verdict") or "").strip().casefold()
    if verdict not in GROUNDING_VERDICTS:
        verdict = "revisar" if issues else "aprobado"

    unsupported_count = max(_safe_int(data.get("unsupported_count"), len(issues)), len(issues))
    supported_count = _safe_int(data.get("supported_count"), 0)
    if verdict == "aprobado" and unsupported_count:
        verdict = "revisar"
    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "summary": _trim_text(str(data.get("summary") or "").strip(), 1200),
        "supported_count": supported_count,
        "unsupported_count": unsupported_count,
        "issues": issues,
    }


def build_linkedin_posts_payload(raw_value: str | None) -> dict[str, Any]:
    raw_text = (raw_value or "").strip()
    data: Any = None
    if raw_text:
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            data = json_loads_object(_extract_json_object(raw_text))

    if isinstance(data, dict):
        raw_posts = data.get("posts")
    elif isinstance(data, list):
        raw_posts = data
    else:
        raw_posts = []

    posts: list[dict[str, str]] = []
    if isinstance(raw_posts, list):
        for item in raw_posts:
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
            else:
                text = str(item or "").strip()
            if text:
                posts.append({"text": _trim_text(text, 3000)})

    if len(posts) != 5:
        raise ValueError("La IA no devolvio exactamente 5 posts de LinkedIn.")

    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "posts": posts,
    }


def build_quality_report_with_grounding_review(
    project,
    output,
    raw_review: str | None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = build_quality_report(project, output, metrics)
    review = normalize_grounding_review(raw_review)
    report["grounding_review"] = review
    if review["verdict"] != "aprobado":
        report["warnings"].append(
            "La verificacion factual encontro afirmaciones que conviene revisar."
        )
    return report


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


def _extract_json_object(text: str) -> str:
    if not text:
        return ""
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fence_match:
        return fence_match.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ""
    return text[start : end + 1]


def _normalize_grounding_issues(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    issues: list[dict[str, str]] = []
    for item in value[:15]:
        if not isinstance(item, dict):
            continue
        problem = str(item.get("problem") or "").strip().casefold()
        if problem not in GROUNDING_PROBLEMS:
            problem = "no_en_fuente"
        issues.append(
            {
                "claim": _trim_text(str(item.get("claim") or "").strip(), 600),
                "problem": problem,
                "evidence": _trim_text(
                    str(item.get("evidence") or "Sin evidencia localizada").strip(),
                    800,
                ),
                "recommendation": _trim_text(
                    str(item.get("recommendation") or "").strip(),
                    800,
                ),
            }
        )
    return issues


def _safe_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


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
