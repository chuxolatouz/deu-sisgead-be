from flask import Blueprint, request, jsonify
from bson import ObjectId, json_util
import json
from datetime import datetime, timezone
from api.extensions import mongo
from api.util.decorators import token_required, allow_cors, validar_datos

departments_bp = Blueprint('departments', __name__)


def _serialize_cursor(cursor):
    list_cursor = list(cursor)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    return json.loads(list_dump.replace("\\", ""))


def _user_department_id(user):
    dep = user.get("departmentId") or user.get("departamento_id")
    return str(dep) if dep else None


def _can_access_department(user, departamento_id):
    if user.get("role") == "super_admin":
        return True
    user_dep = _user_department_id(user)
    return bool(user_dep and user_dep == str(departamento_id))

@departments_bp.route("/departamentos", methods=["POST"])
@allow_cors
@token_required
@validar_datos({"nombre": str, "descripcion": str, "codigo": str})
def crear_departamento(user):
    """
    Crear nuevo departamento
    ---
    tags:
      - Departamentos
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
            - descripcion
            - codigo
          properties:
            nombre:
              type: string
              description: Nombre del departamento
              example: "Recursos Humanos"
            descripcion:
              type: string
              description: Descripción del departamento
              example: "Departamento de gestión de personal"
            codigo:
              type: string
              description: Código único del departamento
              example: "RRHH-001"
    responses:
      201:
        description: Departamento creado exitosamente
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Departamento creado con éxito"
            _id:
              type: string
              example: "507f1f77bcf86cd799439011"
      400:
        description: Datos inválidos
    """
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
    """
    Listar todos los departamentos
    ---
    tags:
      - Departamentos
    parameters:
      - in: query
        name: activo
        type: boolean
        description: Filtrar por estado activo/inactivo
        example: true
    responses:
      200:
        description: Lista de departamentos
        schema:
          type: array
          items:
            type: object
            properties:
              _id:
                type: string
              nombre:
                type: string
              descripcion:
                type: string
              codigo:
                type: string
              activo:
                type: boolean
              fecha_creacion:
                type: string
                format: date-time
    """
    params = request.args
    query = {}
    if params.get("activo") is not None:
        query["activo"] = params.get("activo").lower() == "true"
    
    has_pagination = params.get("page") is not None or params.get("limit") is not None
    if not has_pagination:
        departamentos = mongo.db.departamentos.find(query)
        return jsonify(_serialize_cursor(departamentos)), 200

    page = int(params.get("page")) if params.get("page") else 0
    limit = int(params.get("limit")) if params.get("limit") else 10
    if limit <= 0:
        limit = 10
    if page < 0:
        page = 0
    skip = page * limit

    departamentos = mongo.db.departamentos.find(query).skip(skip).limit(limit)
    count = mongo.db.departamentos.count_documents(query)
    return jsonify(request_list=_serialize_cursor(departamentos), count=count), 200

@departments_bp.route("/departamentos/<string:departamento_id>", methods=["GET"])
@allow_cors
def obtener_departamento(departamento_id):
    """
    Obtener departamento por ID
    ---
    tags:
      - Departamentos
    parameters:
      - in: path
        name: departamento_id
        type: string
        required: true
        description: ID del departamento
        example: "507f1f77bcf86cd799439011"
    responses:
      200:
        description: Departamento encontrado
        schema:
          type: object
          properties:
            _id:
              type: string
            nombre:
              type: string
            descripcion:
              type: string
            codigo:
              type: string
            activo:
              type: boolean
      400:
        description: ID inválido
      404:
        description: Departamento no encontrado
    """
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
    """
    Actualizar departamento
    ---
    tags:
      - Departamentos
    security:
      - Bearer: []
    parameters:
      - in: path
        name: departamento_id
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          properties:
            nombre:
              type: string
            descripcion:
              type: string
            codigo:
              type: string
            activo:
              type: boolean
    responses:
      200:
        description: Departamento actualizado
      404:
        description: Departamento no encontrado
    """
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
    """
    Eliminar departamento
    ---
    tags:
      - Departamentos
    security:
      - Bearer: []
    parameters:
      - in: path
        name: departamento_id
        type: string
        required: true
    responses:
      200:
        description: Departamento eliminado
      404:
        description: Departamento no encontrado
      400:
        description: No se pudo eliminar
    """
    departamento = mongo.db.departamentos.find_one({"_id": ObjectId(departamento_id)})
    if not departamento:
        return jsonify({"message": "Departamento no encontrado"}), 404
    
    result = mongo.db.departamentos.delete_one({"_id": ObjectId(departamento_id)})
    if result.deleted_count == 1:
        return jsonify({"message": "Departamento eliminado con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar el departamento"}), 400


