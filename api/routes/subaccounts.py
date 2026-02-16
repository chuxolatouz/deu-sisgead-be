from flask import Blueprint, request, jsonify
from bson import ObjectId, json_util
import json
from datetime import datetime, timezone
from api.extensions import mongo
from api.util.decorators import token_required, allow_cors, validar_datos

subaccounts_bp = Blueprint('subaccounts', __name__)

@subaccounts_bp.route("/subaccounts", methods=["POST"])
@allow_cors
#@token_required
@validar_datos({"accountId": str, "departmentId": str, "balanceBs": (int, float)})
#def crear_subcuenta(user):
def crear_subcuenta():
    """
    Crear nueva subcuenta
    ---
    tags:
      - Subcuentas
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - accountId
            - departmentId
            - balanceBs
          properties:
            accountId:
              type: string
              description: ID de la cuenta principal
              example: "507f1f77bcf86cd799439011"
            departmentId:
              type: string
              description: ID del departamento
              example: "507f1f77bcf86cd799439012"
            balanceBs:
              type: number
              description: Saldo inicial en Bolívares
              example: 1000.50
            active:
              type: boolean
              description: Estado de la subcuenta
              example: true
    responses:
      201:
        description: Subcuenta creada exitosamente
      400:
        description: Datos inválidos o balance negativo
    """
    data = request.get_json()
    
    # Validar que el balance no sea negativo
    if data["balanceBs"] < 0:
        return jsonify({"message": "El balance no puede ser negativo"}), 400
    
    # Verificar que la cuenta existe
    try:
        account_id_obj = ObjectId(data["accountId"].strip())
        cuenta = mongo.db.cuentas.find_one({"_id": account_id_obj})
        if not cuenta:
            return jsonify({"message": "Cuenta no encontrada"}), 404
    except Exception:
        return jsonify({"message": "ID de cuenta inválido"}), 400
    
    # Verificar que el departamento existe
    try:
        department_id_obj = ObjectId(data["departmentId"].strip())
        departamento = mongo.db.departamentos.find_one({"_id": department_id_obj})
        if not departamento:
            return jsonify({"message": "Departamento no encontrado"}), 404
    except Exception:
        return jsonify({"message": "ID de departamento inválido"}), 400
    
    # Verificar que no exista una subcuenta con la misma combinación
    subcuenta_existente = mongo.db.subaccounts.find_one({
        "accountId": account_id_obj,
        "departmentId": department_id_obj
    })
    
    if subcuenta_existente:
        return jsonify({"message": "Ya existe una subcuenta para esta cuenta y departamento"}), 400
    
    subcuenta = {
        "accountId": account_id_obj,
        "departmentId": department_id_obj,
        "balanceBs": float(data["balanceBs"]),
        "active": data.get("active", True),
        "fecha_creacion": datetime.now(timezone.utc),
        "fecha_actualizacion": datetime.now(timezone.utc)
    }
    
    subcuenta_insertada = mongo.db.subaccounts.insert_one(subcuenta)
    return jsonify({"message": "Subcuenta creada con éxito", "_id": str(subcuenta_insertada.inserted_id)}), 201

@subaccounts_bp.route("/subaccounts", methods=["GET"])
@allow_cors
def listar_subcuentas():
    """
    Listar subcuentas con filtrado
    ---
    tags:
      - Subcuentas
    parameters:
      - in: query
        name: departmentId
        type: string
        description: Filtrar por ID de departamento
      - in: query
        name: accountId
        type: string
        description: Filtrar por ID de cuenta
      - in: query
        name: active
        type: boolean
        description: Filtrar por estado activo/inactivo
    responses:
      200:
        description: Lista de subcuentas
    """
    params = request.args
    query = {}
    
    if params.get("departmentId"):
        try:
            query["departmentId"] = ObjectId(params.get("departmentId").strip())
        except Exception:
            return jsonify({"message": "ID de departamento inválido"}), 400
    
    if params.get("accountId"):
        try:
            query["accountId"] = ObjectId(params.get("accountId").strip())
        except Exception:
            return jsonify({"message": "ID de cuenta inválido"}), 400
    
    if params.get("active") is not None:
        query["active"] = params.get("active").lower() == "true"
    
    subcuentas = mongo.db.subaccounts.find(query)
    list_cursor = list(subcuentas)
    
    # Primero convertimos a JSON
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump)
    
    # Ahora agregamos la información de cuenta y departamento
    for subcuenta in list_json:
        # Obtener información de la cuenta - NOTA: subcuenta["accountId"] ya es un string
        try:
            cuenta = mongo.db.cuentas.find_one({"_id": ObjectId(subcuenta["accountId"])})
            if cuenta:
                # Convertir cuenta a dict para manipularlo
                cuenta_dict = json_util.loads(json_util.dumps(cuenta))
                subcuenta["cuenta"] = {
                    "_id": str(cuenta_dict["_id"]),
                    "nombre": cuenta_dict.get("nombre", ""),
                    "codigo": cuenta_dict.get("codigo", ""),
                    "tipo": cuenta_dict.get("tipo", "")
                }
        except Exception as e:
            print(f"Error al obtener cuenta: {e}")
            subcuenta["cuenta"] = {}
        
        # Obtener información del departamento
        try:
            departamento = mongo.db.departamentos.find_one({"_id": ObjectId(subcuenta["departmentId"])})
            if departamento:
                # Convertir departamento a dict para manipularlo
                depto_dict = json_util.loads(json_util.dumps(departamento))
                subcuenta["departamento"] = {
                    "_id": str(depto_dict["_id"]),
                    "nombre": depto_dict.get("nombre", ""),
                    "codigo": depto_dict.get("codigo", "")
                }
        except Exception as e:
            print(f"Error al obtener departamento: {e}")
            subcuenta["departamento"] = {}
    
    return jsonify(list_json), 200

