from flask import Blueprint, request, jsonify
from bson import ObjectId, json_util
import json
from datetime import datetime, timezone
from api.extensions import mongo
from api.util.decorators import token_required, allow_cors, validar_datos

departments_bp = Blueprint('departments', __name__)

@departments_bp.route("/departamentos", methods=["POST"])
@allow_cors
@token_required
@validar_datos({"nombre": str, "descripcion": str, "codigo": str})
def crear_departamento(user):
    data = request.get_json()
    departamento = {
        "nombre": data["nombre"],
        "descripcion": data["descripcion"],
        "codigo": data["codigo"],
        "fecha_creacion": datetime.now(timezone.utc),
        "activo": True
    }
    departamento_insertado = mongo.db.departamentos.insert_one(departamento)
    return jsonify({"message": "Departamento creado con éxito", "_id": str(departamento_insertado.inserted_id)}), 201

@departments_bp.route("/departamentos", methods=["GET"])
@allow_cors
def listar_departamentos():
    params = request.args
    query = {}
    if params.get("activo") is not None:
        query["activo"] = params.get("activo").lower() == "true"
    
    departamentos = mongo.db.departamentos.find(query)
    list_cursor = list(departamentos)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    return jsonify(list_json), 200

@departments_bp.route("/departamentos/<string:departamento_id>", methods=["GET"])
@allow_cors
def obtener_departamento(departamento_id):
    try:
        departamento_id_obj = ObjectId(departamento_id.strip())
    except Exception:
        return jsonify({"message": "ID de departamento inválido"}), 400
    
    departamento = mongo.db.departamentos.find_one({"_id": departamento_id_obj})
    
    if not departamento:
        return jsonify({"message": "Departamento no encontrado"}), 404
    
    departamento["_id"] = str(departamento["_id"])
    departamento_dump = json.dumps(departamento, default=json_util.default, ensure_ascii=False)
    departamento_json = json.loads(departamento_dump.replace("\\", ""))
    
    return jsonify(departamento_json), 200

@departments_bp.route("/departamentos/<string:departamento_id>", methods=["PUT"])
@allow_cors
@token_required
def actualizar_departamento(user, departamento_id):
    data = request.get_json()
    departamento = mongo.db.departamentos.find_one({"_id": ObjectId(departamento_id)})
    if not departamento:
        return jsonify({"message": "Departamento no encontrado"}), 404
    
    update_data = {}
    if "nombre" in data:
        update_data["nombre"] = data["nombre"]
    if "descripcion" in data:
        update_data["descripcion"] = data["descripcion"]
    if "codigo" in data:
        update_data["codigo"] = data["codigo"]
    if "activo" in data:
        update_data["activo"] = data["activo"]
    
    mongo.db.departamentos.update_one({"_id": ObjectId(departamento_id)}, {"$set": update_data})
    return jsonify({"message": "Departamento actualizado con éxito"}), 200

@departments_bp.route("/departamentos/<string:departamento_id>", methods=["DELETE"])
@allow_cors
@token_required
def eliminar_departamento(user, departamento_id):
    departamento = mongo.db.departamentos.find_one({"_id": ObjectId(departamento_id)})
    if not departamento:
        return jsonify({"message": "Departamento no encontrado"}), 404
    
    result = mongo.db.departamentos.delete_one({"_id": ObjectId(departamento_id)})
    if result.deleted_count == 1:
        return jsonify({"message": "Departamento eliminado con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar el departamento"}), 400

@departments_bp.route("/contexto_departamento", methods=["GET"])
@allow_cors
@token_required
def obtener_contexto_departamento(user):
    if user.get("role") != "super_admin":
        return jsonify({"message": "Solo super_admin puede usar este endpoint"}), 403
    
    dept_context = request.headers.get("X-Department-Context")
    usando_contexto = False
    departamento = None
    
    if dept_context:
        try:
            dept_id_obj = ObjectId(dept_context.strip())
            dept = mongo.db.departamentos.find_one({"_id": dept_id_obj})
            if dept:
                usando_contexto = True
                departamento = {
                    "_id": str(dept["_id"]),
                    "nombre": dept.get("nombre", ""),
                    "descripcion": dept.get("descripcion", ""),
                    "codigo": dept.get("codigo", ""),
                }
        except Exception:
            pass
    
    return jsonify({
        "departamento_id": dept_context.strip() if dept_context and usando_contexto else None,
        "usando_contexto": usando_contexto,
        "departamento": departamento
    }), 200
