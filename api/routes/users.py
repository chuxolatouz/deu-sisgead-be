from flask import Blueprint, request, jsonify
from bson import ObjectId, json_util
import json
from api.extensions import mongo, bcrypt
from api.util.decorators import token_required, allow_cors, validar_datos
from api.util.access import (
    ROLE_ADMIN_DEPARTAMENTO,
    ROLE_SUPER_ADMIN,
    ROLE_USUARIO,
    VALID_ROLES,
    department_scope_filter,
    ensure_role_department_policy,
    is_admin_departamento,
    is_super_admin,
    normalize_role,
    parse_object_id,
    pick_value,
    user_department_id,
    user_role,
)

users_bp = Blueprint('users', __name__)


def _forbidden(message="No autorizado"):
    return jsonify({"message": message}), 403


def _get_user_or_404(user_id):
    object_id = parse_object_id(user_id)
    if not object_id:
        return None, (jsonify({"message": "ID de usuario inválido"}), 400)
    user = mongo.db.usuarios.find_one({"_id": object_id})
    if not user:
        return None, (jsonify({"message": "Usuario no encontrado"}), 404)
    return user, None

@users_bp.route("/editar_usuario/<id_usuario>", methods=["PUT"])
@token_required
def editar_usuario(actor, id_usuario):
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
    actor_role = user_role(actor)
    if actor_role not in {ROLE_SUPER_ADMIN, ROLE_ADMIN_DEPARTAMENTO}:
        return _forbidden("No autorizado para editar usuarios")

    usuario, error_response = _get_user_or_404(id_usuario)
    if error_response:
        return error_response

    actor_department_id = user_department_id(actor)
    target_department_id = user_department_id(usuario)
    if is_admin_departamento(actor):
        if not actor_department_id:
            return _forbidden("admin_departamento no tiene departamento asociado")
        if actor_department_id != target_department_id:
            return _forbidden("Solo puedes editar usuarios de tu departamento")

    data = request.get_json(silent=True) or {}
    update_data = {}

    if "nombre" in data:
        update_data["nombre"] = data.get("nombre")
    if "email" in data:
        update_data["email"] = data.get("email")
    if "password" in data and data.get("password"):
        update_data["password"] = bcrypt.generate_password_hash(data.get("password")).decode('utf-8')

    has_department_change = ("departmentId" in data) or ("departamento_id" in data) or ("department_id" in data)
    if has_department_change:
        if "departmentId" in data:
            department_payload_value = data.get("departmentId")
        elif "departamento_id" in data:
            department_payload_value = data.get("departamento_id")
        else:
            department_payload_value = data.get("department_id")
        department_object_id, department_error = ensure_role_department_policy(
            usuario.get("rol") or ROLE_USUARIO,
            department_payload_value,
        )
        if department_error:
            return jsonify({"message": department_error}), 400

        if is_admin_departamento(actor):
            if not department_object_id or str(department_object_id) != actor_department_id:
                return _forbidden("Solo puedes mantener usuarios en tu departamento")

        update_data["departamento_id"] = department_object_id

    if not update_data:
        return jsonify({"message": "No hay campos válidos para actualizar"}), 400

    if "departamento_id" not in update_data:
        resolved_department = parse_object_id(usuario.get("departamento_id") or usuario.get("departmentId"))
    else:
        resolved_department = update_data.get("departamento_id")

    target_role = usuario.get("rol") or ROLE_USUARIO
    if target_role != ROLE_SUPER_ADMIN and not resolved_department:
        return jsonify({"message": "El usuario debe tener un departamento asociado"}), 400

    mongo.db.usuarios.update_one({"_id": usuario["_id"]}, {"$set": update_data})
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
    actor_role = user_role(user)
    if actor_role not in {ROLE_SUPER_ADMIN, ROLE_ADMIN_DEPARTAMENTO}:
        return _forbidden("No autorizado para eliminar usuarios")

    data = request.get_json(silent=True) or {}
    id_usuario = pick_value(data, "idUsuario", "id_usuario", "userId")
    if not id_usuario:
        return jsonify({"message": "idUsuario es requerido"}), 400

    usuario, error_response = _get_user_or_404(id_usuario)
    if error_response:
        return error_response

    if str(usuario.get("_id")) == str(user.get("sub")):
        return jsonify({"message": "No puedes eliminar tu propio usuario"}), 400

    if is_admin_departamento(user):
        actor_department_id = user_department_id(user)
        target_department_id = user_department_id(usuario)
        if not actor_department_id or actor_department_id != target_department_id:
            return _forbidden("Solo puedes eliminar usuarios de tu departamento")
        if (usuario.get("rol") or ROLE_USUARIO) != ROLE_USUARIO:
            return _forbidden("admin_departamento solo puede eliminar usuarios con rol usuario")

    result = mongo.db.usuarios.delete_one({"_id": usuario["_id"]})

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
def crear_rol(user):
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
    if not is_super_admin(user):
        return _forbidden("Solo super_admin puede crear roles")

    data = request.get_json(silent=True) or {}
    mongo.db.roles.insert_one(data)
    return jsonify({"message": "Rol creado con éxito"}), 201