@subaccounts_bp.route("/subaccounts/<string:subaccount_id>", methods=["GET"])
@allow_cors
def obtener_subcuenta(subaccount_id):
    """
    Obtener subcuenta por ID
    ---
    tags:
      - Subcuentas
    parameters:
      - in: path
        name: subaccount_id
        type: string
        required: true
        description: ID de la subcuenta
    responses:
      200:
        description: Subcuenta encontrada
      404:
        description: Subcuenta no encontrada
    """
    try:
        subaccount_id_obj = ObjectId(subaccount_id.strip())
    except Exception:
        return jsonify({"message": "ID de subcuenta inválido"}), 400
    
    subcuenta = mongo.db.subaccounts.find_one({"_id": subaccount_id_obj})
    
    if not subcuenta:
        return jsonify({"message": "Subcuenta no encontrada"}), 404
    
    subcuenta["_id"] = str(subcuenta["_id"])
    subcuenta["accountId"] = str(subcuenta["accountId"])
    subcuenta["departmentId"] = str(subcuenta["departmentId"])
    
    # Agregar información de cuenta y departamento
    cuenta = mongo.db.cuentas.find_one({"_id": ObjectId(subcuenta["accountId"])})
    if cuenta:
        subcuenta["cuenta"] = {
            "_id": str(cuenta["_id"]),
            "nombre": cuenta.get("nombre", ""),
            "codigo": cuenta.get("codigo", ""),
            "tipo": cuenta.get("tipo", "")
        }
    
    departamento = mongo.db.departamentos.find_one({"_id": ObjectId(subcuenta["departmentId"])})
    if departamento:
        subcuenta["departamento"] = {
            "_id": str(departamento["_id"]),
            "nombre": departamento.get("nombre", ""),
            "codigo": departamento.get("codigo", "")
        }
    
    subcuenta_dump = json.dumps(subcuenta, default=json_util.default, ensure_ascii=False)
    subcuenta_json = json.loads(subcuenta_dump.replace("\\", ""))
    
    return jsonify(subcuenta_json), 200

@subaccounts_bp.route("/subaccounts/<string:subaccount_id>", methods=["PUT"])
@allow_cors
@token_required
def actualizar_subcuenta(user, subaccount_id):
    """
    Actualizar subcuenta
    ---
    tags:
      - Subcuentas
    security:
      - Bearer: []
    parameters:
      - in: path
        name: subaccount_id
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          properties:
            balanceBs:
              type: number
              description: Nuevo saldo en Bolívares
            active:
              type: boolean
              description: Estado de la subcuenta
    responses:
      200:
        description: Subcuenta actualizada
      400:
        description: Balance negativo no permitido
      404:
        description: Subcuenta no encontrada
    """
    data = request.get_json()
    
    try:
        subaccount_id_obj = ObjectId(subaccount_id.strip())
    except Exception:
        return jsonify({"message": "ID de subcuenta inválido"}), 400
    
    subcuenta = mongo.db.subaccounts.find_one({"_id": subaccount_id_obj})
    if not subcuenta:
        return jsonify({"message": "Subcuenta no encontrada"}), 404
    
    update_data = {"fecha_actualizacion": datetime.now(timezone.utc)}
    
    if "balanceBs" in data:
        if data["balanceBs"] < 0:
            return jsonify({"message": "El balance no puede ser negativo"}), 400
        update_data["balanceBs"] = float(data["balanceBs"])
    
    if "active" in data:
        update_data["active"] = data["active"]
    
    mongo.db.subaccounts.update_one({"_id": subaccount_id_obj}, {"$set": update_data})
    return jsonify({"message": "Subcuenta actualizada con éxito"}), 200

@subaccounts_bp.route("/subaccounts/<string:subaccount_id>", methods=["DELETE"])
@allow_cors
@token_required
def eliminar_subcuenta(user, subaccount_id):
    """
    Eliminar subcuenta
    ---
    tags:
      - Subcuentas
    security:
      - Bearer: []
    parameters:
      - in: path
        name: subaccount_id
        type: string
        required: true
    responses:
      200:
        description: Subcuenta eliminada
      404:
        description: Subcuenta no encontrada
    """
    try:
        subaccount_id_obj = ObjectId(subaccount_id.strip())
    except Exception:
        return jsonify({"message": "ID de subcuenta inválido"}), 400
    
    subcuenta = mongo.db.subaccounts.find_one({"_id": subaccount_id_obj})
    if not subcuenta:
        return jsonify({"message": "Subcuenta no encontrada"}), 404
    
    result = mongo.db.subaccounts.delete_one({"_id": subaccount_id_obj})
    if result.deleted_count == 1:
        return jsonify({"message": "Subcuenta eliminada con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar la subcuenta"}), 400

@subaccounts_bp.route("/subaccounts/create-index", methods=["POST"])
@allow_cors
@token_required
def crear_indice_unico(user):
    """
    Crear índice único para (accountId, departmentId)
    ---
    tags:
      - Subcuentas
    security:
      - Bearer: []
    description: Crea un índice único para evitar duplicados de cuenta-departamento
    responses:
      200:
        description: Índice creado exitosamente
      500:
        description: Error al crear el índice
    """
    try:
        # Crear índice único compuesto
        result = mongo.db.subaccounts.create_index(
            [("accountId", 1), ("departmentId", 1)],
            unique=True,
            name="idx_account_department_unique"
        )
        return jsonify({
            "message": "Índice único creado exitosamente",
            "index_name": "idx_account_department_unique"
        }), 200
    except Exception as e:
        return jsonify({
            "message": "Error al crear el índice",
            "error": str(e)
        }), 500
