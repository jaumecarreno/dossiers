from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.utils import secure_filename

from app.constants import (
    LANGUAGE_CHOICES,
    STATUS_LABELS,
    TEMPLATE_TYPES,
    allowed_file,
    get_project_progress,
    is_valid_language,
    is_valid_template,
)
from app.extensions import db
from app.models import Project, ProjectLog, ProjectOutput, TranscriptChunk
from app.queue import enqueue_project_processing
from app.storage import clear_generated_files, project_original_dir

bp = Blueprint("main", __name__)


def add_project_log(project_id: int, message: str, level: str = "info") -> None:
    db.session.add(ProjectLog(project_id=project_id, level=level, message=message))


def _template_context() -> dict:
    return {
        "template_types": TEMPLATE_TYPES,
        "language_choices": LANGUAGE_CHOICES,
        "status_labels": STATUS_LABELS,
        "get_project_progress": get_project_progress,
    }


@bp.get("/")
def dashboard():
    projects = Project.query.order_by(Project.created_at.desc()).limit(50).all()
    return render_template("dashboard.html", projects=projects, **_template_context())


@bp.get("/projects/new")
def new_project():
    return render_template("projects/new.html", **_template_context())


@bp.post("/projects")
def create_project():
    title = (request.form.get("title") or "").strip()
    client_name = (request.form.get("client_name") or "").strip() or None
    event_name = (request.form.get("event_name") or "").strip() or None
    language = request.form.get("language") or "es"
    template_type = request.form.get("template_type") or ""
    upload = request.files.get("source_file")

    if not title:
        flash("El título es obligatorio.", "error")
        return redirect(url_for("main.new_project"))
    if not is_valid_language(language):
        flash("Idioma no válido.", "error")
        return redirect(url_for("main.new_project"))
    if not is_valid_template(template_type):
        flash("Plantilla no válida.", "error")
        return redirect(url_for("main.new_project"))
    if not upload or not upload.filename:
        flash("Selecciona un archivo de vídeo o audio.", "error")
        return redirect(url_for("main.new_project"))
    if not allowed_file(upload.filename):
        flash("Tipo de archivo no permitido.", "error")
        return redirect(url_for("main.new_project"))

    filename = secure_filename(upload.filename)
    project = Project(
        title=title,
        client_name=client_name,
        event_name=event_name,
        source_filename=filename,
        source_file_path="",
        language=language,
        template_type=template_type,
        status="uploaded",
    )
    db.session.add(project)
    db.session.flush()

    original_dir = project_original_dir(project.id)
    source_path = original_dir / filename
    upload.save(source_path)

    project.source_file_path = str(source_path)
    project.status = "queued"
    add_project_log(project.id, "Archivo recibido y proyecto encolado.")
    db.session.commit()

    try:
        enqueue_project_processing(project.id)
    except Exception as exc:  # pragma: no cover - depends on Redis availability
        project.status = "failed"
        project.error_message = f"No se pudo encolar el proyecto: {exc}"
        add_project_log(project.id, project.error_message, level="error")
        db.session.commit()
        flash(project.error_message, "error")
    else:
        flash("Proyecto creado y encolado.", "success")

    return redirect(url_for("main.project_detail", project_id=project.id))


@bp.get("/projects/<int:project_id>")
def project_detail(project_id: int):
    project = Project.query.get_or_404(project_id)
    return render_template("projects/detail.html", project=project, **_template_context())


@bp.get("/projects/<int:project_id>/status")
def project_status(project_id: int):
    project = Project.query.get_or_404(project_id)
    return render_template("projects/_status_panel.html", project=project, **_template_context())


@bp.post("/projects/<int:project_id>/retry")
def retry_project(project_id: int):
    project = Project.query.get_or_404(project_id)
    if project.status != "failed":
        flash("Solo se pueden reintentar proyectos fallidos.", "error")
        return redirect(url_for("main.project_detail", project_id=project.id))

    TranscriptChunk.query.filter_by(project_id=project.id).delete()
    ProjectOutput.query.filter_by(project_id=project.id).delete()
    clear_generated_files(project.id)
    project.status = "queued"
    project.error_message = None
    add_project_log(project.id, "Proyecto reintentado y encolado de nuevo.")
    db.session.commit()

    try:
        enqueue_project_processing(project.id)
    except Exception as exc:  # pragma: no cover - depends on Redis availability
        project.status = "failed"
        project.error_message = f"No se pudo reencolar el proyecto: {exc}"
        add_project_log(project.id, project.error_message, level="error")
        db.session.commit()
        flash(project.error_message, "error")
    else:
        flash("Proyecto reencolado.", "success")

    return redirect(url_for("main.project_detail", project_id=project.id))


@bp.get("/projects/<int:project_id>/download/markdown")
def download_markdown(project_id: int):
    project = Project.query.get_or_404(project_id)
    if not project.output or not project.output.final_dossier_markdown:
        abort(404)

    filename = f"dossier-{project.id}.md"
    return Response(
        project.output.final_dossier_markdown,
        mimetype="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.get("/projects/<int:project_id>/download/docx")
def download_docx(project_id: int):
    project = Project.query.get_or_404(project_id)
    if not project.output or not project.output.final_dossier_docx_path:
        abort(404)

    path = Path(project.output.final_dossier_docx_path)
    if not path.exists():
        abort(404)

    return send_file(
        path,
        as_attachment=True,
        download_name=f"dossier-{project.id}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
