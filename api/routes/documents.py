from flask import Blueprint, request, jsonify, send_file
from bson import ObjectId, json_util
import json
import os
import math
from datetime import datetime, timezone
from io import BytesIO

from api.extensions import mongo
from api.util.decorators import token_required, allow_cors
from api.util.common import agregar_log
from api.util.utils import string_to_int, int_to_string
from api.util.backblaze import upload_file
from api.services.project_funding_service import ProjectFundingService
from api.util.access import (
    can_access_project,
    is_admin_departamento,
    is_super_admin,
    parse_object_id,
)

documents_bp = Blueprint('documents', __name__)
ALLOWED_RESULT_IMAGE_EXTENSIONS = {".png", ".gif", ".jpeg", ".jpg"}
ALLOWED_RESULT_IMAGE_MIME_PREFIX = "image/"
VALID_ACTIVITY_STATUSES = {"new", "in_progress", "finished"}


def _pick_form_value(*keys):
    for key in keys:
        value = request.form.get(key)
        if value not in (None, ""):
            return value
    return None


def _pick_json_value(data, *keys):
    for key in keys:
        if key in data and data.get(key) not in (None, ""):
            return data.get(key)
    return None


def _is_allowed_result_image(file_storage):
    filename = (getattr(file_storage, "filename", "") or "").strip().lower()
    _, ext = os.path.splitext(filename)
    mimetype = (getattr(file_storage, "mimetype", "") or "").strip().lower()
    return ext in ALLOWED_RESULT_IMAGE_EXTENSIONS or mimetype.startswith(ALLOWED_RESULT_IMAGE_MIME_PREFIX)


def _with_result_attachment_links(doc_id, attachments):
    output = []
    for index, attachment in enumerate(attachments or []):
        item = dict(attachment)
        if not item.get("download_url") and item.get("ruta"):
            item["download_url"] = f"/documentos/{doc_id}/resultados/{index}"
        output.append(item)
    return output


def _result_text(documento):
    return str(
        documento.get("resultados")
        or documento.get("description")
        or ""
    ).strip()


def _ensure_administrative_close_access(user):
    if is_super_admin(user) or is_admin_departamento(user):
        return None
    return _forbidden("Solo super_admin o admin_departamento pueden realizar el cierre administrativo")


def _resolve_activity_funding_year(project):
    year_value = _pick_form_value("year", "fundingYear")
    if year_value in (None, ""):
        year_value = project.get("fundingYear") or project.get("funding_year")

    if year_value in (None, ""):
        return datetime.now(timezone.utc).year, None

    try:
        return int(year_value), None
    except (TypeError, ValueError):
        return None, (jsonify({"error": "year inválido"}), 400)


def _save_result_files(project_id, doc_id, files):
    project_folder = os.path.join("files", str(project_id))
    result_folder = os.path.join(project_folder, str(doc_id), "resultados")
    os.makedirs(result_folder, exist_ok=True)

    attachments = []
    for archivo in files:
        if not archivo or not (archivo.filename or "").strip():
            continue
        file_name = archivo.filename
        file_path = os.path.join(result_folder, file_name)
        archivo.save(file_path)
        attachments.append({"nombre": file_name, "ruta": file_path})
    return attachments


def _forbidden(message="No autorizado"):
    return jsonify({"message": message}), 403


def _ensure_project_access(user, project):
    if not can_access_project(user, project):
        return _forbidden("No autorizado para acceder a este proyecto")
    return None


