from __future__ import annotations

import os
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    return int(value)


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    _DATABASE_URL = os.getenv("DATABASE_URL") or (
        "postgresql+psycopg://dossiers:dossiers@localhost:5432/dossiers"
    )
    if _DATABASE_URL.startswith("postgres://"):
        _DATABASE_URL = _DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
    elif _DATABASE_URL.startswith("postgresql://"):
        _DATABASE_URL = _DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    SQLALCHEMY_DATABASE_URI = _DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    RQ_QUEUE_NAME = os.getenv("RQ_QUEUE_NAME", "default")
    RQ_JOB_TIMEOUT_SECONDS = _int_env("RQ_JOB_TIMEOUT_SECONDS", 60 * 60 * 6)

    STORAGE_ROOT = os.getenv("STORAGE_ROOT", str(Path.cwd() / "storage"))
    MAX_UPLOAD_MB = _int_env("MAX_UPLOAD_MB", 2000)
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_TRANSCRIPTION_MODEL = os.getenv(
        "OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"
    )
    OPENAI_SUMMARY_MODEL = os.getenv("OPENAI_SUMMARY_MODEL", "gpt-5.4-mini")

    TRANSCRIPT_CHUNK_MINUTES = _int_env("TRANSCRIPT_CHUNK_MINUTES", 20)
