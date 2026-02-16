from flask import Blueprint, request, jsonify
from bson import ObjectId, json_util
import json
from datetime import datetime, timezone
from api.extensions import mongo
from api.util.decorators import token_required, allow_cors, validar_datos

account_movements_bp = Blueprint('account_movements', __name__)

@account_movements_bp.route("/account-movements", methods=["POST"])
@allow_cors
@token_required
@validar_datos({"type": str, "amountBs": (int, float), "createdBy": str})
def crear_movimiento(user):
    """
    Crear nuevo movimiento de cuenta
    ---
    tags:
      - Movimientos de Cuenta
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - type
            - amountBs
            - createdBy
          properties:
            type:
              type: string
              description: Tipo de movimiento (credit, debit, transfer)
              example: "credit"
            amountBs:
              type: number
              description: Monto del movimiento (debe ser mayor a 0)
              example: 500.00
            fromSubaccountId:
              type: string
              description: ID de subcuenta origen (requerido para transfer y debit)
              example: "507f1f77bcf86cd799439011"
            toSubaccountId:
              type: string
              description: ID de subcuenta destino (requerido para transfer y credit)
              example: "507f1f77bcf86cd799439012"
            createdBy:
              type: string
              description: ID del usuario que crea el movimiento
              example: "507f1f77bcf86cd799439013"
            metadata:
              type: object
              description: Información adicional del movimiento
              example: {"description": "Pago de servicios", "reference": "REF-001"}
    responses:
      201:
        description: Movimiento creado exitosamente
      400:
        description: Datos inválidos o monto no válido
    """
    data = request.get_json()
    
    # Validar que el monto sea mayor a 0
    if data["amountBs"] <= 0:
        return jsonify({"message": "El monto debe ser mayor a 0"}), 400
    
    # Validar tipo de movimiento
    valid_types = ["credit", "debit", "transfer"]
    if data["type"] not in valid_types:
        return jsonify({"message": f"Tipo de movimiento inválido. Debe ser: {', '.join(valid_types)}"}), 400
    
    # Validar campos requeridos según tipo
    if data["type"] in ["debit", "transfer"] and not data.get("fromSubaccountId"):
        return jsonify({"message": "fromSubaccountId es requerido para movimientos de tipo debit o transfer"}), 400
    
    if data["type"] in ["credit", "transfer"] and not data.get("toSubaccountId"):
        return jsonify({"message": "toSubaccountId es requerido para movimientos de tipo credit o transfer"}), 400
    
    # Verificar que las subcuentas existen
    if data.get("fromSubaccountId"):
        try:
            from_subaccount_id = ObjectId(data["fromSubaccountId"].strip())
            from_subaccount = mongo.db.subaccounts.find_one({"_id": from_subaccount_id})
            if not from_subaccount:
                return jsonify({"message": "Subcuenta origen no encontrada"}), 404
        except Exception:
            return jsonify({"message": "ID de subcuenta origen inválido"}), 400
    else:
        from_subaccount_id = None
    
    if data.get("toSubaccountId"):
        try:
            to_subaccount_id = ObjectId(data["toSubaccountId"].strip())
            to_subaccount = mongo.db.subaccounts.find_one({"_id": to_subaccount_id})
            if not to_subaccount:
                return jsonify({"message": "Subcuenta destino no encontrada"}), 404
        except Exception:
            return jsonify({"message": "ID de subcuenta destino inválido"}), 400
    else:
        to_subaccount_id = None
    
    # Verificar que el usuario existe
    try:
        created_by_id = ObjectId(data["createdBy"].strip())
        usuario = mongo.db.usuarios.find_one({"_id": created_by_id})
        if not usuario:
            return jsonify({"message": "Usuario no encontrado"}), 404
    except Exception:
        return jsonify({"message": "ID de usuario inválido"}), 400
    
    # Crear movimiento
    movimiento = {
        "type": data["type"],
        "amountBs": float(data["amountBs"]),
        "fromSubaccountId": from_subaccount_id,
        "toSubaccountId": to_subaccount_id,
        "createdBy": created_by_id,
        "createdAt": datetime.now(timezone.utc),
        "metadata": data.get("metadata", {})
    }
    
    # Actualizar saldos de subcuentas
    if data["type"] == "debit" and from_subaccount_id:
        # Verificar saldo suficiente
        from_subaccount = mongo.db.subaccounts.find_one({"_id": from_subaccount_id})
        if from_subaccount["balanceBs"] < data["amountBs"]:
            return jsonify({"message": "Saldo insuficiente en la subcuenta origen"}), 400
        
        # Restar del saldo origen
        mongo.db.subaccounts.update_one(
            {"_id": from_subaccount_id},
            {"$inc": {"balanceBs": -data["amountBs"], "fecha_actualizacion": datetime.now(timezone.utc)}}
        )
    
    elif data["type"] == "credit" and to_subaccount_id:
        # Sumar al saldo destino
        mongo.db.subaccounts.update_one(
            {"_id": to_subaccount_id},
            {"$inc": {"balanceBs": data["amountBs"], "fecha_actualizacion": datetime.now(timezone.utc)}}
        )
    
    elif data["type"] == "transfer":
        # Verificar saldo suficiente
        from_subaccount = mongo.db.subaccounts.find_one({"_id": from_subaccount_id})
        if from_subaccount["balanceBs"] < data["amountBs"]:
            return jsonify({"message": "Saldo insuficiente en la subcuenta origen"}), 400
        
        # Restar del origen y sumar al destino
        mongo.db.subaccounts.update_many(
            [
                {"_id": from_subaccount_id},
                {"_id": to_subaccount_id}
            ],
            [
                {"$inc": {"balanceBs": -data["amountBs"], "fecha_actualizacion": datetime.now(timezone.utc)}},
                {"$inc": {"balanceBs": data["amountBs"], "fecha_actualizacion": datetime.now(timezone.utc)}}
            ]
        )
    
    movimiento_insertado = mongo.db.account_movements.insert_one(movimiento)
    return jsonify({
        "message": "Movimiento creado con éxito", 
        "_id": str(movimiento_insertado.inserted_id)
    }), 201

