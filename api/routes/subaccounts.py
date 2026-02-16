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

@subaccounts_bp.route("/subaccounts/transfer", methods=["POST"])
@allow_cors
#@token_required
#def transferir_entre_subcuentas(user):
def transferir_entre_subcuentas():
    """
    Transferir fondos entre subcuentas (solo administradores)
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
            - fromSubaccountId
            - toSubaccountId
            - amountBs
          properties:
            fromSubaccountId:
              type: string
              description: ID de la subcuenta origen
              example: "507f1f77bcf86cd799439011"
            toSubaccountId:
              type: string
              description: ID de la subcuenta destino
              example: "507f1f77bcf86cd799439012"
            amountBs:
              type: number
              description: Monto a transferir (debe ser mayor a 0)
              example: 500.00
            description:
              type: string
              description: Descripción de la transferencia
              example: "Transferencia entre departamentos"
    responses:
      200:
        description: Transferencia realizada exitosamente
      400:
        description: Datos inválidos o saldo insuficiente
      403:
        description: No autorizado - solo administradores
      404:
        description: Subcuenta no encontrada
    """
    # Validar permisos de administrador
    
    #if user.get("role") not in ["admin", "super_admin"]:
        #return jsonify({"message": "No autorizado - solo administradores pueden realizar transferencias"}), 403
    
    data = request.get_json()
    
    # Validar campos requeridos
    required_fields = ["fromSubaccountId", "toSubaccountId", "amountBs"]
    for field in required_fields:
        if field not in data:
            return jsonify({"message": f"Campo requerido: {field}"}), 400
    
    # Validar que los IDs sean diferentes
    if data["fromSubaccountId"] == data["toSubaccountId"]:
        return jsonify({"message": "La subcuenta origen y destino deben ser diferentes"}), 400
    
    # Validar que el monto sea mayor a 0
    if data["amountBs"] <= 0:
        return jsonify({"message": "El monto debe ser mayor a 0"}), 400
    
    try:
        from_subaccount_id = ObjectId(data["fromSubaccountId"].strip())
        to_subaccount_id = ObjectId(data["toSubaccountId"].strip())
    except Exception:
        return jsonify({"message": "IDs de subcuenta inválidos"}), 400
    
    # Verificar que ambas subcuentas existen
    from_subaccount = mongo.db.subaccounts.find_one({"_id": from_subaccount_id})
    to_subaccount = mongo.db.subaccounts.find_one({"_id": to_subaccount_id})
    
    if not from_subaccount:
        return jsonify({"message": "Subcuenta origen no encontrada"}), 404
    if not to_subaccount:
        return jsonify({"message": "Subcuenta destino no encontrada"}), 404
    
    # Validar saldo suficiente
    if from_subaccount["balanceBs"] < data["amountBs"]:
        return jsonify({"message": "Saldo insuficiente en la subcuenta origen"}), 400
    
    # Realizar transferencia atómica usando transacción
    from datetime import datetime, timezone
    
    try:
        # Iniciar sesión para transacción
        with mongo.cx.start_session() as session:
            with session.start_transaction():
                # Actualizar saldos atómicamente
                mongo.db.subaccounts.update_one(
                    {"_id": from_subaccount_id},
                    {
                        "$inc": {"balanceBs": -data["amountBs"]},
                        "$set": {"fecha_actualizacion": datetime.now(timezone.utc)}
                    },
                    session=session
                )
                
                mongo.db.subaccounts.update_one(
                    {"_id": to_subaccount_id},
                    {
                        "$inc": {"balanceBs": data["amountBs"]},
                        "$set": {"fecha_actualizacion": datetime.now(timezone.utc)}
                    },
                    session=session
                )
                
                # Registrar movimiento de transferencia
                movimiento = {
                    "type": "transfer",
                    "amountBs": float(data["amountBs"]),
                    "fromSubaccountId": from_subaccount_id,
                    "toSubaccountId": to_subaccount_id,
                    "createdBy": ObjectId("000000000000000000000000"),  # Usuario de prueba
                    #"createdBy": ObjectId(user["_id"]),
                    "createdAt": datetime.now(timezone.utc),
                    "metadata": {
                        "description": data.get("description", "Transferencia entre subcuentas"),
                        "fromDepartmentId": from_subaccount["departmentId"],
                        "toDepartmentId": to_subaccount["departmentId"],
                        "fromAccountId": from_subaccount["accountId"],
                        "toAccountId": to_subaccount["accountId"]
                    }
                }
                
                mongo.db.account_movements.insert_one(movimiento, session=session)
                
    except Exception as e:
        return jsonify({
            "message": "Error al realizar la transferencia",
            "error": str(e)
        }), 500
    
    # Obtener saldos actualizados
    from_updated = mongo.db.subaccounts.find_one({"_id": from_subaccount_id})
    to_updated = mongo.db.subaccounts.find_one({"_id": to_subaccount_id})
    
    return jsonify({
        "message": "Transferencia realizada exitosamente",
        "transfer": {
            "fromSubaccountId": str(from_subaccount_id),
            "toSubaccountId": str(to_subaccount_id),
            "amountBs": float(data["amountBs"]),
            "description": data.get("description", ""),
            "fromNewBalance": from_updated["balanceBs"],
            "toNewBalance": to_updated["balanceBs"],
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    }), 200

@subaccounts_bp.route("/subaccounts/transfer-preview", methods=["POST"])
@allow_cors
@token_required
def previsualizar_transferencia(user):
    """
    Previsualizar transferencia entre subcuentas (solo administradores)
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
            - fromSubaccountId
            - toSubaccountId
            - amountBs
          properties:
            fromSubaccountId:
              type: string
            toSubaccountId:
              type: string
            amountBs:
              type: number
    responses:
      200:
        description: Previsualización de transferencia
      403:
        description: No autorizado
      400:
        description: Datos inválidos
    """
    # Validar permisos de administrador
    if user.get("role") not in ["admin", "super_admin"]:
        return jsonify({"message": "No autorizado"}), 403
    
    data = request.get_json()
    
    # Validar campos básicos
    if data["fromSubaccountId"] == data["toSubaccountId"]:
        return jsonify({"message": "Las subcuentas deben ser diferentes"}), 400
    
    if data["amountBs"] <= 0:
        return jsonify({"message": "El monto debe ser mayor a 0"}), 400
    
    try:
        from_subaccount_id = ObjectId(data["fromSubaccountId"].strip())
        to_subaccount_id = ObjectId(data["toSubaccountId"].strip())
    except Exception:
        return jsonify({"message": "IDs inválidos"}), 400
    
    # Obtener información de subcuentas
    from_subaccount = mongo.db.subaccounts.find_one({"_id": from_subaccount_id})
    to_subaccount = mongo.db.subaccounts.find_one({"_id": to_subaccount_id})
    
    if not from_subaccount or not to_subaccount:
        return jsonify({"message": "Subcuenta no encontrada"}), 404
    
    # Verificar saldo suficiente
    saldo_suficiente = from_subaccount["balanceBs"] >= data["amountBs"]
    
    # Obtener información adicional
    from_cuenta = mongo.db.cuentas.find_one({"_id": from_subaccount["accountId"]})
    to_cuenta = mongo.db.cuentas.find_one({"_id": to_subaccount["accountId"]})
    from_dept = mongo.db.departamentos.find_one({"_id": from_subaccount["departmentId"]})
    to_dept = mongo.db.departamentos.find_one({"_id": to_subaccount["departmentId"]})
    
    return jsonify({
        "preview": {
            "fromSubaccount": {
                "id": str(from_subaccount_id),
                "account": from_cuenta.get("nombre", "") if from_cuenta else "",
                "department": from_dept.get("nombre", "") if from_dept else "",
                "currentBalance": from_subaccount["balanceBs"],
                "afterBalance": from_subaccount["balanceBs"] - data["amountBs"]
            },
            "toSubaccount": {
                "id": str(to_subaccount_id),
                "account": to_cuenta.get("nombre", "") if to_cuenta else "",
                "department": to_dept.get("nombre", "") if to_dept else "",
                "currentBalance": to_subaccount["balanceBs"],
                "afterBalance": to_subaccount["balanceBs"] + data["amountBs"]
            },
            "transfer": {
                "amountBs": float(data["amountBs"]),
                "sufficientFunds": saldo_suficiente,
                "sameDepartment": from_subaccount["departmentId"] == to_subaccount["departmentId"],
                "sameAccount": from_subaccount["accountId"] == to_subaccount["accountId"]
            }
        }
    }), 200

@subaccounts_bp.route("/subaccounts/<string:subaccount_id>/movements", methods=["GET"])
@allow_cors
def listar_movimientos_subcuenta(subaccount_id):
    """
    Listar movimientos de una subcuenta con paginación y filtros
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
        description: ID de la subcuenta
      - in: query
        name: fromDate
        type: string
        description: Fecha de inicio (YYYY-MM-DD)
      - in: query
        name: toDate
        type: string
        description: Fecha de fin (YYYY-MM-DD)
      - in: query
        name: type
        type: string
        description: Tipo de movimiento (credit, debit, transfer)
      - in: query
        name: limit
        type: integer
        description: Límite de resultados por página
        default: 20
      - in: query
        name: offset
        type: integer
        description: Número de resultados a omitir
        default: 0
      - in: query
        name: page
        type: integer
        description: Número de página (alternativa a offset)
        default: 1
    responses:
      200:
        description: Lista de movimientos paginada
      400:
        description: Parámetros inválidos
      403:
        description: No autorizado
      404:
        description: Subcuenta no encontrada
    """
    try:
        subaccount_id_obj = ObjectId(subaccount_id.strip())
    except Exception:
        return jsonify({"message": "ID de subcuenta inválido"}), 400
    
    # Verificar que la subcuenta existe
    subcuenta = mongo.db.subaccounts.find_one({"_id": subaccount_id_obj})
    if not subcuenta:
        return jsonify({"message": "Subcuenta no encontrada"}), 404
    
    # Obtener usuario del token si existe (para RBAC)
    user = None
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        try:
            # Aquí deberías decodificar el token para obtener el usuario
            # Por ahora, asumimos que no hay autenticación para pruebas
            user = None
        except:
            user = None
    
    # RBAC: Verificar permisos
    if user:
        # Si es usuario normal, solo puede ver movimientos de su departamento
        if user.get("role") not in ["admin", "super_admin"]:
            user_dept_id = user.get("departmentId")
            if not user_dept_id or str(subcuenta["departmentId"]) != str(user_dept_id):
                return jsonify({"message": "No autorizado - solo puede ver movimientos de su departamento"}), 403
    
    # Construir query base
    query = {
        "$or": [
            {"fromSubaccountId": subaccount_id_obj},
            {"toSubaccountId": subaccount_id_obj}
        ]
    }
    
    # Aplicar filtros
    params = request.args
    
    # Filtro por tipo
    if params.get("type"):
        query["type"] = params.get("type")
    
    # Filtro por fechas
    if params.get("fromDate") or params.get("toDate"):
        query["createdAt"] = {}
        if params.get("fromDate"):
            try:
                from_date = datetime.strptime(params.get("fromDate"), "%Y-%m-%d")
                query["createdAt"]["$gte"] = from_date
            except ValueError:
                return jsonify({"message": "Formato de fecha de inicio inválido. Use YYYY-MM-DD"}), 400
        
        if params.get("toDate"):
            try:
                to_date = datetime.strptime(params.get("toDate"), "%Y-%m-%d")
                to_date = to_date.replace(hour=23, minute=59, second=59)
                query["createdAt"]["$lte"] = to_date
            except ValueError:
                return jsonify({"message": "Formato de fecha de fin inválido. Use YYYY-MM-DD"}), 400
    
    # Paginación
    limit = min(int(params.get("limit", 20)), 100)  # Máximo 100 por página
    
    if params.get("page"):
        page = max(int(params.get("page", 1)), 1)
        offset = (page - 1) * limit
    else:
        offset = max(int(params.get("offset", 0)), 0)
    
    # Obtener total de documentos para paginación
    total_count = mongo.db.account_movements.count_documents(query)
    
    # Obtener movimientos paginados
    movimientos = mongo.db.account_movements.find(query).sort("createdAt", -1).skip(offset).limit(limit)
    
    list_cursor = list(movimientos)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    
    # Enriquecer datos
    for movimiento in list_json:
        movimiento["_id"] = str(movimiento["_id"])
        
        # Información de subcuentas
        if movimiento.get("fromSubaccountId"):
            movimiento["fromSubaccountId"] = str(movimiento["fromSubaccountId"])
            if movimiento["fromSubaccountId"] == subaccount_id:
                movimiento["direction"] = "outgoing"
        
        if movimiento.get("toSubaccountId"):
            movimiento["toSubaccountId"] = str(movimiento["toSubaccountId"])
            if movimiento["toSubaccountId"] == subaccount_id:
                movimiento["direction"] = "incoming"
        
        # Si no tiene dirección, determinar por el tipo
        if not movimiento.get("direction"):
            if movimiento.get("type") == "debit":
                movimiento["direction"] = "outgoing"
            elif movimiento.get("type") == "credit":
                movimiento["direction"] = "incoming"
            elif movimiento.get("type") == "transfer":
                movimiento["direction"] = "outgoing"  # Por defecto para transferencias
        
        # Información del usuario creador
        if movimiento.get("createdBy"):
            try:
                creator_id = ObjectId(movimiento["createdBy"])
                creator = mongo.db.usuarios.find_one({"_id": creator_id})
                if creator:
                    movimiento["creator"] = {
                        "_id": str(creator["_id"]),
                        "nombre": creator.get("nombre", ""),
                        "email": creator.get("email", "")
                    }
            except:
                pass
    
    # Calcular información de paginación
    total_pages = (total_count + limit - 1) // limit
    current_page = (offset // limit) + 1
    has_next = offset + limit < total_count
    has_prev = offset > 0
    
    return jsonify({
        "movements": list_json,
        "pagination": {
            "totalCount": total_count,
            "limit": limit,
            "offset": offset,
            "currentPage": current_page,
            "totalPages": total_pages,
            "hasNext": has_next,
            "hasPrev": has_prev
        },
        "filters": {
            "subaccountId": subaccount_id,
            "fromDate": params.get("fromDate"),
            "toDate": params.get("toDate"),
            "type": params.get("type")
        }
    }), 200
