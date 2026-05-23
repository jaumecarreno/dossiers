from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Template(TimestampMixin, db.Model):
    __tablename__ = "templates"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    prompt_instructions = db.Column(db.Text, nullable=False)
    is_default = db.Column(db.Boolean, default=False, nullable=False)

    projects = db.relationship("Project", back_populates="template")


class Project(TimestampMixin, db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    client_name = db.Column(db.String(255), nullable=True)
    event_name = db.Column(db.String(255), nullable=True)
    source_filename = db.Column(db.String(512), nullable=False)
    source_file_path = db.Column(db.String(1024), nullable=False)
    duration_seconds = db.Column(db.Integer, nullable=True)
    language = db.Column(db.String(8), nullable=False, default="auto")
    transcription_model = db.Column(
        db.String(64), nullable=False, default="gpt-4o-transcribe"
    )
    template_id = db.Column(db.Integer, db.ForeignKey("templates.id"), nullable=True)
    status = db.Column(db.String(64), nullable=False, default="uploaded", index=True)
    share_token = db.Column(db.String(64), unique=True, index=True, nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    template = db.relationship("Template", back_populates="projects")
    chunks = db.relationship(
        "TranscriptChunk",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="TranscriptChunk.chunk_index",
    )
    output = db.relationship(
        "ProjectOutput",
        back_populates="project",
        cascade="all, delete-orphan",
        uselist=False,
    )
    logs = db.relationship(
        "ProjectLog",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectLog.created_at.desc()",
    )


class TranscriptChunk(TimestampMixin, db.Model):
    __tablename__ = "transcript_chunks"
    __table_args__ = (
        db.UniqueConstraint("project_id", "chunk_index", name="uq_chunk_project_index"),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    chunk_index = db.Column(db.Integer, nullable=False)
    audio_path = db.Column(db.String(1024), nullable=False)
    start_seconds = db.Column(db.Integer, nullable=True)
    end_seconds = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(64), nullable=False, default="pending")
    transcript_text = db.Column(db.Text, nullable=True)
    segments_json = db.Column(db.Text, nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    project = db.relationship("Project", back_populates="chunks")


class ProjectOutput(TimestampMixin, db.Model):
    __tablename__ = "project_outputs"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("projects.id"), nullable=False, unique=True
    )
    full_transcript = db.Column(db.Text, nullable=True)
    cleaned_transcript = db.Column(db.Text, nullable=True)
    block_summary = db.Column(db.Text, nullable=True)
    final_dossier_markdown = db.Column(db.Text, nullable=True)
    final_dossier_docx_path = db.Column(db.String(1024), nullable=True)

    project = db.relationship("Project", back_populates="output")


class ProjectLog(db.Model):
    __tablename__ = "project_logs"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    level = db.Column(db.String(32), nullable=False, default="info")
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    project = db.relationship("Project", back_populates="logs")
