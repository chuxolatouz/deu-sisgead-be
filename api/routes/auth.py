from flask import Blueprint, request, jsonify, current_app
from jose import jwt
from api.extensions import mongo, bcrypt
from api.util.utils import generar_token
from api.util.decorators import validar_datos
from api.config import Config
from api.util.access import (
    ROLE_ADMIN_DEPARTAMENTO,
    ROLE_SUPER_ADMIN,
    ROLE_USUARIO,
    VALID_ROLES,
    ensure_role_department_policy,
    normalize_role,
    pick_value,
    resolve_department_object_id,
    user_department_id,
    user_role,
)

auth_bp = Blueprint('auth', __name__)


def _actor_from_request():
    token = request.headers.get("Authorization")
    if not token:
        return None, None

    try:
        parts = token.split()
        if len(parts) != 2:
            return None, (jsonify({"message": "Token no es válido o ha expirado"}), 403)
        decoded = jwt.decode(parts[1], key=current_app.config["SECRET_KEY"], algorithms=["HS256"])
        return decoded, None
    except Exception:
        return None, (jsonify({"message": "Token no es válido o ha expirado"}), 403)

@auth_bp.route("/registrar", methods=["POST"])
@validar_datos({"nombre": str, "email": str, "password": str, "rol": str})
def registrar():
    """
    Registrar un nuevo usuario
    ---
    tags:
      - Autenticación
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - nombre
            - email
            - password
            - rol
          properties:
            nombre:
              type: string
              description: Nombre del usuario
              example: "Juan Pérez"
            email:
              type: string
              format: email
              description: Email del usuario
              example: "juan.perez@example.com"
            password:
              type: string
              format: password
              description: Contraseña del usuario
              example: "SecurePass123"
            rol:
              type: string
              enum: ["usuario", "admin_departamento", "super_admin"]
              description: Rol del usuario
              example: "usuario"
    responses:
      201:
        description: Usuario registrado con éxito
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Usuario registrado con éxito"
      400:
        description: El email ya está registrado o rol inválido
        schema:
          type: object
          properties:
            message:
              type: string
    """
    data = request.get_json(silent=True) or {}
    db_usuarios = mongo.db.usuarios

    usuario_existente = db_usuarios.find_one({"email": data["email"]})
    if usuario_existente:
        return jsonify({"message": "El email ya está registrado"}), 400

    actor, actor_error = _actor_from_request()
    if actor_error:
        return actor_error

    requested_role = normalize_role(data.get("rol"))
    if requested_role not in VALID_ROLES:
        return jsonify({"message": f"Rol inválido. Debe ser uno de: {', '.join(VALID_ROLES)}"}), 400

    users_count = db_usuarios.count_documents({})
    is_bootstrap = users_count == 0
    actor_role = user_role(actor) if actor else None

    if is_bootstrap:
        if requested_role != ROLE_SUPER_ADMIN:
            return jsonify({"message": "En la configuración inicial solo se permite crear un usuario super_admin"}), 403
    else:
        if not actor:
            return jsonify({"message": "Token no proporcionado"}), 403
        if actor_role not in {ROLE_SUPER_ADMIN, ROLE_ADMIN_DEPARTAMENTO}:
            return jsonify({"message": "No autorizado para crear usuarios"}), 403

    incoming_department_id = pick_value(data, "departmentId", "departamento_id", "department_id")
    if not is_bootstrap and actor_role == ROLE_ADMIN_DEPARTAMENTO:
        if requested_role != ROLE_USUARIO:
            return jsonify({"message": "admin_departamento solo puede crear usuarios con rol usuario"}), 403
        actor_department_id = user_department_id(actor)
        department_object_id, error = resolve_department_object_id(actor_department_id, required=True)
        if error:
            return jsonify({"message": "admin_departamento no tiene un departamento válido asociado"}), 403
    else:
        department_object_id, error = ensure_role_department_policy(requested_role, incoming_department_id)
        if error:
            return jsonify({"message": error}), 400

    payload = {
        "nombre": data["nombre"],
        "email": data["email"],
        "password": bcrypt.generate_password_hash(data["password"]).decode('utf-8'),
        "rol": requested_role,
    }
    if department_object_id:
        payload["departamento_id"] = department_object_id

    db_usuarios.insert_one(payload)
    return jsonify({"message": "Usuario registrado con éxito"}), 201

@auth_bp.route("/login", methods=["POST"])
@validar_datos({"email": str, "password": str})
def login():
    """
    Iniciar sesión
    ---
    tags:
      - Autenticación
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
            - password
          properties:
            email:
              type: string
              format: email
              description: Email del usuario
              example: "juan.perez@example.com"
            password:
              type: string
              format: password
              description: Contraseña del usuario
              example: "SecurePass123"
    responses:
      200:
        description: Login exitoso
        schema:
          type: object
          properties:
            token:
              type: string
              description: JWT token de autenticación
            email:
              type: string
            id:
              type: string
            nombre:
              type: string
            role:
              type: string
              enum: ["usuario", "admin_departamento", "super_admin"]
            departamento_id:
              type: string
              description: ID del departamento (opcional)
      401:
        description: Credenciales inválidas
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Credenciales inválidas"
    """
    data = request.get_json()
    db_usuarios = mongo.db.usuarios
    usuario = db_usuarios.find_one({"email": data["email"]})
    
    if usuario and bcrypt.check_password_hash(usuario["password"], data["password"]):
        token = generar_token(usuario, Config.SECRET_KEY)

        if "rol" in usuario:
            role = usuario["rol"]
        elif usuario.get("is_admin"):
            role = "super_admin"
        else:
            role = "usuario"

        response_data = {
            "token": token,
            "email": data["email"],
            "id": str(usuario["_id"]),
            "nombre": usuario["nombre"],
            "role": role,
        }
        
        user_department_id = usuario.get("departmentId") or usuario.get("departamento_id")
        if user_department_id:
            response_data["departamento_id"] = str(user_department_id)
            response_data["departmentId"] = str(user_department_id)

        return jsonify(response_data), 200
    else:
        return jsonify({"message": "Credenciales inválidas"}), 401

@auth_bp.route("/olvido_contraseña", methods=["POST"])
def olvido_contraseña():
    """
    Recuperar contraseña olvidada
    ---
    tags:
      - Autenticación
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
          properties:
            email:
              type: string
              format: email
              description: Email del usuario registrado
              example: "juan.perez@example.com"
    responses:
      200:
        description: Email enviado exitosamente
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Se ha enviado un email electrónico para restablecer la contraseña"
      404:
        description: Email no registrado
        schema:
          type: object
          properties:
            message:
              type: string
              example: "El email electrónico no está registrado"
    """
    data = request.get_json()
    db_usuarios = mongo.db.usuarios
    usuario = db_usuarios.find_one({"email": data["email"]})
    if usuario:
        # TODO: Implementar envío real de correo
        return jsonify({"message": "Se ha enviado un email electrónico para restablecer la contraseña"}), 200
    else:
        return jsonify({"message": "El email electrónico no está registrado"}), 404
