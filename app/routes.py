from __future__ import annotations

from pathlib import Path
import uuid

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
    allowed_file,
    get_project_progress,
    is_valid_language,
)
from app.extensions import db
from app.models import Project, ProjectLog, ProjectOutput, TranscriptChunk, Template
from app.queue import enqueue_project_processing
from app.services.export_service import get_transcript_content, markdown_to_docx
from app.storage import clear_generated_files, project_original_dir, project_root, project_outputs_dir

bp = Blueprint("main", __name__)


def add_project_log(project_id: int, message: str, level: str = "info") -> None:
    db.session.add(ProjectLog(project_id=project_id, level=level, message=message))


def get_estimated_time(project) -> str | None:
    if project.status in ("completed", "failed"):
        return None
    if not project.duration_seconds:
        return "~ calculando..."
    
    total_minutes = project.duration_seconds / 60.0
    # Whisper takes ~10-15% of audio length. Let's estimate 10%.
    # Plus a fixed 1-2 minutes for summary overhead.
    remaining_minutes = (total_minutes * 0.15) + 2
    
    # If chunks exist, calculate based on pending chunks (each 20min chunk takes ~2-3 min)
    if project.chunks:
        pending_chunks = sum(1 for c in project.chunks if c.status != "completed")
        remaining_minutes = pending_chunks * 3 + 1
        
    return f"~ {int(remaining_minutes)} min"


def _template_context() -> dict:
    templates = Template.query.all()
    return {
        "templates": templates,
        "language_choices": LANGUAGE_CHOICES,
        "status_labels": STATUS_LABELS,
        "get_project_progress": get_project_progress,
        "get_estimated_time": get_estimated_time,
    }


@bp.get("/")
def dashboard():
    q = request.args.get("q", "").strip()
    query = Project.query
    if q:
        search = f"%{q}%"
        query = query.outerjoin(ProjectOutput).filter(
            db.or_(
                Project.title.ilike(search),
                Project.client_name.ilike(search),
                Project.event_name.ilike(search),
                ProjectOutput.full_transcript.ilike(search),
                ProjectOutput.block_summary.ilike(search),
            )
        )
    projects = query.order_by(Project.created_at.desc()).limit(50).all()
    return render_template("dashboard.html", projects=projects, q=q, **_template_context())


@bp.get("/projects/new")
def new_project():
    return render_template("projects/new.html", **_template_context())


@bp.post("/projects")
def create_project():
    title = (request.form.get("title") or "").strip()
    client_name = (request.form.get("client_name") or "").strip() or None
    event_name = (request.form.get("event_name") or "").strip() or None
    language = request.form.get("language") or "auto"
    template_id_str = request.form.get("template_id")
    upload = request.files.get("source_file")

    if not title:
        flash("El título es obligatorio.", "error")
        return redirect(url_for("main.new_project"))
    if not is_valid_language(language):
        flash("Idioma no válido.", "error")
        return redirect(url_for("main.new_project"))
    if not template_id_str or not template_id_str.isdigit():
        flash("Plantilla no válida.", "error")
        return redirect(url_for("main.new_project"))
    
    template = Template.query.get(int(template_id_str))
    if not template:
        flash("La plantilla seleccionada no existe.", "error")
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
        template_id=template.id,
        status="uploaded",
        share_token=uuid.uuid4().hex,
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
    if not project.share_token:
        project.share_token = uuid.uuid4().hex
        db.session.commit()
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

    # Smart Retry: no borramos chunks ni output. tasks.py se encargará de saltar lo hecho.
    project.status = "queued"
    project.error_message = None
    add_project_log(project.id, "Proyecto reintentado (Smart Retry).")
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


@bp.get("/projects/<int:project_id>/download/transcript")
def download_transcript(project_id: int):
    project = Project.query.get_or_404(project_id)
    if not project.output:
        abort(404)

    fmt = request.args.get("format", "txt")
    timestamps = request.args.get("timestamps", "false").lower() == "true"

    content = get_transcript_content(project, include_timestamps=timestamps)
    if not content:
        abort(404)

    filename_base = f"transcripcion-{project.id}{'-tiempos' if timestamps else ''}"

    if fmt == "docx":
        outputs_dir = project_outputs_dir(project.id)
        docx_path = outputs_dir / f"{filename_base}.docx"
        markdown_to_docx(content, docx_path)
        return send_file(
            docx_path,
            as_attachment=True,
            download_name=f"{filename_base}.docx",
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    mimetype = "text/markdown" if fmt == "md" else "text/plain"
    ext = "md" if fmt == "md" else "txt"

    return Response(
        content,
        mimetype=f"{mimetype}; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename_base}.{ext}"},
    )


@bp.post("/projects/<int:project_id>/delete")
def delete_project(project_id: int):
    import shutil

    project = Project.query.get_or_404(project_id)
    root = project_root(project.id)
    db.session.delete(project)
    db.session.commit()
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    flash("Proyecto eliminado.", "success")
    return redirect(url_for("main.dashboard"))


@bp.get("/p/<token>")
def shared_dossier(token: str):
    project = Project.query.filter_by(share_token=token).first_or_404()
    return render_template("projects/shared.html", project=project)

# TEMPLATES CRUD
# ==============================================================================

@bp.get("/templates")
def templates_list():
    templates = Template.query.order_by(Template.name).all()
    return render_template("templates/index.html", templates=templates)


@bp.get("/templates/new")
def new_template():
    return render_template("templates/form.html", template=None)


@bp.post("/templates")
def create_template():
    name = (request.form.get("name") or "").strip()
    prompt_instructions = (request.form.get("prompt_instructions") or "").strip()

    if not name or not prompt_instructions:
        flash("El nombre y las instrucciones son obligatorios.", "error")
        return redirect(url_for("main.new_template"))

    template = Template(name=name, prompt_instructions=prompt_instructions)
    db.session.add(template)
    db.session.commit()
    flash("Plantilla creada correctamente.", "success")
    return redirect(url_for("main.templates_list"))


@bp.get("/templates/<int:template_id>/edit")
def edit_template(template_id: int):
    template = Template.query.get_or_404(template_id)
    return render_template("templates/form.html", template=template)


@bp.post("/templates/<int:template_id>")
def update_template(template_id: int):
    template = Template.query.get_or_404(template_id)
    name = (request.form.get("name") or "").strip()
    prompt_instructions = (request.form.get("prompt_instructions") or "").strip()

    if not name or not prompt_instructions:
        flash("El nombre y las instrucciones son obligatorios.", "error")
        return redirect(url_for("main.edit_template", template_id=template.id))

    template.name = name
    template.prompt_instructions = prompt_instructions
    db.session.commit()
    flash("Plantilla actualizada correctamente.", "success")
    return redirect(url_for("main.templates_list"))


@bp.post("/templates/<int:template_id>/delete")
def delete_template(template_id: int):
    template = Template.query.get_or_404(template_id)
    if template.projects:
        flash("No se puede eliminar la plantilla porque está en uso por algunos proyectos.", "error")
        return redirect(url_for("main.templates_list"))

    db.session.delete(template)
    db.session.commit()
    flash("Plantilla eliminada correctamente.", "success")
    return redirect(url_for("main.templates_list"))
