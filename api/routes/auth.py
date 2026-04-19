from datetime import datetime, timedelta, timezone
import secrets
import string

from flask import Blueprint, request, jsonify, current_app
from jose import jwt
from api.extensions import mongo, bcrypt
from api.util.utils import generar_token
from api.util.decorators import token_required, validar_datos
from api.config import Config
from api.routes.notifications import send_email_notification_thread
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
TEMPORARY_PASSWORD_TTL_HOURS = 2


def _utc_now():
    return datetime.now(timezone.utc)


def _is_expired(value):
    if not value:
        return False
    if isinstance(value, dict) and "$date" in value:
        value = value["$date"]
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value < _utc_now()
    return False


def _generate_temporary_password(length=12):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _send_temporary_password_email(usuario, temporary_password, expires_at):
    expires_label = expires_at.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    body = f"""
    <p>Hola {usuario.get("nombre", "Usuario")},</p>
    <p>Se generó una contraseña temporal para ingresar a DEU SISGEAD.</p>
    <p><strong>Contraseña temporal:</strong> {temporary_password}</p>
    <p>Esta contraseña vence el {expires_label}. Al iniciar sesión deberás crear una contraseña nueva.</p>
    <p>Si no solicitaste este cambio, comunícate con el administrador del sistema.</p>
    """
    send_email_notification_thread(
        app=current_app._get_current_object(),
        subject="Recuperación de contraseña DEU",
        recipient=usuario["email"],
        body=body,
        is_html=True,
    )


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
        must_change_password = bool(usuario.get("mustChangePassword"))
        if must_change_password and _is_expired(usuario.get("temporaryPasswordExpiresAt")):
            return jsonify({
                "message": "La contraseña temporal expiró. Solicita una nueva recuperación de contraseña."
            }), 403

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
            "mustChangePassword": must_change_password,
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
    data = request.get_json(silent=True) or {}
    if not data.get("email"):
        return jsonify({"message": "El email es requerido"}), 400

    db_usuarios = mongo.db.usuarios
    usuario = db_usuarios.find_one({"email": data["email"]})
    if not usuario:
        return jsonify({"message": "El email electrónico no está registrado"}), 404

    temporary_password = _generate_temporary_password()
    expires_at = _utc_now() + timedelta(hours=TEMPORARY_PASSWORD_TTL_HOURS)
    db_usuarios.update_one(
        {"_id": usuario["_id"]},
        {
            "$set": {
                "password": bcrypt.generate_password_hash(temporary_password).decode("utf-8"),
                "mustChangePassword": True,
                "temporaryPasswordExpiresAt": expires_at,
            }
        },
    )

    usuario = {**usuario, "password": temporary_password}
    _send_temporary_password_email(usuario, temporary_password, expires_at)
    return jsonify({"message": "Se ha enviado un email electrónico para restablecer la contraseña"}), 200


@auth_bp.route("/change-password", methods=["POST"])
@token_required
def change_password(user):
    data = request.get_json(silent=True) or {}
    current_password = data.get("currentPassword") or data.get("current_password")
    new_password = data.get("newPassword") or data.get("new_password") or data.get("password")

    if not current_password or not new_password:
        return jsonify({"message": "currentPassword y newPassword son requeridos"}), 400
    if len(str(new_password)) < 6:
        return jsonify({"message": "La nueva contraseña debe tener al menos 6 caracteres"}), 400

    db_usuarios = mongo.db.usuarios
    usuario = db_usuarios.find_one({"email": user.get("email")})
    if not usuario:
        return jsonify({"message": "Usuario no encontrado"}), 404
    if not bcrypt.check_password_hash(usuario["password"], current_password):
        return jsonify({"message": "La contraseña actual no es válida"}), 401
    if usuario.get("mustChangePassword") and _is_expired(usuario.get("temporaryPasswordExpiresAt")):
        return jsonify({
            "message": "La contraseña temporal expiró. Solicita una nueva recuperación de contraseña."
        }), 403

    db_usuarios.update_one(
        {"_id": usuario["_id"]},
        {
            "$set": {
                "password": bcrypt.generate_password_hash(new_password).decode("utf-8"),
                "mustChangePassword": False,
                "temporaryPasswordExpiresAt": None,
                "passwordChangedAt": _utc_now(),
            }
        },
    )
    return jsonify({"message": "Contraseña actualizada con éxito"}), 200
