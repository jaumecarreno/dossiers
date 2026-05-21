from __future__ import annotations

import shutil
from pathlib import Path

from flask import current_app


def storage_root() -> Path:
    return Path(current_app.config["STORAGE_ROOT"]).resolve()


def project_root(project_id: int) -> Path:
    return storage_root() / "projects" / str(project_id)


def project_dir(project_id: int, name: str) -> Path:
    path = project_root(project_id) / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_original_dir(project_id: int) -> Path:
    return project_dir(project_id, "original")


def project_audio_dir(project_id: int) -> Path:
    return project_dir(project_id, "audio")


def project_chunks_dir(project_id: int) -> Path:
    return project_dir(project_id, "chunks")


def project_outputs_dir(project_id: int) -> Path:
    return project_dir(project_id, "outputs")


def clear_generated_files(project_id: int) -> None:
    for name in ("audio", "chunks", "outputs"):
        path = project_root(project_id) / name
        if path.exists():
            shutil.rmtree(path)
