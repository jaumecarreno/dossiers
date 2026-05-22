from __future__ import annotations

import re
from pathlib import Path

import json
from docx import Document


def format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02}:{m:02}:{s:02}"
    return f"{m:02}:{s:02}"


def get_transcript_content(project, include_timestamps: bool = False) -> str:
    if not include_timestamps or not project.chunks:
        if project.output and project.output.full_transcript:
            return project.output.full_transcript
        return ""
        
    lines = []
    for chunk in project.chunks:
        offset = chunk.start_seconds or 0
        
        if not chunk.segments_json:
            if include_timestamps:
                lines.append(f"[{format_time(offset)}]\n{chunk.transcript_text or ''}")
            else:
                lines.append(chunk.transcript_text or "")
            continue
            
        try:
            segments = json.loads(chunk.segments_json)
            
            current_paragraph_start = None
            current_paragraph_text = []
            
            for seg in segments:
                start = offset + seg.get("start", 0)
                text = seg.get("text", "").strip()
                if not text:
                    continue
                    
                if current_paragraph_start is None:
                    current_paragraph_start = start
                    
                current_paragraph_text.append(text)
                
                duration = start - current_paragraph_start
                if (duration > 30.0 and text[-1] in ".!?。") or duration > 60.0:
                    lines.append(f"[{format_time(current_paragraph_start)}] {' '.join(current_paragraph_text)}")
                    current_paragraph_start = None
                    current_paragraph_text = []
                    
            if current_paragraph_text:
                lines.append(f"[{format_time(current_paragraph_start)}] {' '.join(current_paragraph_text)}")
                
        except Exception:
            lines.append(chunk.transcript_text or "")
            
    return "\n\n".join(lines).strip()


def _strip_markdown_inline(text: str) -> str:
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"`(.*?)`", r"\1", text)
    return text.strip()


def markdown_to_docx(markdown_text: str, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    document = Document()
    title_added = False

    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#"):
            level = min(len(line) - len(line.lstrip("#")), 4)
            text = _strip_markdown_inline(line[level:].strip())
            if not title_added and level == 1:
                document.add_heading(text, level=0)
                title_added = True
            else:
                document.add_heading(text, level=level)
            continue

        if line.startswith(("- ", "* ")):
            document.add_paragraph(_strip_markdown_inline(line[2:]), style="List Bullet")
            continue

        numbered = re.match(r"^\d+\.\s+(.*)$", line)
        if numbered:
            document.add_paragraph(
                _strip_markdown_inline(numbered.group(1)), style="List Number"
            )
            continue

        document.add_paragraph(_strip_markdown_inline(line))

    if not document.paragraphs:
        document.add_paragraph("")
    elif not title_added:
        document.paragraphs[0].style = document.styles["Title"]

    document.save(output)
    return output