@account_movements_bp.route("/account-movements", methods=["GET"])
@allow_cors
def listar_movimientos():
    """
    Listar movimientos de cuenta
    ---
    tags:
      - Movimientos de Cuenta
    parameters:
      - in: query
        name: fromSubaccountId
        type: string
        description: Filtrar por ID de subcuenta origen
      - in: query
        name: toSubaccountId
        type: string
        description: Filtrar por ID de subcuenta destino
      - in: query
        name: type
        type: string
        description: Filtrar por tipo de movimiento
      - in: query
        name: startDate
        type: string
        description: Fecha de inicio (YYYY-MM-DD)
      - in: query
        name: endDate
        type: string
        description: Fecha de fin (YYYY-MM-DD)
      - in: query
        name: limit
        type: integer
        description: Límite de resultados
        default: 50
    responses:
      200:
        description: Lista de movimientos
    """
    params = request.args
    query = {}
    
    if params.get("fromSubaccountId"):
        try:
            query["fromSubaccountId"] = ObjectId(params.get("fromSubaccountId").strip())
        except Exception:
            return jsonify({"message": "ID de subcuenta origen inválido"}), 400
    
    if params.get("toSubaccountId"):
        try:
            query["toSubaccountId"] = ObjectId(params.get("toSubaccountId").strip())
        except Exception:
            return jsonify({"message": "ID de subcuenta destino inválido"}), 400
    
    if params.get("type"):
        query["type"] = params.get("type")
    
    # Filtro por fechas
    if params.get("startDate") or params.get("endDate"):
        query["createdAt"] = {}
        if params.get("startDate"):
            try:
                start_date = datetime.strptime(params.get("startDate"), "%Y-%m-%d")
                query["createdAt"]["$gte"] = start_date
            except ValueError:
                return jsonify({"message": "Formato de fecha de inicio inválido. Use YYYY-MM-DD"}), 400
        
        if params.get("endDate"):
            try:
                end_date = datetime.strptime(params.get("endDate"), "%Y-%m-%d")
                end_date = end_date.replace(hour=23, minute=59, second=59)
                query["createdAt"]["$lte"] = end_date
            except ValueError:
                return jsonify({"message": "Formato de fecha de fin inválido. Use YYYY-MM-DD"}), 400
    
    # Ordenar por fecha descendente
    limit = int(params.get("limit", 50))
    movimientos = mongo.db.account_movements.find(query).sort("createdAt", -1).limit(limit)
    
    list_cursor = list(movimientos)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    
    # Agregar información de subcuentas y usuario
    for movimiento in list_json:
        # Información de subcuenta origen
        if movimiento.get("fromSubaccountId"):
            from_subaccount = mongo.db.subaccounts.find_one({"_id": ObjectId(movimiento["fromSubaccountId"])})
            if from_subaccount:
                movimiento["fromSubaccount"] = {
                    "_id": str(from_subaccount["_id"]),
                    "accountId": str(from_subaccount["accountId"]),
                    "departmentId": str(from_subaccount["departmentId"]),
                    "balanceBs": from_subaccount["balanceBs"]
                }
        
        # Información de subcuenta destino
        if movimiento.get("toSubaccountId"):
            to_subaccount = mongo.db.subaccounts.find_one({"_id": ObjectId(movimiento["toSubaccountId"])})
            if to_subaccount:
                movimiento["toSubaccount"] = {
                    "_id": str(to_subaccount["_id"]),
                    "accountId": str(to_subaccount["accountId"]),
                    "departmentId": str(to_subaccount["departmentId"]),
                    "balanceBs": to_subaccount["balanceBs"]
                }
        
        # Información del usuario creador
        if movimiento.get("createdBy"):
            usuario = mongo.db.usuarios.find_one({"_id": ObjectId(movimiento["createdBy"])})
            if usuario:
                movimiento["creator"] = {
                    "_id": str(usuario["_id"]),
                    "nombre": usuario.get("nombre", ""),
                    "email": usuario.get("email", "")
                }
    
    return jsonify(list_json), 200

