from __future__ import annotations

SOURCE_KIND_MEDIA = "media"
SOURCE_KIND_YOUTUBE = "youtube"
SOURCE_KIND_TRANSCRIPT_FILES = "transcript_files"
SOURCE_KINDS = {
    SOURCE_KIND_MEDIA,
    SOURCE_KIND_YOUTUBE,
    SOURCE_KIND_TRANSCRIPT_FILES,
}

ALLOWED_EXTENSIONS = {"mp4", "mov", "m4v", "mp3", "wav", "m4a", "webm"}
ALLOWED_TRANSCRIPT_EXTENSIONS = {"txt", "md", "docx", "srt", "vtt"}
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
LANGUAGE_CHOICES = {
    "ca": "Catalán",
    "en": "Inglés",
    "es": "Español",
    "auto": "Detectar automáticamente",
}
TRANSCRIPTION_MODEL_CHOICES = {
    "gpt-4o-transcribe": {
        "label": "Alta calidad",
        "description": "Mejor precisión para reuniones, ponencias y nombres propios.",
    },
    "gpt-4o-mini-transcribe": {
        "label": "Equilibrado",
        "description": "Buena calidad con menor coste.",
    },
    "whisper-1": {
        "label": "Económico / Whisper",
        "description": "Modelo clásico con coste por minuto y marcas de tiempo compatibles.",
    },
}
TRANSCRIPTION_MODEL_COSTS_USD_PER_MINUTE = {
    "gpt-4o-transcribe": 0.006,
    "gpt-4o-mini-transcribe": 0.003,
    "whisper-1": 0.006,
}

PROJECT_STATUSES = [
    "uploaded",
    "queued",
    "importing_transcript",
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
    "importing_transcript": "Importando transcripciones",
    "extracting_audio": "Extrayendo audio",
    "splitting_audio": "Dividiendo",
    "transcribing": "Transcribiendo",
    "cleaning_transcript": "Limpiando texto",
    "summarizing": "Resumiendo",
    "generating_dossier": "Generando dossier",
    "completed": "Completado",
    "failed": "Error",
}

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_transcript_file(filename: str) -> bool:
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_TRANSCRIPT_EXTENSIONS
    )


def is_valid_language(lang: str) -> bool:
    return lang in LANGUAGE_CHOICES


def is_valid_transcription_model(model: str) -> bool:
    return model in TRANSCRIPTION_MODEL_CHOICES


def get_transcription_model_label(model: str | None) -> str:
    if model in TRANSCRIPTION_MODEL_CHOICES:
        return TRANSCRIPTION_MODEL_CHOICES[model]["label"]
    return TRANSCRIPTION_MODEL_CHOICES[DEFAULT_TRANSCRIPTION_MODEL]["label"]


def estimate_transcription_cost_usd(duration_seconds: float | int | None, model: str | None) -> float | None:
    if not duration_seconds or duration_seconds <= 0:
        return None
    rate = TRANSCRIPTION_MODEL_COSTS_USD_PER_MINUTE.get(model or DEFAULT_TRANSCRIPTION_MODEL)
    if rate is None:
        return None
    return (float(duration_seconds) / 60.0) * rate


def get_project_progress(status: str) -> int:
    if status == "failed":
        return 100
    if status not in PROJECT_STATUSES:
        return 0
    index = PROJECT_STATUSES.index(status)
    completed_index = PROJECT_STATUSES.index("completed")
    return min(100, int((index / completed_index) * 100))
