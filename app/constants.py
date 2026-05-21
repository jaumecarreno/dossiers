from __future__ import annotations

ALLOWED_EXTENSIONS = {"mp4", "mov", "m4v", "mp3", "wav", "m4a", "webm"}
LANGUAGE_CHOICES = {
    "es": "Español",
    "ca": "Català",
    "en": "English",
}

PROJECT_STATUSES = [
    "uploaded",
    "queued",
    "extracting_audio",
    "splitting_audio",
    "transcribing",
    "cleaning_transcript",
    "summarizing",
    "generating_dossier",
    "completed",
    "failed",
]

STATUS_LABELS = {
    "uploaded": "Subido",
    "queued": "En cola",
    "extracting_audio": "Extrayendo audio",
    "splitting_audio": "Dividiendo",
    "transcribing": "Transcribiendo",
    "cleaning_transcript": "Limpiando texto",
    "summarizing": "Resumiendo",
    "generating_dossier": "Generando dossier",
    "completed": "Completado",
    "failed": "Error",
}

LANGUAGE_CHOICES = {
    "es": "Español",
    "en": "Inglés",
    "ca": "Catalán",
}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def is_valid_language(lang: str) -> bool:
    return lang in LANGUAGE_CHOICES


def get_project_progress(status: str) -> int:
    if status == "failed":
        return 100
    if status not in PROJECT_STATUSES:
        return 0
    index = PROJECT_STATUSES.index(status)
    completed_index = PROJECT_STATUSES.index("completed")
    return min(100, int((index / completed_index) * 100))
