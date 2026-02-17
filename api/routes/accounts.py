from flask import Blueprint, request, jsonify
from bson import json_util, ObjectId
import json
from datetime import datetime
from api.extensions import mongo
from api.util.decorators import allow_cors, validar_datos, token_required, admin_required, apply_department_filter

accounts_bp = Blueprint('accounts', __name__)


def serialize_account(account):
    """Serializar una cuenta de MongoDB a formato JSON compatible con frontend"""
    if not account:
        return None
    
    serialized = {
        "_id": str(account["_id"]),
        "code": account.get("code", ""),
        "name": account.get("name", ""),
        "description": account.get("description", ""),
        "active": account.get("active", True)
    }
    
    # Serializar departamentos (array de ObjectIds a array de strings)
    if "departments" in account and account["departments"]:
        serialized["departments"] = [str(dept_id) for dept_id in account["departments"]]
    else:
        serialized["departments"] = []
    
    # Convertir fechas a strings ISO
    if "created_at" in account and account["created_at"]:
        serialized["created_at"] = account["created_at"].isoformat() if isinstance(account["created_at"], datetime) else str(account["created_at"])
    
    if "updated_at" in account and account["updated_at"]:
        serialized["updated_at"] = account["updated_at"].isoformat() if isinstance(account["updated_at"], datetime) else str(account["updated_at"])
    
    if "created_by" in account:
        serialized["created_by"] = account.get("created_by", "")
    
    if "deactivated_by" in account:
        serialized["deactivated_by"] = account.get("deactivated_by", "")
    
    return serialized


@accounts_bp.route("/accounts", methods=["GET"])
@allow_cors
@token_required
def obtener_cuentas(data_token):
    """
    Listar cuentas contables con filtros opcionales y control de acceso por departamento
    ---
    tags:
      - Cuentas Contables
    security:
      - Bearer: []
    parameters:
      - in: query
        name: code
        type: string
        description: Código de cuenta para búsqueda exacta o parcial
        example: "1.1"
      - in: query
        name: name
        type: string
        description: Texto para buscar en el nombre de la cuenta
        example: "Caja"
      - in: query
        name: active
        type: boolean
        description: Filtrar por estado activo/inactivo
        example: true
      - in: query
        name: page
        type: integer
        description: Número de página (default 1)
        example: 1
      - in: query
        name: limit
        type: integer
        description: Cantidad de registros por página (default 50, max 200)
        example: 50
    responses:
      200:
        description: Lista de cuentas contables (filtradas por departamento si no es admin)
        schema:
          type: object
          properties:
            data:
              type: array
              items:
                type: object
                properties:
                  _id:
                    type: string
                    example: "507f1f77bcf86cd799439011"
                  code:
                    type: string
                    example: "1.1.01"
                    description: Código de la cuenta contable
                  name:
                    type: string
                    example: "Caja General"
                  description:
                    type: string
                    example: "Cuenta para manejo de efectivo en caja"
                  departments:
                    type: array
                    items:
                      type: string
                    description: IDs de departamentos asociados
                  active:
                    type: boolean
                    example: true
                  created_at:
                    type: string
                    format: date-time
                  updated_at:
                    type: string
                    format: date-time
            pagination:
              type: object
              properties:
                page:
                  type: integer
                limit:
                  type: integer
                total:
                  type: integer
                pages:
                  type: integer
      403:
        description: No autorizado
      500:
        description: Error interno del servidor
    """
    try:
        # Construir filtro de búsqueda
        filtro = {}
        
        # Aplicar filtro de departamento basado en rol del usuario
        # Super admin ve todo, usuarios normales solo ven cuentas de su departamento
        dept_filter = apply_department_filter(data_token)
        if dept_filter:
            filtro.update(dept_filter)
        
        # Filtro por código (búsqueda parcial)
        code_param = request.args.get("code")
        if code_param:
            filtro["code"] = {"$regex": f"^{code_param}", "$options": "i"}
        
        # Filtro por nombre (búsqueda parcial)
        name_param = request.args.get("name")
        if name_param:
            filtro["name"] = {"$regex": name_param, "$options": "i"}
        
        # Filtro por estado activo
        active_param = request.args.get("active")
        if active_param is not None:
            filtro["active"] = active_param.lower() == "true"
        
        # Paginación
        page = int(request.args.get("page", 1))
        limit = min(int(request.args.get("limit", 50)), 200)  # Máximo 200 registros
        skip = (page - 1) * limit
        
        # Obtener total de registros
        total = mongo.db.accounts.count_documents(filtro)
        
        # Obtener cuentas con paginación y ordenamiento por código
        cursor = mongo.db.accounts.find(filtro).sort("code", 1).skip(skip).limit(limit)
        
        # Serializar cuentas
        accounts_list = [serialize_account(account) for account in cursor]
        
        # Calcular total de páginas
        total_pages = (total + limit - 1) // limit
        
        response = {
            "data": accounts_list,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": total_pages
            }
        }
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({"message": f"Error al obtener cuentas: {str(e)}"}), 500