@account_movements_bp.route("/account-movements/<string:movimiento_id>", methods=["GET"])
@allow_cors
def obtener_movimiento(movimiento_id):
    """
    Obtener movimiento por ID
    ---
    tags:
      - Movimientos de Cuenta
    parameters:
      - in: path
        name: movimiento_id
        type: string
        required: true
        description: ID del movimiento
    responses:
      200:
        description: Movimiento encontrado
      404:
        description: Movimiento no encontrado
    """
    try:
        movimiento_id_obj = ObjectId(movimiento_id.strip())
    except Exception:
        return jsonify({"message": "ID de movimiento inválido"}), 400
    
    movimiento = mongo.db.account_movements.find_one({"_id": movimiento_id_obj})
    
    if not movimiento:
        return jsonify({"message": "Movimiento no encontrado"}), 404
    
    movimiento["_id"] = str(movimiento["_id"])
    if movimiento.get("fromSubaccountId"):
        movimiento["fromSubaccountId"] = str(movimiento["fromSubaccountId"])
    if movimiento.get("toSubaccountId"):
        movimiento["toSubaccountId"] = str(movimiento["toSubaccountId"])
    if movimiento.get("createdBy"):
        movimiento["createdBy"] = str(movimiento["createdBy"])
    
    movimiento_dump = json.dumps(movimiento, default=json_util.default, ensure_ascii=False)
    movimiento_json = json.loads(movimiento_dump.replace("\\", ""))
    
    return jsonify(movimiento_json), 200

@account_movements_bp.route("/account-movements/create-indexes", methods=["POST"])
@allow_cors
@token_required
def crear_indices(user):
    """
    Crear índices para account_movements
    ---
    tags:
      - Movimientos de Cuenta
    security:
      - Bearer: []
    description: Crea índices por fromSubaccountId, toSubaccountId y createdAt
    responses:
      200:
        description: Índices creados exitosamente
      500:
        description: Error al crear los índices
    """
    try:
        # Crear índice para fromSubaccountId
        mongo.db.account_movements.create_index("fromSubaccountId", name="idx_from_subaccount")
        
        # Crear índice para toSubaccountId
        mongo.db.account_movements.create_index("toSubaccountId", name="idx_to_subaccount")
        
        # Crear índice compuesto para createdAt
        mongo.db.account_movements.create_index("createdAt", name="idx_created_at")
        
        # Crear índice compuesto para consultas comunes
        mongo.db.account_movements.create_index([
            ("type", 1),
            ("createdAt", -1)
        ], name="idx_type_created_at")
        
        return jsonify({
            "message": "Índices creados exitosamente",
            "indexes": [
                "idx_from_subaccount",
                "idx_to_subaccount", 
                "idx_created_at",
                "idx_type_created_at"
            ]
        }), 200
    except Exception as e:
        return jsonify({
            "message": "Error al crear los índices",
            "error": str(e)
        }), 500

@account_movements_bp.route("/account-movements/balance/<string:subaccount_id>", methods=["GET"])
@allow_cors
def obtener_balance_subcuenta(subaccount_id):
    """
    Obtener balance actual de una subcuenta
    ---
    tags:
      - Movimientos de Cuenta
    parameters:
      - in: path
        name: subaccount_id
        type: string
        required: true
        description: ID de la subcuenta
    responses:
      200:
        description: Balance actual
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
    
    return jsonify({
        "subaccountId": str(subcuenta["_id"]),
        "balanceBs": subcuenta["balanceBs"],
        "lastUpdated": subcuenta.get("fecha_actualizacion", subcuenta.get("fecha_creacion"))
    }), 200
