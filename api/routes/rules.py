from flask import Blueprint, request, jsonify
from bson import ObjectId, json_util
import json
from datetime import datetime, timezone

from api.extensions import mongo
from api.util.decorators import token_required, allow_cors
from api.util.common import agregar_log
from api.util.utils import int_to_string, actualizar_pasos
from api.services.project_funding_service import ProjectFundingService
from api.util.access import can_access_project, parse_object_id

rules_bp = Blueprint('rules', __name__)


def _pick_value(data, *keys):
    for key in keys:
        if key in data and data.get(key) not in (None, ""):
            return data.get(key)
    return None


def _clean_str(value):
    return str(value or "").strip()


def _forbidden(message="No autorizado"):
    return jsonify({"message": message}), 403


def _ensure_project_access(user, project):
    if not can_access_project(user, project):
        return _forbidden("No autorizado para acceder a este proyecto")
    return None

@rules_bp.route("/crear_solicitud_regla_fija", methods=["POST"])
@token_required
def crear_solicitud_regla_fija(user):
    """
    Crear solicitud de regla fija
    ---
    tags:
      - Reglas de Distribución
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - name
            - items
          properties:
            name:
              type: string
              example: "Regla de Distribución 2024"
            items:
              type: array
              items:
                type: object
                properties:
                  nombre_regla:
                    type: string
                  monto:
                    type: number
    responses:
      200:
        description: Solicitud creada
    """
    data = request.get_json()
    solicitud_regla = {}
    items = data["items"]
    for item in items:
        item["monto"] = item["monto"] * 100

    solicitud_regla["nombre"] = data["name"]
    solicitud_regla["reglas"] = items
    solicitud_regla["status"] = "new"
    solicitud_regla["usuario"] = user
    mongo.db.solicitudes.insert_one(solicitud_regla)
    return jsonify({"message": "Solicitud de regla creada con éxito"}), 200

@rules_bp.route("/eliminar_solicitud_regla_fija/<string:id>", methods=["POST"])
@allow_cors
def eliminar_solicitud_regla_fija(id):
    """
    Eliminar solicitud de regla fija
    ---
    tags:
      - Reglas de Distribución
    parameters:
      - in: path
        name: id
        type: string
        required: true
    responses:
      200:
        description: Solicitud eliminada
      400:
        description: No se pudo eliminar
    """
    query = {"_id": ObjectId(id)}
    result = mongo.db.solicitudes.delete_one(query)
    if result.deleted_count == 1:
        return jsonify({"message": "Solicitud de regla eliminada con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar la regla"}), 400

@rules_bp.route("/completar_solicitud_regla_fija/<string:id>", methods=["POST"])
@allow_cors
def completar_solicitud_regla_fija(id):
    """
    Completar o rechazar solicitud de regla
    ---
    tags:
      - Reglas de Distribución
    parameters:
      - in: path
        name: id
        type: string
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - resolution
          properties:
            resolution:
              type: string
              enum: ["completed", "rejected"]
    responses:
      200:
        description: Solicitud actualizada
      400:
        description: No se pudo actualizar
    """
    data = request.get_json()
    resolution = data["resolution"]
    query = {"_id": ObjectId(id)}
    result = mongo.db.solicitudes.update_one(query, {"$set": {"status": resolution}})
    if result.modified_count == 1:
        return jsonify({"message": "Solicitud de regla eliminada con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar la regla"}), 400

@rules_bp.route("/mostrar_reglas_fijas", methods=["GET"])
@allow_cors
@token_required
def mostrar_reglas_fijas(user):
    """
    Listar reglas fijas completadas
    ---
    tags:
      - Reglas de Distribución
    security:
      - Bearer: []
    responses:
      200:
        description: Lista de reglas
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
                  nombre:
                    type: string
                  reglas:
                    type: array
                  status:
                    type: string
    """
    list_request = mongo.db.solicitudes.find({"status": "completed"})
    list_cursor = list(list_request)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    return jsonify(request_list=list_json), 200