@accounts_bp.route("/accounts/<account_id>", methods=["GET"])
@allow_cors
@token_required
def obtener_cuenta_por_id(data_token, account_id):
    """
    Obtener una cuenta contable específica por ID (con control de acceso por departamento)
    ---
    tags:
      - Cuentas Contables
    security:
      - Bearer: []
    parameters:
      - in: path
        name: account_id
        type: string
        required: true
        description: ID de la cuenta contable
        example: "507f1f77bcf86cd799439011"
    responses:
      200:
        description: Cuenta contable encontrada
        schema:
          type: object
          properties:
            _id:
              type: string
            code:
              type: string
            name:
              type: string
            description:
              type: string
            departments:
              type: array
              items:
                type: string
            active:
              type: boolean
            created_at:
              type: string
              format: date-time
            updated_at:
              type: string
              format: date-time
      403:
        description: Acceso denegado
      404:
        description: Cuenta no encontrada
      500:
        description: Error interno del servidor
    """
    try:
        if not ObjectId.is_valid(account_id):
            return jsonify({"message": "ID de cuenta inválido"}), 400
        
        # Construir filtro con ID y restricción de departamento
        filtro = {"_id": ObjectId(account_id)}
        
        # Aplicar filtro de departamento si no es super_admin
        dept_filter = apply_department_filter(data_token)
        if dept_filter:
            filtro.update(dept_filter)
        
        cuenta = mongo.db.accounts.find_one(filtro)
        
        if not cuenta:
            # Verificar si la cuenta existe pero el usuario no tiene acceso
            cuenta_existe = mongo.db.accounts.find_one({"_id": ObjectId(account_id)})
            if cuenta_existe:
                return jsonify({"message": "Acceso denegado. No tiene permisos para ver esta cuenta"}), 403
            return jsonify({"message": "Cuenta no encontrada"}), 404
        
        cuenta_serializada = serialize_account(cuenta)
        
        return jsonify(cuenta_serializada), 200
        
    except Exception as e:
        return jsonify({"message": f"Error al obtener cuenta: {str(e)}"}), 500


