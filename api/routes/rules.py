from flask import Blueprint, request, jsonify
from bson import ObjectId, json_util
import json
from datetime import datetime, timezone

from api.extensions import mongo
from api.util.decorators import token_required, allow_cors
from api.util.common import agregar_log
from api.util.utils import int_to_string, actualizar_pasos

rules_bp = Blueprint('rules', __name__)

@rules_bp.route("/crear_solicitud_regla_fija", methods=["POST"])
@token_required
def crear_solicitud_regla_fija(user):
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
    query = {"_id": ObjectId(id)}
    result = mongo.db.solicitudes.delete_one(query)
    if result.deleted_count == 1:
        return jsonify({"message": "Solicitud de regla eliminada con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar la regla"}), 400

@rules_bp.route("/completar_solicitud_regla_fija/<string:id>", methods=["POST"])
@allow_cors
def completar_solicitud_regla_fija(id):
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
    list_request = mongo.db.solicitudes.find({"status": "completed"})
    list_cursor = list(list_request)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    return jsonify(request_list=list_json), 200

@rules_bp.route("/asignar_regla_fija/", methods=["POST"])
@allow_cors
@token_required
def asignar_regla_fija(user):
    data = request.get_json()
    proyecto_id = data["proyecto_id"]
    regla_id = data["regla_id"]

    proyecto = mongo.db.proyectos.find_one({"_id": ObjectId(proyecto_id)})
    if proyecto is None:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    regla = mongo.db.solicitudes.find_one({"_id": ObjectId(regla_id)})
    if regla is None:
        return jsonify({"message": "Regla fija no encontrada"}), 404

    if proyecto["balance_inicial"] == 0:
        return jsonify(
            {"message": "Antes de asignar regla tienes que asignar balance"}
        ), 400

    balance = int(proyecto["balance"])

    for x in regla["reglas"]:
        balance -= x["monto"]
        mongo.db.proyectos.update_one(
            {"_id": ObjectId(proyecto_id)}, {"$set": {"balance": balance}}
        )

        data_acciones = {
            "project_id": ObjectId(proyecto_id),
            "user": user["nombre"],
            "type": x["nombre_regla"],
            "amount": x["monto"] * -1,
            "total_amount": balance,
            "created_at": datetime.utcnow()
        }
        mongo.db.acciones.insert_one(data_acciones)
        message_log = f'{user["nombre"]} asigno la regla: {regla["nombre"]} con el item {x["nombre_regla"]} con un monto de ${int_to_string(x["monto"])}'
        agregar_log(proyecto_id, message_log)

    new_status, _ = actualizar_pasos(proyecto["status"], 5)

    mongo.db.proyectos.update_one(
        {"_id": ObjectId(proyecto_id)},
        {"$set": {"regla_fija": regla, "status": new_status}},
    )

    return jsonify({"message": "La regla se asigno correctamente"}), 200

@rules_bp.route("/mostrar_solicitudes", methods=["GET"])
@allow_cors
@token_required
def mostrar_solicitudes(user):
    params = request.args
    skip = int(params.get("page")) if params.get("page") else 0
    limit = int(params.get("limit")) if params.get("limit") else 10
    
    list_verification_request = mongo.db.solicitudes.find({}).skip(skip * limit).limit(limit)
    quantity = mongo.db.solicitudes.count_documents({})
    list_cursor = list(list_verification_request)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    return jsonify(request_list=list_json, count=quantity)