@documents_bp.route("/proyecto/<string:id>/documentos", methods=["GET"])
@allow_cors
@token_required
def mostrar_documentos_proyecto(user, id):
    """
    Listar actividades de un proyecto
    ---
    tags:
      - Actividades
    parameters:
      - in: path
        name: id
        type: string
        required: true
        description: ID del proyecto
      - in: query
        name: page
        type: integer
        default: 0
      - in: query
        name: limit
        type: integer
        default: 10
    responses:
      200:
        description: Lista de actividades
        schema:
          type: object
          properties:
            request_list:
              type: array
              items:
                type: object
                properties:
                  _id:
                    type: string
                  descripcion:
                    type: string
                  monto:
                    type: integer
                  status:
                    type: string
                    enum: [new, in_progress, finished]
            count:
              type: integer
    """
    project_object_id = parse_object_id(id)
    if not project_object_id:
        return jsonify({"message": "ID de proyecto inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id}, {"departamento_id": 1})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    params = request.args
    page = int(params.get("page")) if params.get("page") else 0
    limit = int(params.get("limit")) if params.get("limit") else 10
    status = (params.get("status") or "").strip().lower()
    skip = page * limit  # Calcular skip basado en page y limit
    query = {
        "$or": [
            {"project_id": project_object_id},
            {"proyecto_id": project_object_id},
        ]
    }
    if status in VALID_ACTIVITY_STATUSES:
        query["status"] = status

    documentos = mongo.db.documentos.find(query).skip(skip).limit(limit)
    total_items = mongo.db.documentos.count_documents(query)
    quantity = math.ceil(total_items / limit) if limit > 0 else 1
    list_cursor = list(documentos)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump)
    for documento in list_json:
        project_id = documento.get("project_id")
        if isinstance(project_id, dict):
            documento["projectId"] = project_id.get("$oid")
        elif project_id:
            documento["projectId"] = str(project_id)

        if "objetivo_especifico" in documento:
            documento["specificObjective"] = documento.get("objetivo_especifico")
        if "monto_transferencia" in documento:
            documento["transferAmount"] = documento.get("monto_transferencia")
        if "cuenta_contable" in documento:
            documento["accountCode"] = documento.get("cuenta_contable")
        documento["resultados"] = _result_text(documento)
        documento["resultDescription"] = documento.get("resultados")
        documento["logros"] = documento.get("logros") or ""
        documento["limitaciones"] = documento.get("limitaciones") or ""
        documento["lecciones"] = documento.get("lecciones") or ""
        documento["lineas_accion"] = documento.get("lineas_accion") or ""
        documento["lineasAccion"] = documento.get("lineas_accion") or ""
        if "administrative_closed_at" in documento:
            documento["administrativeClosedAt"] = documento.get("administrative_closed_at")
        if "finalized_at" in documento:
            documento["finalizedAt"] = documento.get("finalized_at")
        documento["resultAttachments"] = _with_result_attachment_links(
            documento.get("_id", {}).get("$oid"),
            documento.get("archivos_aprobado", []),
        )
    return jsonify(request_list=list_json, count=quantity)


@documents_bp.route("/documentos/<string:doc_id>/resultados/<int:file_index>", methods=["GET"])
@allow_cors
@token_required
def descargar_resultado(user, doc_id, file_index):
    documento_object_id = parse_object_id(doc_id)
    if not documento_object_id:
        return jsonify({"message": "ID de actividad inválido"}), 400

    documento = mongo.db.documentos.find_one({"_id": documento_object_id})
    if not documento:
        return jsonify({"message": "Actividad no encontrada"}), 404

    project_object_id = parse_object_id(documento.get("project_id") or documento.get("proyecto_id"))
    if not project_object_id:
        return jsonify({"message": "Proyecto inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id}, {"departamento_id": 1})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    attachments = documento.get("archivos_aprobado") or []
    if file_index < 0 or file_index >= len(attachments):
        return jsonify({"message": "Adjunto no encontrado"}), 404

    attachment = attachments[file_index]
    file_path = attachment.get("ruta")
    if not file_path or not os.path.exists(file_path):
        return jsonify({"message": "Archivo no disponible"}), 404

    return send_file(file_path, as_attachment=False, download_name=attachment.get("nombre"))

@documents_bp.route("/documento_crear", methods=["POST"])
@allow_cors
@token_required
def crear_presupuesto(user):
    """
    Crear nueva actividad con archivos
    ---
    tags:
      - Actividades
    security:
      - Bearer: []
    consumes:
      - multipart/form-data
    parameters:
      - in: formData
        name: proyecto_id
        type: string
        required: true
      - in: formData
        name: descripcion
        type: string
        required: true
      - in: formData
        name: monto
        type: string
        required: true
        description: Monto en formato string (ej. "1000.00")
      - in: formData
        name: objetivo_especifico
        type: string
      - in: formData
        name: files
        type: file
        description: Archivos adjuntos
    responses:
      201:
        description: Actividad creada
        schema:
          type: object
          properties:
            mensaje:
              type: string
            _id:
              type: string
      400:
        description: Campos requeridos faltantes
    """
    project_id = _pick_form_value("projectId", "project_id", "proyecto_id")
    descripcion = request.form.get("descripcion")
    monto = request.form.get("monto")
    objetivo_especifico = _pick_form_value("specificObjective", "objetivo_especifico")

    if not project_id or not descripcion or not monto:
        return jsonify({"error": "Missing required fields"}), 400

    project_object_id = parse_object_id(project_id)
    if not project_object_id:
        return jsonify({"error": "projectId inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id}, {"departamento_id": 1})
    if not proyecto:
        return jsonify({"error": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error
        
    presupuesto_id = str(ObjectId())

    presupuesto = {
        "project_id": project_object_id,
        "presupuesto_id": presupuesto_id,
        "descripcion": descripcion,
        "monto": string_to_int(monto),
        "status": "new",
        "objetivo_especifico": objetivo_especifico,
        "archivos": [],
        "created_at": datetime.utcnow(),
    }

    archivos = request.files.getlist("files")
    error_messages = []

    for archivo in archivos:
        public_id = f"budgets/{project_id}/{presupuesto_id}/{archivo.filename}"
        file_buffer = BytesIO(archivo.read())
        upload_result = upload_file(file_buffer, public_id)

        if upload_result is not None:
            presupuesto["archivos"].append(
                {"nombre": archivo.filename, "public_id": upload_result["fileId"], "download_url": upload_result["download_url"]}
            )
        else:
            error_messages.append(
                f"Error uploading file {archivo.filename}: {upload_result.get('error')}"
            )

    if error_messages:
        return jsonify({"error": error_messages}), 400

    result = mongo.db.documentos.insert_one(presupuesto)

    if not result.acknowledged:
        return jsonify({"error": "Error saving actividad"}), 500

    message_log = f'{user["nombre"]} agrego la actividad {descripcion} con un monto de Bs. {monto}'
    agregar_log(project_id, message_log)

    return jsonify({"mensaje": "Archivos subidos exitosamente", "_id": str(result.inserted_id)}), 201

@documents_bp.route("/documento_cerrar", methods=["POST"])
@allow_cors
@token_required
def cerrar_presupuesto(user):
    """
    Registrar cierre administrativo de actividad
    ---
    tags:
      - Actividades
    security:
      - Bearer: []
    consumes:
      - multipart/form-data
    parameters:
      - in: formData
        name: proyecto_id
        type: string
        required: true
      - in: formData
        name: doc_id
        type: string
        required: true
        description: ID de la actividad
      - in: formData
        name: monto
        type: string
        required: true
        description: Monto aprobado
      - in: formData
        name: referencia
        type: string
      - in: formData
        name: monto_transferencia
        type: string
      - in: formData
        name: banco
        type: string
      - in: formData
        name: cuenta_contable
        type: string
      - in: formData
        name: year
        type: integer
        description: Año contable del proyecto
    responses:
      201:
        description: Cierre administrativo registrado
      400:
        description: Monto excede saldo disponible
    """
    id = _pick_form_value("projectId", "project_id", "proyecto_id")
    doc_id = _pick_form_value("docId", "doc_id")
    data_balance = request.form.get("monto")
    data_descripcion = (_pick_form_value("description", "descripcion") or "").strip()
    referencia = request.form.get("referencia")
    monto_transferencia = _pick_form_value("transferAmount", "monto_transferencia")
    banco = (request.form.get("banco") or "").strip()
    cuenta_contable = (_pick_form_value("accountCode", "cuenta_contable") or "").strip()

    if not id or not doc_id or not data_balance:
        return jsonify({"error": "projectId, docId y monto son requeridos"}), 400
    if not cuenta_contable:
        return jsonify({"error": "accountCode es requerido"}), 400

    project_object_id = parse_object_id(id)
    if not project_object_id:
        return jsonify({"error": "projectId inválido"}), 400

    documento_object_id = parse_object_id(doc_id)
    if not documento_object_id:
        return jsonify({"error": "docId inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id})
    if not proyecto:
        return jsonify({"error": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    funding_year, funding_year_error = _resolve_activity_funding_year(proyecto)
    if funding_year_error:
        return funding_year_error

    admin_access_error = _ensure_administrative_close_access(user)
    if admin_access_error:
        return admin_access_error

    documento = mongo.db.documentos.find_one({"_id": documento_object_id})
    if not documento:
        return jsonify({"error": "Actividad no encontrada"}), 404

    documento_project_id = parse_object_id(documento.get("project_id") or documento.get("proyecto_id"))
    if not documento_project_id or str(documento_project_id) != str(project_object_id):
        return jsonify({"error": "La actividad no pertenece al proyecto indicado"}), 400

    current_status = (documento.get("status") or "new").strip().lower()
    if current_status == "in_progress":
        return jsonify({"error": "La actividad ya tiene un cierre administrativo registrado"}), 400
    if current_status == "finished":
        return jsonify({"error": "La actividad ya está finalizada"}), 400
    if current_status != "new":
        return jsonify({"error": "La actividad no se encuentra en un estado válido para cierre administrativo"}), 400

    data_balance_int = string_to_int(data_balance)
    amount_units = round(data_balance_int / 100, 2)
    accounting_description = data_descripcion or documento.get("descripcion") or f"Consumo de actividad {doc_id}"

    try:
        actor_name = user.get("nombre", "Usuario")
        ProjectFundingService.consume_project_account(
            proyecto,
            year=funding_year,
            account_code=cuenta_contable,
            amount=amount_units,
            user=user,
            description=accounting_description,
            reference={
                "kind": "project_expense",
                "budgetId": str(doc_id),
                "projectId": str(id),
                "actorName": actor_name,
                "title": "Consumo por actividad",
                "accountCode": cuenta_contable,
                "referenceNumber": referencia,
                "bank": banco,
                "transferAmount": monto_transferencia,
            },
            allow_negative=False,
            log_message=(
                f'{actor_name} registro el cierre administrativo de la actividad {documento.get("descripcion", "")} '
                f'por Bs. {int_to_string(data_balance_int)} '
                f'imputando la partida {cuenta_contable}'
            ),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    mongo.db.documentos.update_one(
        {"_id": documento_object_id},
        {
            "$set": {
                "status": "in_progress",
                "monto_aprobado": data_balance_int,
                "referencia": referencia,
                "monto_transferencia": monto_transferencia,
                "transferAmount": monto_transferencia,
                "banco": banco,
                "cuenta_contable": cuenta_contable,
                "accountCode": cuenta_contable,
                "administrative_closed_at": datetime.utcnow(),
            }
        },
    )

    return jsonify({"mensaje": "Cierre administrativo registrado exitosamente", "year": funding_year}), 201


@documents_bp.route("/documento_finalizar", methods=["POST"])
@allow_cors
@token_required
def finalizar_actividad(user):
    """
    Finalizar actividad con resultados e imágenes
    ---
    tags:
      - Actividades
    security:
      - Bearer: []
    consumes:
      - multipart/form-data
    parameters:
      - in: formData
        name: proyecto_id
        type: string
        required: true
      - in: formData
        name: doc_id
        type: string
        required: true
      - in: formData
        name: resultados
        type: string
        required: true
      - in: formData
        name: logros
        type: string
      - in: formData
        name: limitaciones
        type: string
      - in: formData
        name: lecciones
        type: string
      - in: formData
        name: lineas_accion
        type: string
      - in: formData
        name: files
        type: file
    responses:
      201:
        description: Actividad finalizada
    """
    id = _pick_form_value("projectId", "project_id", "proyecto_id")
    doc_id = _pick_form_value("docId", "doc_id")
    resultados = (
        _pick_form_value("resultados", "resultDescription", "description", "descripcion")
        or ""
    ).strip()
    logros = (_pick_form_value("logros") or "").strip()
    limitaciones = (_pick_form_value("limitaciones") or "").strip()
    lecciones = (_pick_form_value("lecciones") or "").strip()
    lineas_accion = (
        _pick_form_value("lineasAccion", "lineas_accion")
        or ""
    ).strip()

    if not id or not doc_id:
        return jsonify({"error": "projectId y docId son requeridos"}), 400
    if not resultados:
        return jsonify({"error": "resultados es requerido"}), 400

    project_object_id = parse_object_id(id)
    if not project_object_id:
        return jsonify({"error": "projectId inválido"}), 400

    documento_object_id = parse_object_id(doc_id)
    if not documento_object_id:
        return jsonify({"error": "docId inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id})
    if not proyecto:
        return jsonify({"error": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    documento = mongo.db.documentos.find_one({"_id": documento_object_id})
    if not documento:
        return jsonify({"error": "Actividad no encontrada"}), 404

    documento_project_id = parse_object_id(documento.get("project_id") or documento.get("proyecto_id"))
    if not documento_project_id or str(documento_project_id) != str(project_object_id):
        return jsonify({"error": "La actividad no pertenece al proyecto indicado"}), 400

    current_status = (documento.get("status") or "new").strip().lower()
    if current_status == "new":
        return jsonify({"error": "La actividad debe pasar por cierre administrativo antes de finalizarse"}), 400
    if current_status == "finished":
        return jsonify({"error": "La actividad ya está finalizada"}), 400
    if current_status != "in_progress":
        return jsonify({"error": "La actividad no se encuentra en un estado válido para finalizarse"}), 400

    archivos = request.files.getlist("files")
    invalid_files = [
        archivo.filename
        for archivo in archivos
        if archivo and (archivo.filename or "").strip() and not _is_allowed_result_image(archivo)
    ]
    if invalid_files:
        return jsonify({"error": "Solo se permiten imágenes PNG, GIF, JPEG o JPG en el cierre de actividad"}), 400

    archivos_guardados = _save_result_files(id, doc_id, archivos)

    mongo.db.documentos.update_one(
        {"_id": documento_object_id},
        {
            "$set": {
                "status": "finished",
                "resultados": resultados,
                "description": resultados,
                "logros": logros,
                "limitaciones": limitaciones,
                "lecciones": lecciones,
                "lineas_accion": lineas_accion,
                "archivos_aprobado": archivos_guardados,
                "finalized_at": datetime.utcnow(),
            }
        },
    )

    agregar_log(
        id,
        f'{user.get("nombre", "Usuario")} finalizo la actividad {documento.get("descripcion", "")}',
    )

    return jsonify({"mensaje": "Actividad finalizada exitosamente"}), 201

@documents_bp.route("/eliminar_presupuesto", methods=["POST"])
@documents_bp.route("/documento_eliminar", methods=["POST"])
@allow_cors
@token_required
def eliminar_presupuesto_route(user): 
    """
    Eliminar actividad
    ---
    tags:
      - Actividades
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - budget_id
          properties:
            budget_id:
              type: string
              description: ID de la actividad
            project_id:
              type: string
              description: ID del proyecto
    responses:
      200:
        description: Actividad eliminada
      401:
        description: Actividad finalizada, no se puede eliminar
      404:
        description: Actividad no encontrada
    """ 
    data = request.get_json(silent=True) or {}
    presupuesto_id = _pick_json_value(data, "budgetId", "budget_id")
    project_id = _pick_json_value(data, "projectId", "project_id", "proyecto_id")
    if not presupuesto_id:
        return jsonify({"message": "budgetId es requerido"}), 400

    presupuesto_object_id = parse_object_id(presupuesto_id)
    if not presupuesto_object_id:
        return jsonify({"message": "budgetId inválido"}), 400

    documento = mongo.db.documentos.find_one({"_id": presupuesto_object_id})
    if documento is None:
        return jsonify({"message": "Actividad no encontrada"}), 404

    documento_project_id = parse_object_id(documento.get("project_id") or documento.get("proyecto_id") or project_id)
    if not documento_project_id:
        return jsonify({"message": "La actividad no está asociada a un proyecto válido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": documento_project_id}, {"departamento_id": 1})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    if (documento.get("status") or "new") != "new":
        return jsonify({"mensaje": "Solo se pueden eliminar actividades en estado nuevo"}), 401
    
    result = mongo.db.documentos.delete_one({"_id": presupuesto_object_id})
    if result.deleted_count == 1:
        message_log = f'{user["nombre"]} elimino la actividad {documento["descripcion"]} con un monto de Bs. {int_to_string(documento["monto"])}'
        agregar_log(documento_project_id, message_log)
        return jsonify({"message": "Actividad eliminada con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar"}), 400
