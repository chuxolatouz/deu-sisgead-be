from flask import Blueprint, request, jsonify
from api.extensions import mongo, bcrypt
from api.util.utils import generar_token
from api.util.decorators import validar_datos
from api.config import Config

auth_bp = Blueprint('auth', __name__)

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
    data = request.get_json()
    db_usuarios = mongo.db.usuarios
    
    usuario_existente = db_usuarios.find_one({"email": data["email"]})
    if usuario_existente:
        return jsonify({"message": "El email ya está registrado"}), 400
    
    hashed_pw = bcrypt.generate_password_hash(data["password"]).decode('utf-8')
    data["password"] = hashed_pw
    
    roles_permitidos = ["usuario", "admin_departamento", "super_admin"]
    if data.get("rol") not in roles_permitidos:
        return jsonify({"message": f"Rol inválido. Debe ser uno de: {', '.join(roles_permitidos)}"}), 400
    
    if "departamento_id" in data:
        del data["departamento_id"]
    
    db_usuarios.insert_one(data)
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
        
        if "departamento_id" in usuario:
            response_data["departamento_id"] = str(usuario["departamento_id"])

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