@users_bp.route("/asignar_rol", methods=["PATCH"])
@token_required
def asignar_rol(user):
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
    if not is_super_admin(user):
        return _forbidden("Solo super_admin puede asignar roles")

    data = request.get_json(silent=True) or {}
    user_id = pick_value(data, "userId", "user_id")
    rol_id = pick_value(data, "roleId", "rol_id")
    if not user_id or not rol_id:
        return jsonify({"message": "userId y roleId son requeridos"}), 400
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
    actor_role = user_role(user)
    if actor_role not in {ROLE_SUPER_ADMIN, ROLE_ADMIN_DEPARTAMENTO}:
        return _forbidden("No autorizado para cambiar roles")

    data = request.get_json(silent=True) or {}
    usuario_id = pick_value(data, "id", "userId")
    nuevo_rol = normalize_role(pick_value(data, "rol", "role"))
    departamento_id = pick_value(data, "departmentId", "departamento_id", "department_id")

    if nuevo_rol not in VALID_ROLES:
        return jsonify({"message": f"Rol inválido. Debe ser uno de: {', '.join(VALID_ROLES)}"}), 400

    usuario, error_response = _get_user_or_404(usuario_id)
    if error_response:
        return error_response

    if is_admin_departamento(user):
        actor_department_id = user_department_id(user)
        target_department_id = user_department_id(usuario)
        if not actor_department_id:
            return _forbidden("admin_departamento no tiene departamento asociado")
        if actor_department_id != target_department_id:
            return _forbidden("Solo puedes cambiar roles de usuarios en tu departamento")
        if nuevo_rol != ROLE_USUARIO:
            return _forbidden("admin_departamento solo puede asignar el rol usuario")
        departamento_object_id, department_error = ensure_role_department_policy(nuevo_rol, actor_department_id)
        if department_error:
            return jsonify({"message": "admin_departamento no tiene un departamento válido asociado"}), 403
    else:
        departamento_object_id, department_error = ensure_role_department_policy(nuevo_rol, departamento_id)
        if department_error:
            return jsonify({"message": department_error}), 400

    update_data = {"rol": nuevo_rol}
    if departamento_object_id:
        update_data["departamento_id"] = departamento_object_id
    elif nuevo_rol == ROLE_SUPER_ADMIN:
        update_data["departamento_id"] = None

    mongo.db.usuarios.update_one({"_id": usuario["_id"]}, {"$set": update_data})

    return jsonify({"message": "Rol actualizado correctamente"}), 200

@users_bp.route("/mostrar_usuarios", methods=["GET"])
@allow_cors
@token_required
def mostrar_usuarios(user):
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
    page = int(params.get("page")) if params.get("page") else 0
    limit = int(params.get("limit")) if params.get("limit") else 10
    text = params.get("text")
    if page < 0:
        page = 0
    if limit <= 0:
        limit = 10

    filters = []
    if text:
        filters.append({
            "$or": [
                {"nombre": {"$regex": text, "$options": "i"}},
                {"email": {"$regex": text, "$options": "i"}},
            ]
        })

    scope_filter = department_scope_filter(user)
    if scope_filter:
        filters.append(scope_filter)

    if len(filters) > 1:
        query = {"$and": filters}
    elif len(filters) == 1:
        query = filters[0]
    else:
        query = {}

    projection = {"password": 0}
    list_users = mongo.db.usuarios.find(query, projection=projection).skip(page * limit).limit(limit)
    quantity = mongo.db.usuarios.count_documents(query)
    list_cursor = list(list_users)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))

    department_object_ids = []
    for item in list_json:
        dep_id = item.get("departamento_id")
        if isinstance(dep_id, dict):
            dep_id = dep_id.get("$oid")
            item["departamento_id"] = dep_id
        elif isinstance(dep_id, ObjectId):
            dep_id = str(dep_id)
            item["departamento_id"] = dep_id
        if dep_id:
            item["departmentId"] = dep_id
            if ObjectId.is_valid(dep_id):
                department_object_ids.append(ObjectId(dep_id))

    department_map = {}
    if department_object_ids:
        departamentos = mongo.db.departamentos.find(
            {"_id": {"$in": list({dept_id for dept_id in department_object_ids})}},
            {"codigo": 1, "nombre": 1},
        )
        department_map = {str(departamento["_id"]): departamento for departamento in departamentos}

    for item in list_json:
        dep_id = item.get("departmentId")
        if not dep_id:
            continue

        department = department_map.get(str(dep_id))
        if not department:
            continue

        item["departmentCode"] = department.get("codigo")
        item["departmentName"] = department.get("nombre")
        item["departamento"] = {
            "_id": dep_id,
            "codigo": department.get("codigo"),
            "nombre": department.get("nombre"),
        }

    return jsonify(request_list=list_json, count=quantity)
