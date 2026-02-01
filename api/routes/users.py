from flask import Blueprint, request, jsonify
from bson import ObjectId, json_util
import json
from api.extensions import mongo
from api.util.decorators import token_required, allow_cors, validar_datos

users_bp = Blueprint('users', __name__)

@users_bp.route("/editar_usuario/<id_usuario>", methods=["PUT"])
@token_required
def editar_usuario(id_usuario):
    """
    Editar información de usuario
    ---
    tags:
      - Usuarios
    security:
      - Bearer: []
    parameters:
      - in: path
        name: id_usuario
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          properties:
            nombre:
              type: string
            email:
              type: string
    responses:
      200:
        description: Usuario actualizado
    """
    data = request.get_json()
    mongo.db.usuarios.update_one({"_id": ObjectId(id_usuario)}, {"$set": data})
    return jsonify({"message": "Información de usuario actualizada con éxito"}), 200

@users_bp.route("/eliminar_usuario", methods=["POST"])
@token_required
def eliminar_usuario(user):
    """
    Eliminar usuario
    ---
    tags:
      - Usuarios
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - id_usuario
          properties:
            id_usuario:
              type: string
              example: "507f1f77bcf86cd799439011"
    responses:
      200:
        description: Usuario eliminado
      400:
        description: No se pudo eliminar
    """
    data = request.get_json()
    id_usuario = data["id_usuario"]
    result = mongo.db.usuarios.delete_one({"_id": ObjectId(id_usuario)})

    if result.deleted_count == 1:
        return jsonify({"message": "Usuario eliminado éxitosamente"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar el usuario"}), 400

@users_bp.route("/roles", methods=["GET"])
@allow_cors
def roles():
    """
    Listar todos los roles
    ---
    tags:
      - Usuarios
    responses:
      200:
        description: Lista de roles
        schema:
          type: array
          items:
            type: object
            properties:
              _id:
                type: string
              nombre:
                type: string
    """
    roles = mongo.db.roles.find({})
    list_cursor = list(roles)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    return jsonify(list_json)

@users_bp.route("/crear_rol", methods=["POST"])
@token_required
def crear_rol():
    """
    Crear nuevo rol
    ---
    tags:
      - Usuarios
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            nombre:
              type: string
    responses:
      201:
        description: Rol creado
    """
    data = request.get_json()
    mongo.db.roles.insert_one(data)
    return jsonify({"message": "Rol creado con éxito"}), 201

@users_bp.route("/asignar_rol", methods=["PATCH"])
@token_required
def asignar_rol():
    """
    Asignar rol a usuario
    ---
    tags:
      - Usuarios
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - user_id
            - rol_id
          properties:
            user_id:
              type: string
            rol_id:
              type: string
    responses:
      200:
        description: Rol asignado
    """
    data = request.get_json()
    user_id = data["user_id"]
    rol_id = data["rol_id"]
    mongo.db.usuarios.update_one(
        {"_id": ObjectId(user_id)}, {"$set": {"rol_id": ObjectId(rol_id)}}
    )
    return jsonify({"message": "Rol asignado con éxito"}), 200

@users_bp.route("/cambiar_rol_usuario", methods=["POST"])
@allow_cors
@validar_datos({"id": str, "rol": str})
@token_required
def cambiar_rol_usuario(user):
    """
    Cambiar rol de usuario
    ---
    tags:
      - Usuarios
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - id
            - rol
          properties:
            id:
              type: string
              description: ID del usuario
            rol:
              type: string
              enum: ["usuario", "admin_departamento", "super_admin"]
            departamento_id:
              type: string
              description: ID del departamento (opcional)
    responses:
      200:
        description: Rol actualizado
      400:
        description: Rol inválido o departamento no encontrado
      404:
        description: Usuario no encontrado
    """
    data = request.get_json()
    usuario_id = data.get("id")
    nuevo_rol = data.get("rol")
    departamento_id = data.get("departamento_id")

    roles_permitidos = ["usuario", "admin_departamento", "super_admin"]
    if nuevo_rol not in roles_permitidos:
        return jsonify({"message": f"Rol inválido. Debe ser uno de: {', '.join(roles_permitidos)}"}), 400

    usuario = mongo.db.usuarios.find_one({"_id": ObjectId(usuario_id)})
    if not usuario:
        return jsonify({"message": "Usuario no encontrado"}), 404

    update_data = {"rol": nuevo_rol}
    
    if departamento_id:
        try:
            departamento = mongo.db.departamentos.find_one({"_id": ObjectId(departamento_id)})
            if not departamento:
                return jsonify({"message": "Departamento no encontrado"}), 400
            update_data["departamento_id"] = ObjectId(departamento_id)
        except Exception:
            return jsonify({"message": "ID de departamento inválido"}), 400
    elif "departamento_id" in data and data["departamento_id"] is None:
        update_data["departamento_id"] = None

    mongo.db.usuarios.update_one(
        {"_id": ObjectId(usuario_id)},
        {"$set": update_data}
    )

    return jsonify({"message": "Rol actualizado correctamente"}), 200

@users_bp.route("/mostrar_usuarios", methods=["GET"])
@allow_cors
def mostrar_usuarios():
    """
    Listar usuarios con paginación
    ---
    tags:
      - Usuarios
    parameters:
      - in: query
        name: page
        type: integer
        default: 0
      - in: query
        name: limit
        type: integer
        default: 10
      - in: query
        name: text
        type: string
        description: Buscar por nombre o email
    responses:
      200:
        description: Lista de usuarios
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
                  email:
                    type: string
                  rol:
                    type: string
            count:
              type: integer
    """
    params = request.args
    skip = int(params.get("page")) if params.get("page") else 0
    limit = int(params.get("limit")) if params.get("limit") else 10
    text = params.get("text")

    query = {}
    if text:
        query = {
            "$or": [
                {"nombre": {"$regex": text, "$options": "i"}},
                {"email": {"$regex": text, "$options": "i"}},
            ]
        }
    list_users = mongo.db.usuarios.find(query).skip(skip * limit).limit(limit)
    quantity = mongo.db.usuarios.count_documents(query)
    list_cursor = list(list_users)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    return jsonify(request_list=list_json, count=quantity)
