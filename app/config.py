from __future__ import annotations

import os
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    return int(value)


def _clean_database_url(value: str | None) -> str:
    database_url = (
        value or "postgresql+psycopg://dossiers:dossiers@localhost:5432/dossiers"
    )
    database_url = database_url.strip().strip("\"'`")
    if database_url.startswith("DATABASE_URL="):
        database_url = database_url.split("=", 1)[1].strip().strip("\"'`")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = _clean_database_url(os.getenv("DATABASE_URL"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    RQ_QUEUE_NAME = os.getenv("RQ_QUEUE_NAME", "default")
    RQ_JOB_TIMEOUT_SECONDS = _int_env("RQ_JOB_TIMEOUT_SECONDS", 60 * 60 * 6)

    STORAGE_ROOT = os.getenv("STORAGE_ROOT", str(Path.cwd() / "storage"))
    MAX_UPLOAD_MB = _int_env("MAX_UPLOAD_MB", 10000)
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024
    MEDIA_UPLOAD_CHUNK_MB = _int_env("MEDIA_UPLOAD_CHUNK_MB", 8)

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_TRANSCRIPTION_MODEL = os.getenv(
        "OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-transcribe"
    )
    OPENAI_SUMMARY_MODEL = os.getenv("OPENAI_SUMMARY_MODEL", "gpt-5.4-mini")

    TRANSCRIPT_CHUNK_MINUTES = _int_env("TRANSCRIPT_CHUNK_MINUTES", 20)