@rules_bp.route("/asignar_regla_fija/", methods=["POST"])
@allow_cors
@token_required
def asignar_regla_fija(user):
    """
    Asignar regla fija a proyecto
    ---
    tags:
      - Reglas de Distribución
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - proyecto_id
            - regla_id
          properties:
            proyecto_id:
              type: string
            regla_id:
              type: string
    responses:
      200:
        description: Regla asignada
      400:
        description: Proyecto sin balance
      404:
        description: Proyecto o regla no encontrado
    """
    data = request.get_json(silent=True) or {}
    proyecto_id = _pick_value(data, "projectId", "project_id", "proyecto_id")
    regla_id = _pick_value(data, "ruleId", "rule_id", "regla_id")
    if not proyecto_id or not regla_id:
        return jsonify({"message": "projectId y ruleId son requeridos"}), 400

    proyecto_object_id = parse_object_id(proyecto_id)
    if not proyecto_object_id:
        return jsonify({"message": "projectId inválido"}), 400

    regla_object_id = parse_object_id(regla_id)
    if not regla_object_id:
        return jsonify({"message": "ruleId inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": proyecto_object_id})
    if proyecto is None:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    regla = mongo.db.solicitudes.find_one({"_id": regla_object_id})
    if regla is None:
        return jsonify({"message": "Regla fija no encontrada"}), 404
    account_mappings = data.get("accountMappings") or []
    mapping_by_index = {
        int(item.get("itemIndex")): _pick_value(item, "accountCode", "account_code")
        for item in account_mappings
        if _pick_value(item, "accountCode", "account_code") not in (None, "")
    }

    grouped_amounts = {}
    normalized_items = []
    for idx, item in enumerate(regla["reglas"]):
        account_code = _clean_str(mapping_by_index.get(idx) or item.get("accountCode") or item.get("cuenta_contable"))
        if not account_code:
            return jsonify({"message": "Cada item de la regla fija requiere una partida asociada"}), 400
        amount_units = round(float(item.get("monto", 0) or 0) / 100, 2)
        grouped_amounts[account_code] = round(grouped_amounts.get(account_code, 0) + amount_units, 2)
        normalized_items.append({"item": item, "accountCode": account_code, "amountUnits": amount_units})

    for account_code, total_amount in grouped_amounts.items():
        balance = ProjectFundingService._project_balance_for_account(proyecto_id, account_code, year=2025)
        if (balance - total_amount) < 0:
            return jsonify({"message": f"Saldo insuficiente en la partida {account_code} para aplicar la regla fija"}), 400

    for item_data in normalized_items:
        item = item_data["item"]
        account_code = item_data["accountCode"]
        amount_units = item_data["amountUnits"]
        try:
            ProjectFundingService.consume_project_account(
                proyecto,
                year=2025,
                account_code=account_code,
                amount=amount_units,
                user=user,
                description=f"Regla fija {regla['nombre']} - {item['nombre_regla']}",
                reference={
                    "kind": "fixed_rule",
                    "ruleId": str(regla_object_id),
                    "projectId": str(proyecto_object_id),
                    "actorName": user.get("nombre", "Usuario"),
                    "title": "Consumo por regla fija",
                    "accountCode": account_code,
                    "ruleName": regla["nombre"],
                    "ruleItemName": item["nombre_regla"],
                },
                allow_negative=False,
                log_message=(
                    f'{user["nombre"]} asigno la regla {regla["nombre"]} con el item {item["nombre_regla"]} '
                    f'en la partida {account_code} por Bs. {int_to_string(item["monto"])}'
                ),
            )
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400

    new_status, _ = actualizar_pasos(proyecto["status"], 5)

    mongo.db.proyectos.update_one(
        {"_id": proyecto_object_id},
        {"$set": {"regla_fija": {**regla, "accountMappings": account_mappings}, "status": new_status}},
    )

    return jsonify({"message": "La regla se asigno correctamente"}), 200

@rules_bp.route("/mostrar_solicitudes", methods=["GET"])
@allow_cors
@token_required
def mostrar_solicitudes(user):
    """
    Listar solicitudes con paginación
    ---
    tags:
      - Reglas de Distribución
    security:
      - Bearer: []
    parameters:
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
        description: Lista de solicitudes
        schema:
          type: object
          properties:
            request_list:
              type: array
            count:
              type: integer
    """
    params = request.args
    skip = int(params.get("page")) if params.get("page") else 0
    limit = int(params.get("limit")) if params.get("limit") else 10
    
    list_verification_request = mongo.db.solicitudes.find({}).skip(skip * limit).limit(limit)
    quantity = mongo.db.solicitudes.count_documents({})
    list_cursor = list(list_verification_request)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    return jsonify(request_list=list_json, count=quantity)
