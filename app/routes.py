from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import uuid

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from app.constants import (
    DEFAULT_TRANSCRIPTION_MODEL,
    LANGUAGE_CHOICES,
    STATUS_LABELS,
    TRANSCRIPTION_MODEL_CHOICES,
    TRANSCRIPTION_MODEL_COSTS_USD_PER_MINUTE,
    SOURCE_KIND_MEDIA,
    SOURCE_KIND_TRANSCRIPT_FILES,
    allowed_file,
    allowed_transcript_file,
    get_transcription_model_label,
    get_project_progress,
    is_valid_language,
    is_valid_transcription_model,
)
from app.extensions import db
from app.models import Project, ProjectLog, ProjectOutput, TranscriptChunk, Template
from app.queue import enqueue_project_processing
from app.services.export_service import get_transcript_content, markdown_to_docx, text_to_pdf
from app.services.openai_service import verify_dossier_against_transcript
from app.services.quality_service import (
    active_transcript,
    build_quality_report,
    build_quality_report_with_grounding_review,
    glossary_text_from_project,
    json_dumps,
    json_loads_object,
    parse_glossary_terms,
    serialize_glossary_terms,
)
from app.storage import (
    clear_generated_files,
    project_original_dir,
    project_root,
    project_outputs_dir,
    storage_root,
)
from app.tasks import regenerate_project_outputs

bp = Blueprint("main", __name__)


def add_project_log(project_id: int, message: str, level: str = "info") -> None:
    db.session.add(ProjectLog(project_id=project_id, level=level, message=message))


def _wants_json_response() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _form_error(message: str, status_code: int = 400):
    if _wants_json_response():
        return jsonify({"ok": False, "error": message}), status_code
    flash(message, "error")
    return redirect(url_for("main.new_project"))


def _create_project_success(project: Project, message: str):
    detail_url = url_for("main.project_detail", project_id=project.id)
    if _wants_json_response():
        return jsonify({"ok": True, "message": message, "redirect_url": detail_url}), 201
    flash(message, "success")
    return redirect(detail_url)


def _create_project_failure(project: Project, message: str, status_code: int = 500):
    detail_url = url_for("main.project_detail", project_id=project.id)
    if _wants_json_response():
        return (
            jsonify(
                {
                    "ok": False,
                    "error": message,
                    "redirect_url": detail_url,
                }
            ),
            status_code,
        )
    flash(message, "error")
    return redirect(detail_url)


def _json_error(message: str, status_code: int = 400):
    return jsonify({"ok": False, "error": message}), status_code


@bp.app_errorhandler(RequestEntityTooLarge)
def request_entity_too_large(_exc: RequestEntityTooLarge):
    max_upload_mb = current_app.config.get("MAX_UPLOAD_MB")
    message = f"El archivo supera el limite de subida configurado ({max_upload_mb} MB)."
    if _wants_json_response():
        return jsonify({"ok": False, "error": message}), 413
    flash(message, "error")
    return redirect(url_for("main.new_project"))


