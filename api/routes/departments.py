from flask import Blueprint, request, jsonify
from bson import ObjectId, json_util
import json
from datetime import datetime, timezone
from api.extensions import mongo
from api.util.decorators import token_required, allow_cors, validar_datos

departments_bp = Blueprint('departments', __name__)

@departments_bp.route("/departamentos", methods=["POST"])
@allow_cors
#@token_required
@validar_datos({"nombre": str, "descripcion": str, "codigo": str})
#def crear_departamento(user):
def crear_departamento():
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
    
    departamentos = mongo.db.departamentos.find(query)
    list_cursor = list(departamentos)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    return jsonify(list_json), 200

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
    
    return jsonify({
        "departamento_id": dept_context.strip() if dept_context and usando_contexto else None,
        "usando_contexto": usando_contexto,
        "departamento": departamento
    }), 200