@accounts_bp.route("/accounts", methods=["POST"])
@allow_cors
@token_required
@admin_required
@validar_datos({"code": str, "name": str})
def crear_cuenta(data_token):
    """
    Crear una nueva cuenta contable
    ---
    tags:
      - Cuentas Contables
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - code
            - name
          properties:
            code:
              type: string
              description: Código único de la cuenta contable
              example: "1.1.01"
            name:
              type: string
              description: Nombre de la cuenta
              example: "Caja General"
            description:
              type: string
              description: Descripción detallada de la cuenta
              example: "Cuenta para manejo de efectivo en caja"
            departments:
              type: array
              items:
                type: string
              description: Array de IDs de departamentos asociados
              example: ["507f1f77bcf86cd799439011"]
            active:
              type: boolean
              description: Estado de la cuenta (default true)
              example: true
    responses:
      201:
        description: Cuenta creada con éxito
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Cuenta contable creada con éxito"
            _id:
              type: string
              example: "507f1f77bcf86cd799439011"
            account:
              type: object
      400:
        description: Datos inválidos o cuenta duplicada
      403:
        description: No autorizado
      500:
        description: Error interno del servidor
    """
    try:
        data = request.get_json()
        
        code = data["code"].strip()
        name = data["name"].strip()
        description = data.get("description", "").strip()
        active = data.get("active", True)
        
        # Validar que el código no esté vacío
        if not code:
            return jsonify({"message": "El código de cuenta no puede estar vacío"}), 400
        
        # Verificar si ya existe una cuenta con el mismo código
        cuenta_existente = mongo.db.accounts.find_one({"code": code})
        if cuenta_existente:
            return jsonify({"message": f"Ya existe una cuenta con el código '{code}'"}), 400
        
        # Procesar departamentos
        departments = []
        if "departments" in data and data["departments"]:
            # Validar que los departamentos existan
            for dept_id_str in data["departments"]:
                try:
                    dept_id = ObjectId(dept_id_str)
                    departamento = mongo.db.departamentos.find_one({"_id": dept_id})
                    if not departamento:
                        return jsonify({"message": f"Departamento con ID '{dept_id_str}' no encontrado"}), 400
                    departments.append(dept_id)
                except Exception:
                    return jsonify({"message": f"ID de departamento inválido: '{dept_id_str}'"}), 400
        else:
            # Si no se especifican departamentos y el usuario no es super_admin,
            # asignar automáticamente al departamento del usuario
            if data_token.get("role") != "super_admin":
                if "departamento_id" in data_token and data_token["departamento_id"]:
                    try:
                        departments.append(ObjectId(data_token["departamento_id"]))
                    except Exception:
                        pass
        
        # Crear la cuenta
        cuenta = {
            "code": code,
            "name": name,
            "description": description,
            "departments": departments,
            "active": active,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "created_by": data_token.get("email", "system")
        }
        
        cuenta_insertada = mongo.db.accounts.insert_one(cuenta)
        
        # Obtener la cuenta creada y serializarla
        cuenta_creada = mongo.db.accounts.find_one({"_id": cuenta_insertada.inserted_id})
        cuenta_serializada = serialize_account(cuenta_creada)
        
        return jsonify({
            "message": "Cuenta contable creada con éxito",
            "_id": str(cuenta_insertada.inserted_id),
            "account": cuenta_serializada
        }), 201
        
    except KeyError as e:
        return jsonify({"message": f"Campo requerido faltante: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"message": f"Error al crear cuenta: {str(e)}"}), 500


@accounts_bp.route("/accounts/<account_id>", methods=["PUT"])
@allow_cors
@token_required
@admin_required
def actualizar_cuenta(data_token, account_id):
    """
    Actualizar una cuenta contable existente (con control de acceso por departamento)
    ---
    tags:
      - Cuentas Contables
    security:
      - Bearer: []
    parameters:
      - in: path
        name: account_id
        type: string
        required: true
        description: ID de la cuenta contable
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            code:
              type: string
              description: Código de la cuenta contable
              example: "1.1.01"
            name:
              type: string
              description: Nombre de la cuenta
              example: "Caja General Actualizada"
            description:
              type: string
              description: Descripción de la cuenta
            departments:
              type: array
              items:
                type: string
              description: Array de IDs de departamentos asociados
            active:
              type: boolean
              description: Estado de la cuenta
              example: true
    responses:
      200:
        description: Cuenta actualizada con éxito
      400:
        description: Datos inválidos
      403:
        description: No autorizado o acceso denegado
      404:
        description: Cuenta no encontrada
      500:
        description: Error interno del servidor
    """
    try:
        if not ObjectId.is_valid(account_id):
            return jsonify({"message": "ID de cuenta inválido"}), 400
        
        data = request.get_json()
        
        if not data:
            return jsonify({"message": "No se proporcionaron datos para actualizar"}), 400
        
        # Verificar que la cuenta existe y el usuario tiene acceso
        filtro_acceso = {"_id": ObjectId(account_id)}
        dept_filter = apply_department_filter(data_token)
        if dept_filter:
            filtro_acceso.update(dept_filter)
        
        cuenta_existente = mongo.db.accounts.find_one(filtro_acceso)
        if not cuenta_existente:
            # Verificar si la cuenta existe pero el usuario no tiene acceso
            cuenta_existe = mongo.db.accounts.find_one({"_id": ObjectId(account_id)})
            if cuenta_existe:
                return jsonify({"message": "Acceso denegado. No tiene permisos para modificar esta cuenta"}), 403
            return jsonify({"message": "Cuenta no encontrada"}), 404
        
        # Construir objeto de actualización
        actualizacion = {"updated_at": datetime.utcnow()}
        
        if "code" in data:
            nuevo_codigo = data["code"].strip()
            if not nuevo_codigo:
                return jsonify({"message": "El código de cuenta no puede estar vacío"}), 400
            
            # Verificar que no exista otra cuenta con el mismo código
            cuenta_duplicada = mongo.db.accounts.find_one({
                "code": nuevo_codigo,
                "_id": {"$ne": ObjectId(account_id)}
            })
            if cuenta_duplicada:
                return jsonify({"message": f"Ya existe otra cuenta con el código '{nuevo_codigo}'"}), 400
            
            actualizacion["code"] = nuevo_codigo
        
        if "name" in data:
            actualizacion["name"] = data["name"].strip()
        
        if "description" in data:
            actualizacion["description"] = data["description"].strip()
        
        if "active" in data:
            actualizacion["active"] = data["active"]
        
        # Procesar departamentos (solo si se proporciona)
        if "departments" in data:
            departments = []
            if data["departments"]:
                # Validar que los departamentos existan
                for dept_id_str in data["departments"]:
                    try:
                        dept_id = ObjectId(dept_id_str)
                        departamento = mongo.db.departamentos.find_one({"_id": dept_id})
                        if not departamento:
                            return jsonify({"message": f"Departamento con ID '{dept_id_str}' no encontrado"}), 400
                        departments.append(dept_id)
                    except Exception:
                        return jsonify({"message": f"ID de departamento inválido: '{dept_id_str}'"}), 400
            
            actualizacion["departments"] = departments
        
        # Actualizar la cuenta
        resultado = mongo.db.accounts.update_one(
            {"_id": ObjectId(account_id)},
            {"$set": actualizacion}
        )
        
        if resultado.modified_count == 0:
            return jsonify({"message": "No se realizaron cambios en la cuenta"}), 200
        
        # Obtener la cuenta actualizada
        cuenta_actualizada = mongo.db.accounts.find_one({"_id": ObjectId(account_id)})
        cuenta_serializada = serialize_account(cuenta_actualizada)
        
        return jsonify({
            "message": "Cuenta actualizada con éxito",
            "account": cuenta_serializada
        }), 200
        
    except Exception as e:
        return jsonify({"message": f"Error al actualizar cuenta: {str(e)}"}), 500


@accounts_bp.route("/accounts/<account_id>", methods=["DELETE"])
@allow_cors
@token_required
@admin_required
def desactivar_cuenta(data_token, account_id):
    """
    Desactivar una cuenta contable (soft delete)
    ---
    tags:
      - Cuentas Contables
    security:
      - Bearer: []
    parameters:
      - in: path
        name: account_id
        type: string
        required: true
        description: ID de la cuenta contable
    responses:
      200:
        description: Cuenta desactivada con éxito
      400:
        description: ID inválido
      403:
        description: No autorizado
      404:
        description: Cuenta no encontrada
      500:
        description: Error interno del servidor
    """
    try:
        if not ObjectId.is_valid(account_id):
            return jsonify({"message": "ID de cuenta inválido"}), 400
        
        # Verificar que la cuenta existe
        cuenta = mongo.db.accounts.find_one({"_id": ObjectId(account_id)})
        if not cuenta:
            return jsonify({"message": "Cuenta no encontrada"}), 404
        
        # Desactivar la cuenta (soft delete)
        resultado = mongo.db.accounts.update_one(
            {"_id": ObjectId(account_id)},
            {"$set": {
                "active": False,
                "updated_at": datetime.utcnow(),
                "deactivated_by": data_token.get("email", "system")
            }}
        )
        
        if resultado.modified_count == 0:
            return jsonify({"message": "La cuenta ya estaba desactivada"}), 200
        
        return jsonify({"message": "Cuenta desactivada con éxito"}), 200
        
    except Exception as e:
        return jsonify({"message": f"Error al desactivar cuenta: {str(e)}"}), 500


@accounts_bp.route("/accounts/code/<code>", methods=["GET"])
@allow_cors
def obtener_cuenta_por_codigo(code):
    """
    Obtener una cuenta contable por su código
    ---
    tags:
      - Cuentas Contables
    parameters:
      - in: path
        name: code
        type: string
        required: true
        description: Código de la cuenta contable
        example: "1.1.01"
    responses:
      200:
        description: Cuenta contable encontrada
      404:
        description: Cuenta no encontrada
      500:
        description: Error interno del servidor
    """
    try:
        cuenta = mongo.db.accounts.find_one({"code": code})
        
        if not cuenta:
            return jsonify({"message": f"No se encontró cuenta con código '{code}'"}), 404
        
        cuenta_serializada = serialize_account(cuenta)
        
        return jsonify(cuenta_serializada), 200
        
    except Exception as e:
        return jsonify({"message": f"Error al obtener cuenta: {str(e)}"}), 500
