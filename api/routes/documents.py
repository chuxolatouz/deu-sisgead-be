from flask import Blueprint, request, jsonify
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
from api.util.access import can_access_project, parse_object_id

documents_bp = Blueprint('documents', __name__)


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
                    enum: [new, finished]
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
    skip = page * limit  # Calcular skip basado en page y limit
    documentos = mongo.db.documentos.find({"project_id": project_object_id}).skip(skip).limit(limit)
    total_items = mongo.db.documentos.count_documents({"project_id": project_object_id})
    quantity = math.ceil(total_items / limit) if limit > 0 else 1
    list_cursor = list(documentos)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
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
    return jsonify(request_list=list_json, count=quantity)

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
    Cerrar actividad con aprobación
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
        name: description
        type: string
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
        name: files
        type: file
    responses:
      201:
        description: Actividad cerrada
      400:
        description: Monto excede saldo disponible
    """
    id = _pick_form_value("projectId", "project_id", "proyecto_id")
    doc_id = _pick_form_value("docId", "doc_id")
    data_balance = request.form.get("monto")
    data_descripcion = _pick_form_value("description", "descripcion")
    
    # Note: Local file storage code was present in original but mixed with DB updates
    # I kept the logic structure but note that 'files' folder might be ephemeral in some deployments.
    carpeta_proyecto = os.path.join("files", id)
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

    if not os.path.exists(carpeta_proyecto):
        os.makedirs(carpeta_proyecto)
        
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

    data_balance_int = string_to_int(data_balance)
    amount_units = round(data_balance_int / 100, 2)

    try:
        ProjectFundingService.consume_project_account(
            proyecto,
            year=2025,
            account_code=cuenta_contable,
            amount=amount_units,
            user=user,
            description=data_descripcion or f"Consumo de actividad {doc_id}",
            reference={
                "kind": "project_expense",
                "budgetId": str(doc_id),
                "projectId": str(id),
                "actorName": user.get("nombre", "Usuario"),
                "title": "Consumo por actividad",
                "accountCode": cuenta_contable,
                "referenceNumber": referencia,
                "bank": banco,
                "transferAmount": monto_transferencia,
            },
            allow_negative=False,
            log_message=(
                f'{user["nombre"]} cerro la actividad {data_descripcion} por Bs. {int_to_string(data_balance_int)} '
                f'imputando la partida {cuenta_contable}'
            ),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    archivos = request.files.getlist("files")
    archivos_guardados = []
    
    for archivo in archivos:
        nombre_archivo = archivo.filename
        presupuesto_id = str(ObjectId())
        carpeta_presupuesto = os.path.join(carpeta_proyecto, presupuesto_id)
        if not os.path.exists(carpeta_presupuesto):
             os.makedirs(carpeta_presupuesto)
        
        archivo.save(os.path.join(carpeta_presupuesto, nombre_archivo))
        archivos_guardados.append(
            {
                "nombre": nombre_archivo,
                "ruta": os.path.join(carpeta_presupuesto, nombre_archivo),
            }
        )

    mongo.db.documentos.update_one(
        {"_id": documento_object_id},
        {
            "$set": {
                "status": "finished",
                "monto_aprobado": data_balance_int,
                "archivos_aprobado": archivos_guardados,
                "description": data_descripcion,
                "referencia": referencia,
                "monto_transferencia": monto_transferencia,
                "transferAmount": monto_transferencia,
                "banco": banco,
                "cuenta_contable": cuenta_contable,
                "accountCode": cuenta_contable
            }
        },
    )

    return jsonify({"mensaje": "proyecto ajustado exitosamente"}), 201

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

    if documento["status"] == "finished":
        return jsonify({"mensaje": "Actividad esta finalizada, no se puede eliminar"}), 401
    
    result = mongo.db.documentos.delete_one({"_id": presupuesto_object_id})
    if result.deleted_count == 1:
        message_log = f'{user["nombre"]} elimino la actividad {documento["descripcion"]} con un monto de Bs. {int_to_string(documento["monto"])}'
        agregar_log(documento_project_id, message_log)
        return jsonify({"message": "Actividad eliminada con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar"}), 400
