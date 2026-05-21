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
    "splitting_audio": "Dividiendo audio",
    "transcribing": "Transcribiendo",
    "cleaning_transcript": "Limpiando transcripción",
    "summarizing": "Resumiendo",
    "generating_dossier": "Generando dossier",
    "completed": "Completado",
    "failed": "Fallido",
}

TEMPLATE_TYPES = {
    "resumen_ejecutivo": {
        "label": "Resumen ejecutivo",
        "sections": [
            "Título",
            "Contexto de la ponencia",
            "Ideas principales",
            "Conclusiones",
            "Frases destacadas",
            "Posibles acciones posteriores",
        ],
    },
    "dossier_patrocinadores": {
        "label": "Dossier para patrocinadores",
        "sections": [
            "Título del evento",
            "Descripción breve de la sesión",
            "Valor aportado por la ponencia",
            "Temas tratados",
            "Mensajes clave",
            "Impacto para asistentes/público",
            "Frases aprovechables en comunicación",
            "Conclusiones para patrocinadores",
            "Recomendaciones de uso comunicativo",
        ],
    },
    "contenido_comunicacion": {
        "label": "Contenido para comunicación",
        "sections": [
            "Resumen web",
            "Nota breve para newsletter",
            "5 publicaciones para LinkedIn",
            "5 titulares posibles",
            "10 frases destacadas",
            "Ideas para carrusel de redes",
        ],
    },
}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def is_valid_template(template_type: str) -> bool:
    return template_type in TEMPLATE_TYPES


def is_valid_language(language: str) -> bool:
    return language in LANGUAGE_CHOICES


def get_project_progress(status: str) -> int:
    if status == "failed":
        return 100
    if status not in PROJECT_STATUSES:
        return 0
    index = PROJECT_STATUSES.index(status)
    completed_index = PROJECT_STATUSES.index("completed")
    return min(100, int((index / completed_index) * 100))
