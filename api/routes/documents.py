from flask import Blueprint, current_app, request, jsonify, send_file
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
VALID_ACTIVITY_STATUSES = {"new", "partial_admin_closed", "in_progress", "finished"}
ITEM_PENDING_STATUS = "pending"
ITEM_CLOSED_STATUS = "closed"
VALID_ITEM_STATUSES = {ITEM_PENDING_STATUS, ITEM_CLOSED_STATUS}


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


def _pick_mapping_value(data, *keys):
    if not isinstance(data, dict):
        return None
    for key in keys:
        if key in data and data.get(key) not in (None, ""):
            return data.get(key)
    return None


def _amount_to_cents(value, default=0):
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return string_to_int(value)
    return int(round(float(value) * 100))


def _coerce_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "si", "on"}


def _normalize_specific_objectives(value):
    if value in (None, ""):
        return []

    parsed = value
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("objetivos_especificos debe ser un arreglo JSON válido") from exc

    values = parsed if isinstance(parsed, (list, tuple)) else [parsed]
    normalized = []
    for objective in values:
        text = str(objective or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _validate_activity_objectives(project, objectives):
    project_objectives = _normalize_specific_objectives(project.get("objetivos_especificos"))
    if not project_objectives:
        return None
    invalid = [objective for objective in objectives if objective not in project_objectives]
    if invalid:
        return f"Objetivos específicos no asociados al proyecto: {', '.join(invalid)}"
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


def _is_sponsored_activity(documento):
    return _coerce_bool(documento.get("patrocinada") or documento.get("isSponsored"))


def _resolve_sponsored_activity_account_code():
    return str(current_app.config.get("SPONSORED_ACTIVITY_ACCOUNT_CODE") or "").strip()


def _activity_has_real_items(documento):
    return isinstance(documento.get("items"), list)


def _normalize_activity_item(data, existing=None):
    existing = dict(existing or {})
    item = dict(existing)
    item["id"] = str(_pick_mapping_value(data, "id", "_id") or existing.get("id") or ObjectId())
    if "nombre" in data or "name" in data:
        item["nombre"] = str(_pick_mapping_value(data, "nombre", "name") or "").strip()
    else:
        item["nombre"] = str(existing.get("nombre") or existing.get("name") or "").strip()
    if "descripcion" in data or "description" in data:
        item["descripcion"] = str(_pick_mapping_value(data, "descripcion", "description") or "").strip()
    else:
        item["descripcion"] = str(existing.get("descripcion") or existing.get("description") or "").strip()

    if any(key in data for key in ("monto", "amount")):
        item["monto"] = _amount_to_cents(_pick_mapping_value(data, "monto", "amount"), default=0)
    else:
        item["monto"] = int(existing.get("monto", existing.get("amount", 0)) or 0)

    account_code = _pick_mapping_value(data, "accountCode", "cuenta_contable", "account_code")
    if account_code is not None:
        item["accountCode"] = str(account_code).strip()
        item["cuenta_contable"] = item["accountCode"]
    elif existing.get("accountCode") or existing.get("cuenta_contable"):
        item["accountCode"] = str(existing.get("accountCode") or existing.get("cuenta_contable")).strip()
        item["cuenta_contable"] = item["accountCode"]
    else:
        item["accountCode"] = ""
        item["cuenta_contable"] = ""

    status = str(existing.get("status") or ITEM_PENDING_STATUS).strip().lower()
    item["status"] = status if status in VALID_ITEM_STATUSES else ITEM_PENDING_STATUS
    return item


def _parse_activity_items_from_form():
    raw_items = _pick_form_value("items", "activityItems")
    if raw_items is None:
        return []
    try:
        parsed = json.loads(raw_items) if isinstance(raw_items, str) else raw_items
    except (TypeError, ValueError):
        raise ValueError("items debe ser un arreglo JSON válido")
    if not isinstance(parsed, list):
        raise ValueError("items debe ser un arreglo")
    return [_normalize_activity_item(item if isinstance(item, dict) else {}) for item in parsed]


def _activity_total_from_items(items):
    return sum(int(item.get("monto") or 0) for item in items or [])


def _activity_status_for_items(items, fallback="new"):
    if not items:
        return fallback
    closed_count = sum(1 for item in items if item.get("status") == ITEM_CLOSED_STATUS)
    if closed_count == len(items):
        return "in_progress"
    if closed_count > 0:
        return "partial_admin_closed"
    return "new"


def _item_amount_approved(item):
    return int(item.get("montoAprobado") or item.get("monto_aprobado") or 0)


def _account_summary(items):
    codes = sorted({
        str(item.get("accountCode") or item.get("cuenta_contable") or "").strip()
        for item in items or []
        if str(item.get("accountCode") or item.get("cuenta_contable") or "").strip()
    })
    return codes[0] if len(codes) == 1 else ", ".join(codes)


def _legacy_activity_item(documento):
    status = ITEM_CLOSED_STATUS if (documento.get("status") in {"in_progress", "finished"}) else ITEM_PENDING_STATUS
    return {
        "id": "legacy",
        "nombre": documento.get("descripcion") or "Actividad",
        "descripcion": documento.get("descripcion") or "",
        "monto": int(documento.get("monto") or 0),
        "accountCode": documento.get("accountCode") or documento.get("cuenta_contable") or "",
        "cuenta_contable": documento.get("accountCode") or documento.get("cuenta_contable") or "",
        "status": status,
        "montoAprobado": int(documento.get("monto_aprobado") or 0),
        "monto_aprobado": int(documento.get("monto_aprobado") or 0),
        "referencia": documento.get("referencia") or "",
        "banco": documento.get("banco") or "",
        "transferAmount": documento.get("transferAmount") or documento.get("monto_transferencia") or "",
        "isSynthetic": True,
    }


def _decorate_activity_document(documento):
    project_id = documento.get("project_id")
    if isinstance(project_id, dict):
        documento["projectId"] = project_id.get("$oid")
    elif project_id:
        documento["projectId"] = str(project_id)

    raw_objectives = (
        documento.get("objetivos_especificos")
        if "objetivos_especificos" in documento
        else documento.get("objetivo_especifico")
    )
    specific_objectives = _normalize_specific_objectives(raw_objectives)
    documento["objetivos_especificos"] = specific_objectives
    documento["specificObjectives"] = specific_objectives
    documento["objetivo_especifico"] = specific_objectives[0] if specific_objectives else ""
    documento["specificObjective"] = documento["objetivo_especifico"]
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
    documento["patrocinada"] = _is_sponsored_activity(documento)
    documento["isSponsored"] = documento["patrocinada"]
    has_real_items = _activity_has_real_items(documento)
    items = documento.get("items") if has_real_items else [_legacy_activity_item(documento)]
    decorated_items = []
    for item in items or []:
        normalized = dict(item)
        normalized["id"] = str(normalized.get("id") or normalized.get("_id") or "")
        normalized["nombre"] = normalized.get("nombre") or normalized.get("name") or ""
        normalized["descripcion"] = normalized.get("descripcion") or normalized.get("description") or ""
        normalized["accountCode"] = normalized.get("accountCode") or normalized.get("cuenta_contable") or ""
        normalized["cuenta_contable"] = normalized["accountCode"]
        normalized["montoAprobado"] = _item_amount_approved(normalized)
        normalized["monto_aprobado"] = normalized["montoAprobado"]
        normalized["transferAmount"] = normalized.get("transferAmount") or normalized.get("monto_transferencia") or ""
        decorated_items.append(normalized)
    documento["items"] = decorated_items
    documento["hasRealItems"] = has_real_items
    documento["itemsSummary"] = {
        "total": len(decorated_items),
        "closed": sum(1 for item in decorated_items if item.get("status") == ITEM_CLOSED_STATUS),
        "pending": sum(1 for item in decorated_items if item.get("status") != ITEM_CLOSED_STATUS),
    }
    document_id = documento.get("_id")
    if isinstance(document_id, dict):
        document_id = document_id.get("$oid")
    documento["resultAttachments"] = _with_result_attachment_links(
        document_id,
        documento.get("archivos_aprobado", []),
    )
    return documento


def _ensure_administrative_close_access(user):
    if is_super_admin(user) or is_admin_departamento(user):
        return None
    return _forbidden("Solo super_admin o admin_departamento pueden realizar el cierre administrativo")


def _resolve_activity_funding_year(project):
    year_value = _pick_form_value("year", "fundingYear")
    if year_value in (None, "") and request.is_json:
        data = request.get_json(silent=True) or {}
        year_value = _pick_mapping_value(data, "year", "fundingYear")
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
        _decorate_activity_document(documento)
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


def _load_activity_with_project(user, doc_id):
    documento_object_id = parse_object_id(doc_id)
    if not documento_object_id:
        return None, None, None, (jsonify({"message": "ID de actividad inválido"}), 400)

    documento = mongo.db.documentos.find_one({"_id": documento_object_id})
    if not documento:
        return None, None, None, (jsonify({"message": "Actividad no encontrada"}), 404)

    project_object_id = parse_object_id(documento.get("project_id") or documento.get("proyecto_id"))
    if not project_object_id:
        return None, None, None, (jsonify({"message": "Proyecto inválido"}), 400)

    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id})
    if not proyecto:
        return None, None, None, (jsonify({"message": "Proyecto no encontrado"}), 404)

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return None, None, None, access_error

    return documento_object_id, documento, proyecto, None