def get_estimated_time(project) -> str | None:
    if project.status in ("completed", "failed"):
        return None
    if project.source_kind == SOURCE_KIND_TRANSCRIPT_FILES:
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
    templates = Template.query.order_by(Template.is_default.desc(), Template.name).all()
    return {
        "templates": templates,
        "language_choices": LANGUAGE_CHOICES,
        "transcription_model_choices": TRANSCRIPTION_MODEL_CHOICES,
        "transcription_model_costs_usd_per_minute": TRANSCRIPTION_MODEL_COSTS_USD_PER_MINUTE,
        "status_labels": STATUS_LABELS,
        "get_transcription_model_label": get_transcription_model_label,
        "get_project_progress": get_project_progress,
        "get_estimated_time": get_estimated_time,
        "glossary_text_from_project": glossary_text_from_project,
        "get_transcript_content": get_transcript_content,
        "json_loads_object": json_loads_object,
        "max_upload_mb": current_app.config["MAX_UPLOAD_MB"],
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


def _source_filename_summary(filenames: list[str]) -> str:
    safe_names = [
        secure_filename(name) or f"transcripcion-{index}.txt"
        for index, name in enumerate(filenames, start=1)
    ]
    if len(safe_names) == 1:
        return safe_names[0]
    preview = ", ".join(safe_names[:3])
    if len(safe_names) > 3:
        preview += f" y {len(safe_names) - 3} mas"
    summary = f"{len(safe_names)} transcripciones: {preview}"
    if len(summary) > 512:
        return summary[:509] + "..."
    return summary


def _save_transcript_uploads(project_id: int, uploads) -> Path:
    original_dir = project_original_dir(project_id)
    manifest = {"version": 1, "files": []}
    for index, upload in enumerate(uploads, start=1):
        safe_name = secure_filename(upload.filename or "") or f"transcripcion-{index}.txt"
        stored_name = f"{index:02d}-{safe_name}"
        path = original_dir / stored_name
        upload.save(path)
        if path.stat().st_size == 0:
            raise ValueError(f"La transcripcion esta vacia: {upload.filename}")
        manifest["files"].append(
            {
                "order": index,
                "original_filename": upload.filename,
                "stored_filename": stored_name,
                "path": str(path),
                "size": path.stat().st_size,
            }
        )

    manifest_path = original_dir / "transcript_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_path


def _chunk_upload_root() -> Path:
    root = storage_root() / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _media_upload_chunk_bytes() -> int:
    chunk_mb = max(1, int(current_app.config.get("MEDIA_UPLOAD_CHUNK_MB", 8)))
    return chunk_mb * 1024 * 1024


def _chunk_upload_dir(upload_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", upload_id or ""):
        abort(404)
    return _chunk_upload_root() / upload_id


def _chunk_metadata_path(upload_id: str) -> Path:
    return _chunk_upload_dir(upload_id) / "metadata.json"


def _load_chunk_metadata(upload_id: str) -> dict:
    path = _chunk_metadata_path(upload_id)
    if not path.exists():
        abort(404)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        abort(404)
    return data if isinstance(data, dict) else {}


def _received_chunk_indexes(upload_id: str) -> list[int]:
    chunks_dir = _chunk_upload_dir(upload_id) / "chunks"
    indexes = []
    for path in chunks_dir.glob("*.part"):
        if path.stem.isdigit():
            indexes.append(int(path.stem))
    return sorted(indexes)


def _validate_project_metadata(form) -> tuple[dict, Template] | tuple[None, str]:
    title = (form.get("title") or "").strip()
    client_name = (form.get("client_name") or "").strip() or None
    event_name = (form.get("event_name") or "").strip() or None
    language = form.get("language") or "es"
    transcription_model = form.get("transcription_model") or DEFAULT_TRANSCRIPTION_MODEL
    template_id_str = form.get("template_id")
    glossary_terms = parse_glossary_terms(form.get("glossary"))

    if not title:
        return None, "El titulo es obligatorio."
    if not is_valid_language(language):
        return None, "Idioma no valido."
    if not is_valid_transcription_model(transcription_model):
        return None, "Modelo de transcripcion no valido."
    if not template_id_str or not template_id_str.isdigit():
        return None, "Plantilla no valida."

    template = Template.query.get(int(template_id_str))
    if not template:
        return None, "La plantilla seleccionada no existe."

    return {
        "title": title,
        "client_name": client_name,
        "event_name": event_name,
        "language": language,
        "transcription_model": transcription_model,
        "template": template,
        "glossary_terms": glossary_terms,
    }, template


def _enqueue_created_project(project: Project):
    try:
        enqueue_project_processing(project.id)
    except Exception as exc:  # pragma: no cover - depends on Redis availability
        project.status = "failed"
        project.error_message = f"No se pudo encolar el proyecto: {exc}"
        add_project_log(project.id, project.error_message, level="error")
        db.session.commit()
        return _create_project_failure(project, project.error_message)
    return _create_project_success(project, "Proyecto creado y encolado.")


@bp.post("/uploads/media/start")
def start_media_upload():
    data = request.get_json(silent=True) or {}
    original_filename = str(data.get("filename") or "").strip()
    filename = secure_filename(original_filename)
    size = int(data.get("size") or 0)
    content_type = str(data.get("content_type") or "").strip()

    if not filename:
        return _json_error("Nombre de archivo no valido.")
    if not allowed_file(filename):
        return _json_error("Tipo de archivo no permitido.")
    if size <= 0:
        return _json_error("El archivo esta vacio o no informa tamano.")

    max_content_length = current_app.config.get("MAX_CONTENT_LENGTH")
    if max_content_length and size > max_content_length:
        max_upload_mb = current_app.config.get("MAX_UPLOAD_MB")
        return _json_error(
            f"El archivo supera el limite de subida configurado ({max_upload_mb} MB).",
            413,
        )

    upload_id = uuid.uuid4().hex
    upload_dir = _chunk_upload_dir(upload_id)
    chunks_dir = upload_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    chunk_size = _media_upload_chunk_bytes()
    metadata = {
        "version": 1,
        "upload_id": upload_id,
        "original_filename": original_filename,
        "filename": filename,
        "size": size,
        "content_type": content_type,
        "chunk_size": chunk_size,
    }
    _chunk_metadata_path(upload_id).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return jsonify(
        {
            "ok": True,
            "upload_id": upload_id,
            "chunk_size": chunk_size,
            "received_chunks": [],
        }
    )


@bp.post("/uploads/media/<upload_id>/chunk")
def upload_media_chunk(upload_id: str):
    metadata = _load_chunk_metadata(upload_id)
    chunk_file = request.files.get("chunk")
    if not chunk_file:
        return _json_error("No se recibio el fragmento de archivo.")

    try:
        chunk_index = int(request.form.get("chunk_index", "-1"))
        total_chunks = int(request.form.get("total_chunks", "0"))
    except ValueError:
        return _json_error("Indice de fragmento no valido.")

    if chunk_index < 0 or total_chunks <= 0 or chunk_index >= total_chunks:
        return _json_error("Indice de fragmento fuera de rango.")

    upload_dir = _chunk_upload_dir(upload_id)
    chunks_dir = upload_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = chunks_dir / f"{chunk_index:06d}.part"
    tmp_path = chunks_dir / f"{chunk_index:06d}.tmp"
    chunk_file.save(tmp_path)
    tmp_path.replace(chunk_path)

    metadata["total_chunks"] = total_chunks
    _chunk_metadata_path(upload_id).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    received = _received_chunk_indexes(upload_id)
    return jsonify(
        {
            "ok": True,
            "upload_id": upload_id,
            "chunk_index": chunk_index,
            "received_count": len(received),
            "total_chunks": total_chunks,
        }
    )


@bp.post("/uploads/media/<upload_id>/finish")
def finish_media_upload(upload_id: str):
    metadata = _load_chunk_metadata(upload_id)
    project_data, template_or_error = _validate_project_metadata(request.form)
    if not project_data:
        return _json_error(str(template_or_error))

    total_chunks = int(metadata.get("total_chunks") or request.form.get("total_chunks") or 0)
    if total_chunks <= 0:
        return _json_error("La subida no tiene fragmentos registrados.")

    received = _received_chunk_indexes(upload_id)
    missing = [index for index in range(total_chunks) if index not in received]
    if missing:
        return _json_error(
            f"Faltan {len(missing)} fragmento(s) de la subida. Primero pendiente: {missing[0] + 1}/{total_chunks}."
        )

    project = Project(
        title=project_data["title"],
        client_name=project_data["client_name"],
        event_name=project_data["event_name"],
        source_filename=metadata["filename"],
        source_file_path="",
        source_kind=SOURCE_KIND_MEDIA,
        language=project_data["language"],
        glossary_json=serialize_glossary_terms(project_data["glossary_terms"]),
        transcription_model=project_data["transcription_model"],
        template_id=project_data["template"].id,
        status="uploaded",
        share_token=uuid.uuid4().hex,
    )
    db.session.add(project)
    db.session.flush()

    try:
        original_dir = project_original_dir(project.id)
        source_path = original_dir / metadata["filename"]
        tmp_source_path = source_path.with_suffix(source_path.suffix + ".uploading")
        with tmp_source_path.open("wb") as output_file:
            for index in range(total_chunks):
                chunk_path = _chunk_upload_dir(upload_id) / "chunks" / f"{index:06d}.part"
                with chunk_path.open("rb") as input_file:
                    shutil.copyfileobj(input_file, output_file)
        tmp_source_path.replace(source_path)
        expected_size = int(metadata.get("size") or 0)
        actual_size = source_path.stat().st_size
        if expected_size and actual_size != expected_size:
            raise ValueError(
                f"Tamano final inesperado: {actual_size} bytes recibidos de {expected_size}."
            )
    except Exception as exc:
        db.session.rollback()
        shutil.rmtree(project_root(project.id), ignore_errors=True)
        return _json_error(f"No se pudo reconstruir el archivo subido: {exc}", 500)

    project.source_file_path = str(source_path)
    project.status = "queued"
    add_project_log(
        project.id,
        f"Archivo recibido por subida fragmentada ({total_chunks} fragmentos) y proyecto encolado.",
    )
    db.session.commit()
    shutil.rmtree(_chunk_upload_dir(upload_id), ignore_errors=True)
    return _enqueue_created_project(project)


@bp.post("/projects")
def create_project():
    title = (request.form.get("title") or "").strip()
    client_name = (request.form.get("client_name") or "").strip() or None
    event_name = (request.form.get("event_name") or "").strip() or None
    language = request.form.get("language") or "es"
    source_mode = request.form.get("source_mode") or SOURCE_KIND_MEDIA
    transcription_model = request.form.get("transcription_model") or DEFAULT_TRANSCRIPTION_MODEL
    template_id_str = request.form.get("template_id")
    glossary_terms = parse_glossary_terms(request.form.get("glossary"))
    upload = request.files.get("source_file")
    transcript_uploads = [
        file
        for file in request.files.getlist("transcript_files")
        if file and file.filename
    ]
    if not title:
        return _form_error("El titulo es obligatorio.")
    if not is_valid_language(language):
        return _form_error("Idioma no valido.")
    if not is_valid_transcription_model(transcription_model):
        return _form_error("Modelo de transcripcion no valido.")
    if not template_id_str or not template_id_str.isdigit():
        return _form_error("Plantilla no valida.")
    
    template = Template.query.get(int(template_id_str))
    if not template:
        return _form_error("La plantilla seleccionada no existe.")

    if source_mode not in {SOURCE_KIND_MEDIA, SOURCE_KIND_TRANSCRIPT_FILES}:
        return _form_error("Tipo de origen no valido.")

    source_kind = SOURCE_KIND_MEDIA
    if source_mode == SOURCE_KIND_TRANSCRIPT_FILES:
        source_kind = SOURCE_KIND_TRANSCRIPT_FILES
        if not transcript_uploads:
            return _form_error("Sube al menos un archivo de transcripcion.")
        invalid_transcripts = [
            file.filename
            for file in transcript_uploads
            if not allowed_transcript_file(file.filename)
        ]
        if invalid_transcripts:
            return _form_error("Tipo de transcripcion no permitido.")
        filename = _source_filename_summary([file.filename for file in transcript_uploads])
    else:
        if not upload or not upload.filename:
            return _form_error("Selecciona un archivo de audio o video.")
        if not allowed_file(upload.filename):
            return _form_error("Tipo de archivo no permitido.")
        filename = secure_filename(upload.filename)
    project = Project(
        title=title,
        client_name=client_name,
        event_name=event_name,
        source_filename=filename,
        source_file_path="",
        source_kind=source_kind,
        language=language,
        glossary_json=serialize_glossary_terms(glossary_terms),
        transcription_model=transcription_model,
        template_id=template.id,
        status="uploaded",
        share_token=uuid.uuid4().hex,
    )
    db.session.add(project)
    db.session.flush()

    original_dir = project_original_dir(project.id)
    source_path = original_dir / filename
    if source_kind == SOURCE_KIND_TRANSCRIPT_FILES:
        try:
            source_path = _save_transcript_uploads(project.id, transcript_uploads)
        except ValueError as exc:
            db.session.rollback()
            return _form_error(str(exc))
    else:
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
        return _create_project_failure(project, project.error_message)
    else:
        return _create_project_success(project, "Proyecto creado y encolado.")


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
    return render_template(
        "projects/_status_panel.html",
        project=project,
        public_link_oob=request.headers.get("HX-Request") == "true",
        **_template_context(),
    )


@bp.post("/projects/<int:project_id>/transcript/review")
def save_transcript_review(project_id: int):
    project = Project.query.get_or_404(project_id)
    if not project.output:
        abort(404)

    reviewed_transcript = (request.form.get("reviewed_transcript") or "").strip()
    if not reviewed_transcript:
        flash("La transcripcion revisada no puede estar vacia.", "error")
        return redirect(url_for("main.project_detail", project_id=project.id))

    project.output.reviewed_transcript = reviewed_transcript
    project.glossary_json = serialize_glossary_terms(
        parse_glossary_terms(request.form.get("glossary"))
    )
    if project.status == "completed":
        project.status = "reviewing_transcript"
    project.output.quality_report_json = json_dumps(build_quality_report(project, project.output))
    add_project_log(project.id, "Transcripcion revisada guardada. Regenera resumen o dossier para aplicar cambios.")
    db.session.commit()
    flash("Transcripcion revisada guardada.", "success")
    return redirect(url_for("main.project_detail", project_id=project.id))


@bp.post("/projects/<int:project_id>/glossary")
def save_project_glossary(project_id: int):
    project = Project.query.get_or_404(project_id)
    project.glossary_json = serialize_glossary_terms(
        parse_glossary_terms(request.form.get("glossary"))
    )
    if project.output:
        project.output.quality_report_json = json_dumps(build_quality_report(project, project.output))
    add_project_log(project.id, "Glosario del proyecto actualizado.")
    db.session.commit()
    flash("Glosario actualizado.", "success")
    return redirect(url_for("main.project_detail", project_id=project.id))


@bp.post("/projects/<int:project_id>/regenerate")
def regenerate_project(project_id: int):
    project = Project.query.get_or_404(project_id)
    target = request.form.get("target", "all")
    if target not in {"summary", "dossier", "exports", "all"}:
        flash("Tipo de regeneracion no valido.", "error")
        return redirect(url_for("main.project_detail", project_id=project.id))
    try:
        regenerate_project_outputs(project, target)
    except Exception as exc:
        db.session.rollback()
        project = Project.query.get_or_404(project_id)
        project.status = "failed"
        project.error_message = str(exc)
        add_project_log(project.id, f"Regeneracion fallida: {exc}", level="error")
        db.session.commit()
        flash(f"No se pudo regenerar: {exc}", "error")
    else:
        flash("Regeneracion completada.", "success")
    return redirect(url_for("main.project_detail", project_id=project_id))


@bp.post("/projects/<int:project_id>/verify-dossier")
def verify_dossier_grounding(project_id: int):
    project = Project.query.get_or_404(project_id)
    if not project.output:
        abort(404)
    if project.status == "reviewing_transcript":
        flash("Regenera el dossier antes de verificarlo contra la transcripcion revisada.", "error")
        return redirect(url_for("main.project_detail", project_id=project.id))

    transcript = active_transcript(project.output)
    dossier_markdown = (project.output.final_dossier_markdown or "").strip()
    if not transcript or not dossier_markdown:
        flash("Hace falta transcripcion y dossier final para ejecutar la verificacion.", "error")
        return redirect(url_for("main.project_detail", project_id=project.id))

    try:
        raw_review = verify_dossier_against_transcript(
            transcript,
            dossier_markdown,
            project.language,
        )
        project.output.quality_report_json = json_dumps(
            build_quality_report_with_grounding_review(project, project.output, raw_review)
        )
        report = json_loads_object(project.output.quality_report_json)
        review = report.get("grounding_review", {})
        verdict = review.get("verdict", "revisar")
        add_project_log(
            project.id,
            f"Verificacion factual completada: {verdict}.",
            level="warning" if verdict != "aprobado" else "info",
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        add_project_log(project.id, f"Verificacion factual fallida: {exc}", level="error")
        db.session.commit()
        flash(f"No se pudo verificar el dossier: {exc}", "error")
    else:
        flash("Verificacion factual completada.", "success")
    return redirect(url_for("main.project_detail", project_id=project.id))


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


@bp.get("/projects/<int:project_id>/download/pdf")
def download_pdf(project_id: int):
    project = Project.query.get_or_404(project_id)
    if not project.output or not project.output.final_dossier_pdf_path:
        abort(404)

    path = Path(project.output.final_dossier_pdf_path)
    if not path.exists():
        abort(404)

    return send_file(
        path,
        as_attachment=True,
        download_name=f"dossier-{project.id}.pdf",
        mimetype="application/pdf",
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
        markdown_to_docx(content, docx_path, promote_first_paragraph=False)
        return send_file(
            docx_path,
            as_attachment=True,
            download_name=f"{filename_base}.docx",
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    if fmt == "pdf":
        outputs_dir = project_outputs_dir(project.id)
        pdf_path = outputs_dir / f"{filename_base}.pdf"
        text_to_pdf(content, pdf_path)
        return send_file(
            pdf_path,
            as_attachment=True,
            download_name=f"{filename_base}.pdf",
            mimetype="application/pdf",
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


@bp.get("/p/<token>/download/<fmt>")
def shared_download(token: str, fmt: str):
    project = Project.query.filter_by(share_token=token).first_or_404()
    if not project.output:
        abort(404)

    if fmt == "markdown":
        if not project.output.final_dossier_markdown:
            abort(404)
        filename = f"dossier-{project.id}.md"
        return Response(
            project.output.final_dossier_markdown,
            mimetype="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    if fmt == "docx":
        path = Path(project.output.final_dossier_docx_path or "")
        if not path.exists():
            abort(404)
        return send_file(
            path,
            as_attachment=True,
            download_name=f"dossier-{project.id}.docx",
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    if fmt == "pdf":
        path = Path(project.output.final_dossier_pdf_path or "")
        if not path.exists():
            abort(404)
        return send_file(
            path,
            as_attachment=True,
            download_name=f"dossier-{project.id}.pdf",
            mimetype="application/pdf",
        )

    abort(404)

# TEMPLATES CRUD
# ==============================================================================

@bp.get("/templates")
def templates_list():
    templates = Template.query.order_by(Template.is_default.desc(), Template.name).all()
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


@bp.post("/templates/<int:template_id>/set_default")
def set_default_template(template_id: int):
    template = Template.query.get_or_404(template_id)
    Template.query.update({Template.is_default: False})
    template.is_default = True
    db.session.commit()
    flash(f"Plantilla '{template.name}' establecida como predeterminada.", "success")
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
