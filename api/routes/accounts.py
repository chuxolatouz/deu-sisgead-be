from flask import Blueprint, request, jsonify
from bson import ObjectId, json_util
import json
from datetime import datetime, timezone
from api.extensions import mongo
from api.util.decorators import token_required, allow_cors, validar_datos

accounts_bp = Blueprint('accounts', __name__)

@accounts_bp.route("/cuentas", methods=["POST"])
@allow_cors
#@token_required
@validar_datos({"nombre": str, "codigo": str, "tipo": str})
#def crear_cuenta(user):
def crear_cuenta():
    """
    Crear nueva cuenta
    ---
    tags:
      - Cuentas
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - nombre
            - codigo
            - tipo
          properties:
            nombre:
              type: string
              description: Nombre de la cuenta
              example: "Caja Bancaria"
            codigo:
              type: string
              description: Código único de la cuenta
              example: "CAJA-001"
            tipo:
              type: string
              description: Tipo de cuenta (activo, pasivo, patrimonio, ingreso, gasto)
              example: "activo"
            descripcion:
              type: string
              description: Descripción opcional de la cuenta
              example: "Cuenta principal para movimientos bancarios"
    responses:
      201:
        description: Cuenta creada exitosamente
      400:
        description: Datos inválidos
    """
    data = request.get_json()
    cuenta = {
        "nombre": data["nombre"],
        "codigo": data["codigo"],
        "tipo": data["tipo"],
        "descripcion": data.get("descripcion", ""),
        "fecha_creacion": datetime.now(timezone.utc),
        "activo": True
    }
    cuenta_insertada = mongo.db.cuentas.insert_one(cuenta)
    return jsonify({"message": "Cuenta creada con éxito", "_id": str(cuenta_insertada.inserted_id)}), 201

@accounts_bp.route("/cuentas", methods=["GET"])
@allow_cors
def listar_cuentas():
    """
    Listar todas las cuentas
    ---
    tags:
      - Cuentas
    parameters:
      - in: query
        name: activo
        type: boolean
        description: Filtrar por estado activo/inactivo
      - in: query
        name: tipo
        type: string
        description: Filtrar por tipo de cuenta
    responses:
      200:
        description: Lista de cuentas
    """
    params = request.args
    query = {}
    if params.get("activo") is not None:
        query["activo"] = params.get("activo").lower() == "true"
    if params.get("tipo"):
        query["tipo"] = params.get("tipo")
    
    cuentas = mongo.db.cuentas.find(query)
    list_cursor = list(cuentas)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    return jsonify(list_json), 200

@accounts_bp.route("/cuentas/<string:cuenta_id>", methods=["GET"])
@allow_cors
def obtener_cuenta(cuenta_id):
    """
    Obtener cuenta por ID
    ---
    tags:
      - Cuentas
    parameters:
      - in: path
        name: cuenta_id
        type: string
        required: true
        description: ID de la cuenta
    responses:
      200:
        description: Cuenta encontrada
      404:
        description: Cuenta no encontrada
    """
    try:
        cuenta_id_obj = ObjectId(cuenta_id.strip())
    except Exception:
        return jsonify({"message": "ID de cuenta inválido"}), 400
    
    cuenta = mongo.db.cuentas.find_one({"_id": cuenta_id_obj})
    
    if not cuenta:
        return jsonify({"message": "Cuenta no encontrada"}), 404
    
    cuenta["_id"] = str(cuenta["_id"])
    cuenta_dump = json.dumps(cuenta, default=json_util.default, ensure_ascii=False)
    cuenta_json = json.loads(cuenta_dump.replace("\\", ""))
    
    return jsonify(cuenta_json), 200

@accounts_bp.route("/cuentas/<string:cuenta_id>", methods=["PUT"])
@allow_cors
@token_required
def actualizar_cuenta(user, cuenta_id):
    """
    Actualizar cuenta
    ---
    tags:
      - Cuentas
    security:
      - Bearer: []
    parameters:
      - in: path
        name: cuenta_id
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          properties:
            nombre:
              type: string
            codigo:
              type: string
            tipo:
              type: string
            descripcion:
              type: string
            activo:
              type: boolean
    responses:
      200:
        description: Cuenta actualizada
      404:
        description: Cuenta no encontrada
    """
    data = request.get_json()
    cuenta = mongo.db.cuentas.find_one({"_id": ObjectId(cuenta_id)})
    if not cuenta:
        return jsonify({"message": "Cuenta no encontrada"}), 404
    
    update_data = {}
    if "nombre" in data:
        update_data["nombre"] = data["nombre"]
    if "codigo" in data:
        update_data["codigo"] = data["codigo"]
    if "tipo" in data:
        update_data["tipo"] = data["tipo"]
    if "descripcion" in data:
        update_data["descripcion"] = data["descripcion"]
    if "activo" in data:
        update_data["activo"] = data["activo"]
    
    mongo.db.cuentas.update_one({"_id": ObjectId(cuenta_id)}, {"$set": update_data})
    return jsonify({"message": "Cuenta actualizada con éxito"}), 200

@accounts_bp.route("/cuentas/<string:cuenta_id>", methods=["DELETE"])
@allow_cors
@token_required
def eliminar_cuenta(user, cuenta_id):
    """
    Eliminar cuenta
    ---
    tags:
      - Cuentas
    security:
      - Bearer: []
    parameters:
      - in: path
        name: cuenta_id
        type: string
        required: true
    responses:
      200:
        description: Cuenta eliminada
      404:
        description: Cuenta no encontrada
    """
    cuenta = mongo.db.cuentas.find_one({"_id": ObjectId(cuenta_id)})
    if not cuenta:
        return jsonify({"message": "Cuenta no encontrada"}), 404
    
    result = mongo.db.cuentas.delete_one({"_id": ObjectId(cuenta_id)})
    if result.deleted_count == 1:
        return jsonify({"message": "Cuenta eliminada con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar la cuenta"}), 400