@departments_bp.route("/departamentos/<string:departamento_id>/proyectos", methods=["GET"])
@allow_cors
@token_required
def listar_proyectos_departamento(user, departamento_id):
    if not _can_access_department(user, departamento_id):
        return jsonify({"message": "No autorizado para consultar proyectos de este departamento"}), 403

    try:
        departamento_obj_id = ObjectId(departamento_id.strip())
    except Exception:
        return jsonify({"message": "ID de departamento inválido"}), 400

    page = int(request.args.get("page")) if request.args.get("page") else 0
    limit = int(request.args.get("limit")) if request.args.get("limit") else 10
    if limit <= 0:
        limit = 10
    if page < 0:
        page = 0
    skip = page * limit

    query = {"departamento_id": departamento_obj_id}
    projection = {"miembros.usuario.password": 0}
    projects = mongo.db.proyectos.find(query, projection=projection).skip(skip).limit(limit)
    count = mongo.db.proyectos.count_documents(query)
    payload = _serialize_cursor(projects)
    for item in payload:
        dep_id = item.get("departamento_id")
        if isinstance(dep_id, dict):
            dep_id = dep_id.get("$oid")
            item["departamento_id"] = dep_id
        if dep_id:
            item["departmentId"] = dep_id
    return jsonify(request_list=payload, count=count), 200


@departments_bp.route("/departamentos/<string:departamento_id>/usuarios", methods=["GET"])
@allow_cors
@token_required
def listar_usuarios_departamento(user, departamento_id):
    if not _can_access_department(user, departamento_id):
        return jsonify({"message": "No autorizado para consultar usuarios de este departamento"}), 403

    try:
        departamento_obj_id = ObjectId(departamento_id.strip())
    except Exception:
        return jsonify({"message": "ID de departamento inválido"}), 400

    page = int(request.args.get("page")) if request.args.get("page") else 0
    limit = int(request.args.get("limit")) if request.args.get("limit") else 10
    if limit <= 0:
        limit = 10
    if page < 0:
        page = 0
    skip = page * limit

    query = {"departamento_id": departamento_obj_id}
    projection = {"password": 0}
    users = mongo.db.usuarios.find(query, projection=projection).skip(skip).limit(limit)
    count = mongo.db.usuarios.count_documents(query)
    payload = _serialize_cursor(users)
    for item in payload:
        dep_id = item.get("departamento_id")
        if isinstance(dep_id, dict):
            dep_id = dep_id.get("$oid")
            item["departamento_id"] = dep_id
        if dep_id:
            item["departmentId"] = dep_id
    return jsonify(request_list=payload, count=count), 200

@departments_bp.route("/contexto_departamento", methods=["GET"])
@allow_cors
@token_required
def obtener_contexto_departamento(user):
    """
    Obtener contexto de departamento para super_admin
    ---
    tags:
      - Departamentos
    security:
      - Bearer: []
    description: Solo disponible para usuarios super_admin
    parameters:
      - in: header
        name: X-Department-Context
        type: string
        description: ID del departamento para contexto
    responses:
      200:
        description: Contexto del departamento
        schema:
          type: object
          properties:
            departamento_id:
              type: string
            usando_contexto:
              type: boolean
            departamento:
              type: object
              properties:
                _id:
                  type: string
                nombre:
                  type: string
                descripcion:
                  type: string
                codigo:
                  type: string
      403:
        description: Solo super_admin puede usar este endpoint
    """
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
    
    resolved_department_id = dept_context.strip() if dept_context and usando_contexto else None
    return jsonify({
        "departamento_id": resolved_department_id,
        "departmentId": resolved_department_id,
        "usando_contexto": usando_contexto,
        "departamento": departamento
    }), 200