def _close_activity_items(documento, proyecto, user, funding_year, payload=None, item_id=None):
    payload = payload or {}
    if not _activity_has_real_items(documento):
        return None, (jsonify({"error": "La actividad no tiene items administrativos configurados"}), 400)

    items = [dict(item) for item in (documento.get("items") or [])]
    if not items:
        return None, (jsonify({"error": "La actividad debe tener al menos un item antes del cierre administrativo"}), 400)

    is_sponsored = _is_sponsored_activity(documento)
    sponsored_account = ""
    if is_sponsored:
        sponsored_account = _resolve_sponsored_activity_account_code()
        if not sponsored_account:
            return None, (jsonify({"error": "No hay una cuenta de patrocinio configurada para registrar esta actividad"}), 400)

    referencia = _pick_mapping_value(payload, "referencia", "referenceNumber") or ""
    banco = _pick_mapping_value(payload, "banco", "bank") or ""
    transfer_amount = _pick_mapping_value(payload, "transferAmount", "monto_transferencia") or ""
    fallback_account = str(_pick_mapping_value(payload, "accountCode", "cuenta_contable") or "").strip()
    fallback_amount = _pick_mapping_value(payload, "monto", "amount", "montoAprobado")
    actor_name = user.get("nombre", "Usuario")
    now = datetime.utcnow()

    closed_any = False
    updated_items = []
    for item in items:
        matches_item = item_id is None or str(item.get("id")) == str(item_id)
        if not matches_item or item.get("status") == ITEM_CLOSED_STATUS:
            updated_items.append(item)
            continue

        account_code = sponsored_account if is_sponsored else str(
            item.get("accountCode") or item.get("cuenta_contable") or fallback_account
        ).strip()
        if not is_sponsored and not account_code:
            return None, (jsonify({"error": f"El item {item.get('nombre') or item.get('id')} requiere una partida asociada"}), 400)

        approved_cents = 0 if is_sponsored else int(item.get("monto") or 0)
        if fallback_amount not in (None, "") and item_id is not None:
            approved_cents = 0 if is_sponsored else _amount_to_cents(fallback_amount, default=approved_cents)
        if not is_sponsored and approved_cents <= 0:
            return None, (jsonify({"error": f"El item {item.get('nombre') or item.get('id')} debe tener un monto mayor a 0"}), 400)

        if not is_sponsored:
            try:
                ProjectFundingService.consume_project_account(
                    proyecto,
                    year=funding_year,
                    account_code=account_code,
                    amount=round(approved_cents / 100, 2),
                    user=user,
                    description=f"{documento.get('descripcion', 'Actividad')} - {item.get('nombre') or 'Item'}",
                    reference={
                        "kind": "project_expense",
                        "budgetId": str(documento.get("_id")),
                        "activityItemId": str(item.get("id")),
                        "projectId": str(proyecto.get("_id")),
                        "actorName": actor_name,
                        "title": "Consumo por item de actividad",
                        "accountCode": account_code,
                        "referenceNumber": referencia,
                        "bank": banco,
                        "transferAmount": transfer_amount,
                    },
                    allow_negative=False,
                    log_message=(
                        f'{actor_name} cerro administrativamente el item {item.get("nombre", "")} '
                        f'de la actividad {documento.get("descripcion", "")} por Bs. {int_to_string(approved_cents)} '
                        f'imputando la partida {account_code}'
                    ),
                )
            except ValueError as exc:
                return None, (jsonify({"error": str(exc)}), 400)
        else:
            agregar_log(
                proyecto.get("_id"),
                (
                    f'{actor_name} cerro administrativamente el item patrocinado {item.get("nombre", "")} '
                    f'de la actividad {documento.get("descripcion", "")} usando la cuenta de referencia {account_code}'
                ),
            )

        item.update(
            {
                "status": ITEM_CLOSED_STATUS,
                "montoAprobado": approved_cents,
                "monto_aprobado": approved_cents,
                "referencia": referencia,
                "banco": banco,
                "transferAmount": "0" if is_sponsored else transfer_amount,
                "monto_transferencia": "0" if is_sponsored else transfer_amount,
                "accountCode": account_code,
                "cuenta_contable": account_code,
                "closedAt": now,
                "closedBy": {"id": user.get("sub"), "nombre": actor_name},
            }
        )
        closed_any = True
        updated_items.append(item)

    if item_id is not None and not any(str(item.get("id")) == str(item_id) for item in items):
        return None, (jsonify({"error": "Item no encontrado"}), 404)
    if not closed_any:
        return None, (jsonify({"error": "No hay items pendientes por cerrar"}), 400)

    new_status = _activity_status_for_items(updated_items)
    total_approved = sum(_item_amount_approved(item) for item in updated_items)
    account_code_summary = _account_summary(updated_items)
    set_payload = {
        "items": updated_items,
        "monto": _activity_total_from_items(updated_items),
        "status": new_status,
        "monto_aprobado": total_approved,
        "cuenta_contable": account_code_summary,
        "accountCode": account_code_summary,
        "patrocinada": is_sponsored,
    }
    if new_status in {"partial_admin_closed", "in_progress"}:
        set_payload["administrative_closed_at"] = documento.get("administrative_closed_at") or now

    mongo.db.documentos.update_one({"_id": documento["_id"]}, {"$set": set_payload})
    updated_documento = {**documento, **set_payload}
    return updated_documento, None

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
        name: objetivos_especificos
        type: string
        description: Arreglo JSON de objetivos específicos
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
    raw_objectives = _pick_form_value(
        "specificObjectives",
        "objetivosEspecificos",
        "objetivos_especificos",
        "specificObjective",
        "objetivo_especifico",
    )
    try:
        objetivos_especificos = _normalize_specific_objectives(raw_objectives)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    patrocinada = _coerce_bool(_pick_form_value("patrocinada", "isSponsored"), default=False)
    try:
        items = _parse_activity_items_from_form()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if not project_id or not descripcion or monto in (None, ""):
        return jsonify({"error": "Missing required fields"}), 400

    project_object_id = parse_object_id(project_id)
    if not project_object_id:
        return jsonify({"error": "projectId inválido"}), 400

    proyecto = mongo.db.proyectos.find_one(
        {"_id": project_object_id},
        {"departamento_id": 1, "objetivos_especificos": 1},
    )
    if not proyecto:
        return jsonify({"error": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    objectives_error = _validate_activity_objectives(proyecto, objetivos_especificos)
    if objectives_error:
        return jsonify({"error": objectives_error}), 400
        
    presupuesto_id = str(ObjectId())

    total_items = _activity_total_from_items(items)
    presupuesto_monto = total_items if items else string_to_int(monto)
    presupuesto = {
        "project_id": project_object_id,
        "presupuesto_id": presupuesto_id,
        "descripcion": descripcion,
        "monto": presupuesto_monto,
        "status": "new",
        "objetivos_especificos": objetivos_especificos,
        "objetivo_especifico": objetivos_especificos[0] if objetivos_especificos else "",
        "patrocinada": patrocinada,
        "items": items,
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

    if not getattr(result, "acknowledged", True):
        return jsonify({"error": "Error saving actividad"}), 500

    message_log = f'{user["nombre"]} agrego la actividad {descripcion} con un monto de Bs. {monto}'
    agregar_log(project_id, message_log)

    return jsonify({"mensaje": "Archivos subidos exitosamente", "_id": str(result.inserted_id)}), 201


@documents_bp.route("/documentos/<string:doc_id>", methods=["PUT"])
@allow_cors
@token_required
def editar_actividad(user, doc_id):
    documento_object_id, documento, proyecto, error = _load_activity_with_project(user, doc_id)
    if error:
        return error
    if (documento.get("status") or "new") == "finished":
        return jsonify({"message": "No se puede editar una actividad finalizada"}), 400

    data = request.get_json(silent=True) or {}
    update_data = {}
    if "descripcion" in data:
        update_data["descripcion"] = str(data.get("descripcion") or "").strip()
    objective_keys = (
        "specificObjectives",
        "objetivosEspecificos",
        "objetivos_especificos",
        "specificObjective",
        "objetivo_especifico",
    )
    if any(key in data for key in objective_keys):
        raw_objectives = next(data.get(key) for key in objective_keys if key in data)
        try:
            objetivos_especificos = _normalize_specific_objectives(raw_objectives)
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        objectives_error = _validate_activity_objectives(proyecto, objetivos_especificos)
        if objectives_error:
            return jsonify({"message": objectives_error}), 400
        update_data["objetivos_especificos"] = objetivos_especificos
        update_data["objetivo_especifico"] = objetivos_especificos[0] if objetivos_especificos else ""
    if "patrocinada" in data or "isSponsored" in data:
        update_data["patrocinada"] = _coerce_bool(_pick_mapping_value(data, "patrocinada", "isSponsored"))
    if ("monto" in data or "amount" in data) and not _activity_has_real_items(documento):
        update_data["monto"] = _amount_to_cents(_pick_mapping_value(data, "monto", "amount"))

    if not update_data:
        return jsonify({"message": "No hay campos válidos para actualizar"}), 400

    mongo.db.documentos.update_one({"_id": documento_object_id}, {"$set": update_data})
    return jsonify({"message": "Actividad actualizada con éxito"}), 200


@documents_bp.route("/documentos/<string:doc_id>/items", methods=["POST"])
@allow_cors
@token_required
def agregar_item_actividad(user, doc_id):
    documento_object_id, documento, proyecto, error = _load_activity_with_project(user, doc_id)
    if error:
        return error
    if (documento.get("status") or "new") == "finished":
        return jsonify({"message": "No se pueden agregar items a una actividad finalizada"}), 400

    data = request.get_json(silent=True) or {}
    item = _normalize_activity_item(data)
    if not item["nombre"]:
        return jsonify({"message": "El nombre del item es requerido"}), 400

    items = [dict(row) for row in (documento.get("items") or [])] if _activity_has_real_items(documento) else []
    items.append(item)
    new_status = _activity_status_for_items(items)
    mongo.db.documentos.update_one(
        {"_id": documento_object_id},
        {"$set": {"items": items, "monto": _activity_total_from_items(items), "status": new_status}},
    )
    return jsonify({"message": "Item agregado con éxito", "item": item}), 201


@documents_bp.route("/documentos/<string:doc_id>/items/<string:item_id>", methods=["PUT"])
@allow_cors
@token_required
def editar_item_actividad(user, doc_id, item_id):
    documento_object_id, documento, proyecto, error = _load_activity_with_project(user, doc_id)
    if error:
        return error
    if (documento.get("status") or "new") == "finished":
        return jsonify({"message": "No se pueden editar items de una actividad finalizada"}), 400
    if not _activity_has_real_items(documento):
        return jsonify({"message": "La actividad no tiene items administrativos configurados"}), 400

    data = request.get_json(silent=True) or {}
    items = [dict(row) for row in (documento.get("items") or [])]
    found = False
    for index, item in enumerate(items):
        if str(item.get("id")) != str(item_id):
            continue
        found = True
        if item.get("status") == ITEM_CLOSED_STATUS:
            return jsonify({"message": "No se puede editar un item cerrado administrativamente"}), 400
        items[index] = _normalize_activity_item(data, existing=item)
        if not items[index]["nombre"]:
            return jsonify({"message": "El nombre del item es requerido"}), 400
        break

    if not found:
        return jsonify({"message": "Item no encontrado"}), 404

    mongo.db.documentos.update_one(
        {"_id": documento_object_id},
        {"$set": {"items": items, "monto": _activity_total_from_items(items), "status": _activity_status_for_items(items)}},
    )
    return jsonify({"message": "Item actualizado con éxito"}), 200


@documents_bp.route("/documentos/<string:doc_id>/items/<string:item_id>", methods=["DELETE"])
@allow_cors
@token_required
def eliminar_item_actividad(user, doc_id, item_id):
    documento_object_id, documento, proyecto, error = _load_activity_with_project(user, doc_id)
    if error:
        return error
    if (documento.get("status") or "new") == "finished":
        return jsonify({"message": "No se pueden eliminar items de una actividad finalizada"}), 400
    if not _activity_has_real_items(documento):
        return jsonify({"message": "La actividad no tiene items administrativos configurados"}), 400

    items = [dict(row) for row in (documento.get("items") or [])]
    target = next((item for item in items if str(item.get("id")) == str(item_id)), None)
    if not target:
        return jsonify({"message": "Item no encontrado"}), 404
    if target.get("status") == ITEM_CLOSED_STATUS:
        return jsonify({"message": "No se puede eliminar un item cerrado administrativamente"}), 400

    next_items = [item for item in items if str(item.get("id")) != str(item_id)]
    mongo.db.documentos.update_one(
        {"_id": documento_object_id},
        {"$set": {"items": next_items, "monto": _activity_total_from_items(next_items), "status": _activity_status_for_items(next_items)}},
    )
    return jsonify({"message": "Item eliminado con éxito"}), 200


@documents_bp.route("/documentos/<string:doc_id>/items/<string:item_id>/cierre-administrativo", methods=["POST"])
@allow_cors
@token_required
def cerrar_item_actividad(user, doc_id, item_id):
    admin_access_error = _ensure_administrative_close_access(user)
    if admin_access_error:
        return admin_access_error

    documento_object_id, documento, proyecto, error = _load_activity_with_project(user, doc_id)
    if error:
        return error
    if (documento.get("status") or "new") == "finished":
        return jsonify({"error": "La actividad ya está finalizada"}), 400

    funding_year, funding_year_error = _resolve_activity_funding_year(proyecto)
    if funding_year_error:
        return funding_year_error

    updated_documento, close_error = _close_activity_items(
        documento,
        proyecto,
        user,
        funding_year,
        payload=request.get_json(silent=True) or {},
        item_id=item_id,
    )
    if close_error:
        return close_error
    return jsonify({"mensaje": "Item cerrado administrativamente", "status": updated_documento["status"]}), 201


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

    if not id or not doc_id:
        return jsonify({"error": "projectId y docId son requeridos"}), 400

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
    if current_status == "finished":
        return jsonify({"error": "La actividad ya está finalizada"}), 400

    if _activity_has_real_items(documento):
        if current_status not in {"new", "partial_admin_closed", "in_progress"}:
            return jsonify({"error": "La actividad no se encuentra en un estado válido para cierre administrativo"}), 400
        payload = {
            "monto": data_balance,
            "description": data_descripcion,
            "referencia": referencia,
            "transferAmount": monto_transferencia,
            "banco": banco,
            "accountCode": cuenta_contable,
        }
        updated_documento, close_error = _close_activity_items(
            documento,
            proyecto,
            user,
            funding_year,
            payload=payload,
        )
        if close_error:
            return close_error
        return jsonify({
            "mensaje": "Cierre administrativo de items registrado exitosamente",
            "status": updated_documento["status"],
            "year": funding_year,
        }), 201

    if current_status == "in_progress":
        return jsonify({"error": "La actividad ya tiene un cierre administrativo registrado"}), 400
    if current_status != "new":
        return jsonify({"error": "La actividad no se encuentra en un estado válido para cierre administrativo"}), 400
    if data_balance in (None, ""):
        return jsonify({"error": "monto es requerido"}), 400

    is_sponsored = _is_sponsored_activity(documento)
    if is_sponsored:
        cuenta_patrocinio = _resolve_sponsored_activity_account_code()
        if not cuenta_patrocinio:
            return jsonify({"error": "No hay una cuenta de patrocinio configurada para registrar esta actividad"}), 400
        data_balance = "0"
        monto_transferencia = "0"
        cuenta_contable = cuenta_patrocinio
    elif not cuenta_contable:
        return jsonify({"error": "accountCode es requerido"}), 400

    data_balance_int = string_to_int(data_balance)
    amount_units = round(data_balance_int / 100, 2)
    accounting_description = data_descripcion or documento.get("descripcion") or f"Consumo de actividad {doc_id}"

    actor_name = user.get("nombre", "Usuario")
    if not is_sponsored:
        try:
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
    else:
        agregar_log(
            id,
            (
                f'{actor_name} registro el cierre administrativo patrocinado de la actividad '
                f'{documento.get("descripcion", "")} usando la cuenta de referencia {cuenta_contable}'
            ),
        )

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
                "patrocinada": is_sponsored,
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
    if _activity_has_real_items(documento):
        items = documento.get("items") or []
        if not items or any(item.get("status") != ITEM_CLOSED_STATUS for item in items):
            return jsonify({"error": "Todos los items deben tener cierre administrativo antes de finalizar la actividad"}), 400

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
