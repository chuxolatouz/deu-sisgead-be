import os
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_pymongo import PyMongo
from flask_bcrypt import Bcrypt
from flask_cors import CORS
from datetime import datetime, timezone
from bson import ObjectId, json_util
from api.util.backblaze import upload_file
from collections import defaultdict
from dateutil.relativedelta import relativedelta

from api.util.decorators import validar_datos, allow_cors, token_required
from api.util.generar_acta_finalizacion import generar_acta_finalizacion_pdf
from api.util.utils import (
    string_to_int,
    int_to_string,
    generar_token,
    map_to_doc,
    actualizar_pasos,
    generar_csv,
    generar_json
)
from flasgger import Swagger
import json
import random
import math
import string
from io import BytesIO


##pb
from flask import render_template
from fastapi import BackgroundTasks
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from pydantic import BaseModel
import os
from dotenv import load_dotenv

from flask_mail import Mail, Message
import threading # (Si estás usando el método de threading)
# Cerca del inicio de index.py
load_dotenv()
##end pb

### Swagger UI configuration ###f
SWAGGER_URL = '/swagger'   # URL for exposing Swagger UI (without trailing slash)
API_URL = '/swagger.json'  # Our API url route
print(os.getenv("FLASK_ENV"))
env_file = ".env.test" if os.getenv("FLASK_ENV") == "testing" else ".env"
load_dotenv(env_file)

app = Flask(__name__)
app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), 'templates'))

Swagger(app)

app.config["MONGO_URI"] = os.getenv("MONGODB_URI", "mongodb://localhost:27017/enii")
# app.config["MONGO_URI"] = "mongodb://localhost:27017/mi_db"
app.config["TESTING"] = os.getenv("FLASK_ENV") == "testing"
app.config["SECRET_KEY"] = "tu_clave_secreta"

app.config['MAIL_SERVER'] = os.getenv("SMTP_SERVER", "smtp.gmail.com")
app.config['MAIL_PORT'] = int(os.getenv("SMTP_PORT", 465)) # Usará 465
app.config['MAIL_USERNAME'] = os.getenv("SMTP_USER")
app.config['MAIL_PASSWORD'] = os.getenv("SMTP_PASSWORD")

# 🚨 CAMBIOS CLAVE PARA EL PUERTO 465 (SSL)
app.config['MAIL_USE_TLS'] = False  # <--- Ahora Falso
app.config['MAIL_USE_SSL'] = True   # <--- Ahora Verdadero

app.config['MAIL_DEFAULT_SENDER'] = os.getenv("EMAIL_SENDER")


MAIL_TLS=True,  # O False si no usas TLS
MAIL_SSL=False, # O True si usas SSL (puerto 465)
USE_CREDENTIALS=True,


mongo = PyMongo(app)
bcrypt = Bcrypt(app)
CORS(app, supports_credentials=True, resources={r"/*": {"origins": "http://localhost:3000"}})


# config(
#     cloud_name="dnfl6l0xp",
#     api_key="734477293158223",
#     api_secret="Xh4crQOUXkMG2TaMC_OT4yBx5Wc",
#     secure=True
# )

db_usuarios = mongo.db.usuarios
db_proyectos = mongo.db.proyectos
db_roles = mongo.db.roles
db_acciones = mongo.db.acciones
db_categorias = mongo.db.categorias
db_documentos = mongo.db.documentos
db_solicitudes = mongo.db.solicitudes
db_logs = mongo.db.logs
db_departamentos = mongo.db.departamentos

# Definir una función personalizada de serialización para manejar los objetos ObjectId


def agregar_log(id_proyecto, mensaje):
    data = {}
    data["id_proyecto"] = ObjectId(id_proyecto)
    data["fecha_creacion"] = datetime.now(timezone.utc)
    # data["usuario"] = user
    data["mensaje"] = mensaje

    db_logs.insert_one(data)

    return "registro agregado"


class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        return super().default(o)


class CustomJSONEncoder(JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        elif isinstance(o, datetime):
            return o.isoformat()
        return JSONEncoder.default(self, o)


# Configurar el codificador JSON de Flask para utilizar la función personalizada
app.json_encoder = JSONEncoder


@app.route("/registrar", methods=["POST"])
@validar_datos({"nombre": str, "email": str, "password": str, "rol": str})
def registrar():
    """
    Endpoint para registrar una persona en la plataforma.
    ---
    tags:
      - Usuarios
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            nombre:
              type: string
              example: "Juan"
            email:
              type: string
              example: "juan@example.com"
            password:
              type: string
              example: "password123"
            rol:
              type: string
              enum: [usuario, admin_departamento, super_admin]
              example: "usuario"
              description: "Rol del usuario. Valores permitidos: usuario, admin_departamento, super_admin"
    responses:
      201:
        description: Usuario registrado con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Usuario registrado con éxito"
      400:
        description: Error en los datos enviados o el email ya está registrado.
    """

    data = request.get_json()
    
    # Verificar que el usuario no exista (por email)
    usuario_existente = db_usuarios.find_one({"email": data["email"]})
    if usuario_existente:
        return jsonify({"message": "El email ya está registrado"}), 400
    
    hashed_pw = bcrypt.generate_password_hash(data["password"])
    data["password"] = hashed_pw
    
    # Validar que el rol sea uno de los permitidos
    roles_permitidos = ["usuario", "admin_departamento", "super_admin"]
    if data.get("rol") not in roles_permitidos:
        return jsonify({"message": f"Rol inválido. Debe ser uno de: {', '.join(roles_permitidos)}"}), 400
    
    # Eliminar departamento_id si se envía (se asigna después del registro)
    if "departamento_id" in data:
        del data["departamento_id"]
    
    db_usuarios.insert_one(data)
    return jsonify({"message": "Usuario registrado con éxito"}), 201


@app.route("/login", methods=["POST"])
@validar_datos({"email": str, "password": str})
def login():
    """
    Endpoint para iniciar sesión en la plataforma.
    ---
    tags:
      - Usuarios
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            email:
              type: string
              example: "juan@example.com"
            password:
              type: string
              example: "password123"
    responses:
      200:
        description: Inicio de sesión exitoso.
        schema:
          type: object
          properties:
            token:
              type: string
              example: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
            email:
              type: string
              example: "juan@example.com"
            id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
            nombre:
              type: string
              example: "Juan"
            role:
              type: string
              example: "usuario"
      401:
        description: Credenciales inválidas.
    """
    data = request.get_json()
    usuario = db_usuarios.find_one({"email": data["email"]})
    if usuario and bcrypt.check_password_hash(usuario["password"], data["password"]):
        token = generar_token(usuario, app.config["SECRET_KEY"])

        # Determinar el rol: usar el nuevo campo 'rol' si existe, sino mantener compatibilidad con is_admin
        if "rol" in usuario:
            role = usuario["rol"]
        elif usuario.get("is_admin"):
            role = "super_admin"  # Migración: is_admin = True -> super_admin
        else:
            role = "usuario"

        response_data = {
            "token": token,
            "email": data["email"],
            "id": str(usuario["_id"]),
            "nombre": usuario["nombre"],
            "role": role,
        }
        
        # Incluir departamento_id si existe
        if "departamento_id" in usuario:
            response_data["departamento_id"] = str(usuario["departamento_id"])

        return jsonify(response_data), 200
    else:
        return jsonify({"message": "Credenciales inválidas"}), 401


@app.route("/olvido_contraseña", methods=["POST"])
def olvido_contraseña():
    """
    Endpoint para solicitar restablecimiento de contraseña.
    ---
    tags:
      - Usuarios
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            email:
              type: string
              example: "juan@example.com"
    responses:
      200:
        description: Email enviado para restablecer la contraseña.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Se ha enviado un email electrónico para restablecer la contraseña"
      404:
        description: Email no registrado.
    """
    data = request.get_json()
    usuario = db_usuarios.find_one({"email": data["email"]})
    if usuario:
        # Enviar email electrónico con enlace para restablecer contraseña
        return jsonify(
            {
                "message": "Se ha enviado un email electrónico para restablecer la contraseña"
            }
        ), 200
    else:
        return jsonify({"message": "El email electrónico no está registrado"}), 404


@app.route("/editar_usuario/<id_usuario>", methods=["PUT"])
@token_required
def editar_usuario(id_usuario):
    """
    Endpoint para editar la información de un usuario.
    ---
    tags:
      - Usuarios
    parameters:
      - in: path
        name: id_usuario
        required: true
        description: ID del usuario a editar.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
      - in: body
        name: body
        required: true
        description: Datos a actualizar del usuario.
        schema:
          type: object
          properties:
            nombre:
              type: string
              example: "Juan Actualizado"
            email:
              type: string
              example: "juan_actualizado@example.com"
    responses:
      200:
        description: Información de usuario actualizada con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Información de usuario actualizada con éxito"
      404:
        description: Usuario no encontrado.
    """
    data = request.get_json()
    db_usuarios.update_one({"_id": ObjectId(id_usuario)}, {"$set": data})
    return jsonify({"message": "Información de usuario actualizada con éxito"}), 200


@app.route("/eliminar_usuario", methods=["POST"])
@token_required
def eliminar_usuario(user):
    """
    Endpoint para eliminar un usuario.
    ---
    tags:
      - Usuarios
    parameters:
      - in: body
        name: body
        required: true
        description: ID del usuario a eliminar.
        schema:
          type: object
          properties:
            id_usuario:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
    responses:
      200:
        description: Usuario eliminado exitosamente.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Usuario eliminado éxitosamente"
      400:
        description: No se pudo eliminar el usuario.
    """
    data = request.get_json()
    id_usuario = data["id_usuario"]
    result = db_usuarios.delete_one({"_id": ObjectId(id_usuario)})

    if result.deleted_count == 1:
        return jsonify({"message": "Usuario eliminado éxitosamente"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar el usuario"}), 400


@app.route("/crear_rol", methods=["POST"])
@token_required
def crear_rol():
    """
    Endpoint para crear un nuevo rol.
    ---
    tags:
      - Roles
    parameters:
      - in: body
        name: body
        required: true
        description: Datos del rol a crear.
        schema:
          type: object
          properties:
            nombre:
              type: string
              example: "Administrador"
            permisos:
              type: array
              items:
                type: string
              example: ["crear_usuario", "editar_usuario"]
    responses:
      201:
        description: Rol creado con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Rol creado con éxito"
    """
    data = request.get_json()
    db_roles.insert_one(data)
    return jsonify({"message": "Rol creado con éxito"}), 201


@app.route("/mostrar_categorias", methods=["GET"])
@allow_cors
def obtener_categorias():
    """
    Endpoint para obtener una lista de categorías.
    ---
    tags:
      - Categorías
    parameters:
      - in: query
        name: text
        required: false
        description: Texto para filtrar las categorías por nombre.
        schema:
          type: string
          example: "educación"
    responses:
      200:
        description: Lista de categorías obtenida con éxito.
        schema:
          type: array
          items:
            type: object
            properties:
              _id:
                type: string
                example: "64b8f3e2c9d1a2b3c4d5e6f7"
              nombre:
                type: string
                example: "Educación"
              color:
                type: string
                example: "#FF5733"
    """
    search_text = request.args.get("text")
    if search_text:
        cursor = db_categorias.find(
            {"nombre": {"$regex": search_text, "$options": "i"}}
        )
    else:
        cursor = db_categorias.find()
    
    list_cursor = list(cursor)
    list_dump = json.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    # Remover las barras invertidas
    list_json = json.loads(list_dump.replace("\\", ""))
    return jsonify(list_json), 200


@app.route("/categorias", methods=["POST"])
@allow_cors
@validar_datos({"nombre": str})
def crear_categorias():
    """
    Endpoint para crear una nueva categoría.
    ---
    tags:
      - Categorías
    parameters:
      - in: body
        name: body
        required: true
        description: Datos de la categoría a crear.
        schema:
          type: object
          properties:
            nombre:
              type: string
              example: "Educación"
    responses:
      201:
        description: Categoría creada con éxito.
        schema:
          type: object
          properties:
            id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
            nombre:
              type: string
              example: "Educación"
            color:
              type: string
              example: "#FF5733"
    """
    data = request.get_json()
    nombre = data["nombre"]
    color = "".join(random.choices(string.hexdigits[:-6], k=6))
    categoria = {"nombre": nombre, "color": color}
    categoria_insertada = db_categorias.insert_one(categoria)
    print("Categoría insertada:", categoria_insertada.inserted_id)
    
    return jsonify({"message": "Categoría creada con éxito", "_id": str(categoria_insertada.inserted_id)}), 201

@app.route("/departamentos", methods=["POST"])
@allow_cors
@token_required
@validar_datos({"nombre": str, "descripcion": str, "codigo": str})
def crear_departamento(user):
    """
    Endpoint para crear un nuevo departamento.
    ---
    tags:
      - Departamentos
    parameters:
      - in: body
        name: body
        required: true
        description: Datos del departamento a crear.
        schema:
          type: object
          properties:
            nombre:
              type: string
              example: "Gestión de Proyectos"
            descripcion:
              type: string
              example: "Departamento encargado de la gestión de proyectos"
            codigo:
              type: string
              example: "GP"
    responses:
      201:
        description: Departamento creado con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Departamento creado con éxito"
            _id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
      400:
        description: Error en los datos enviados.
    """
    data = request.get_json()
    departamento = {
        "nombre": data["nombre"],
        "descripcion": data["descripcion"],
        "codigo": data["codigo"],
        "fecha_creacion": datetime.now(timezone.utc),
        "activo": True
    }
    departamento_insertado = db_departamentos.insert_one(departamento)
    return jsonify({"message": "Departamento creado con éxito", "_id": str(departamento_insertado.inserted_id)}), 201

@app.route("/departamentos", methods=["GET"])
@allow_cors
def listar_departamentos():
    """
    Endpoint para obtener la lista de departamentos.
    ---
    tags:
      - Departamentos
    parameters:
      - in: query
        name: activo
        required: false
        description: Filtrar por departamentos activos (true/false).
        schema:
          type: boolean
          example: true
    responses:
      200:
        description: Lista de departamentos obtenida con éxito.
        schema:
          type: array
          items:
            type: object
            properties:
              _id:
                type: string
                example: "64b8f3e2c9d1a2b3c4d5e6f7"
              nombre:
                type: string
                example: "Gestión de Proyectos"
              descripcion:
                type: string
                example: "Departamento encargado de la gestión de proyectos"
              codigo:
                type: string
                example: "GP"
              fecha_creacion:
                type: string
                example: "2025-01-15T10:30:00Z"
              activo:
                type: boolean
                example: true
    """
    params = request.args
    query = {}
    if params.get("activo") is not None:
        query["activo"] = params.get("activo").lower() == "true"
    
    departamentos = db_departamentos.find(query)
    list_cursor = list(departamentos)
    list_dump = json.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    return jsonify(list_json), 200

@app.route("/departamentos/<string:departamento_id>", methods=["GET"])
@allow_cors
def obtener_departamento(departamento_id):
    """
    Endpoint para obtener un departamento por ID.
    ---
    tags:
      - Departamentos
    parameters:
      - in: path
        name: departamento_id
        required: true
        description: ID del departamento a obtener.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
    responses:
      200:
        description: Departamento obtenido con éxito.
        schema:
          type: object
          properties:
            _id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
            nombre:
              type: string
              example: "Gestión de Proyectos"
            descripcion:
              type: string
              example: "Departamento encargado de la gestión de proyectos"
            codigo:
              type: string
              example: "GP"
            fecha_creacion:
              type: string
              example: "2025-01-15T10:30:00Z"
            activo:
              type: boolean
              example: true
      404:
        description: Departamento no encontrado.
      400:
        description: ID de departamento inválido.
    """
    try:
        departamento_id_obj = ObjectId(departamento_id.strip())
    except Exception:
        return jsonify({"message": "ID de departamento inválido"}), 400
    
    departamento = db_departamentos.find_one({"_id": departamento_id_obj})
    
    if not departamento:
        return jsonify({"message": "Departamento no encontrado"}), 404
    
    # Convertir ObjectId a string para la respuesta JSON
    departamento["_id"] = str(departamento["_id"])
    
    # Serializar la respuesta
    departamento_dump = json.dumps(departamento, default=json_util.default, ensure_ascii=False)
    departamento_json = json.loads(departamento_dump.replace("\\", ""))
    
    return jsonify(departamento_json), 200

@app.route("/departamentos/<string:departamento_id>", methods=["PUT"])
@allow_cors
@token_required
def actualizar_departamento(user, departamento_id):
    """
    Endpoint para actualizar un departamento.
    ---
    tags:
      - Departamentos
    parameters:
      - in: path
        name: departamento_id
        required: true
        description: ID del departamento a actualizar.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
      - in: body
        name: body
        required: true
        description: Datos a actualizar del departamento.
        schema:
          type: object
          properties:
            nombre:
              type: string
              example: "Gestión de Proyectos Actualizado"
            descripcion:
              type: string
              example: "Descripción actualizada"
            codigo:
              type: string
              example: "GP"
            activo:
              type: boolean
              example: true
    responses:
      200:
        description: Departamento actualizado con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Departamento actualizado con éxito"
      404:
        description: Departamento no encontrado.
    """
    data = request.get_json()
    departamento = db_departamentos.find_one({"_id": ObjectId(departamento_id)})
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
    
    db_departamentos.update_one({"_id": ObjectId(departamento_id)}, {"$set": update_data})
    return jsonify({"message": "Departamento actualizado con éxito"}), 200

@app.route("/departamentos/<string:departamento_id>", methods=["DELETE"])
@allow_cors
@token_required
def eliminar_departamento(user, departamento_id):
    """
    Endpoint para eliminar un departamento.
    ---
    tags:
      - Departamentos
    parameters:
      - in: path
        name: departamento_id
        required: true
        description: ID del departamento a eliminar.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
    responses:
      200:
        description: Departamento eliminado con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Departamento eliminado con éxito"
      404:
        description: Departamento no encontrado.
    """
    departamento = db_departamentos.find_one({"_id": ObjectId(departamento_id)})
    if not departamento:
        return jsonify({"message": "Departamento no encontrado"}), 404
    
    result = db_departamentos.delete_one({"_id": ObjectId(departamento_id)})
    if result.deleted_count == 1:
        return jsonify({"message": "Departamento eliminado con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar el departamento"}), 400

@app.route("/cambiar_rol_usuario", methods=["POST"])
@allow_cors
@validar_datos({"id": str, "rol": str})
@token_required
def cambiar_rol_usuario(user):
    """
    Endpoint para cambiar el rol de un usuario en el sistema.
    ---
    tags:
      - Usuarios
    parameters:
      - in: body
        name: body
        required: true
        description: Datos para cambiar el rol del usuario.
        schema:
          type: object
          properties:
            id:
              type: string
              example: "64a12fabc123456789012345"
            rol:
              type: string
              enum: [usuario, admin_departamento, super_admin]
              example: "admin_departamento"
            departamento_id:
              type: string
              required: false
              example: "64a12fabc123456789012346"
    responses:
      200:
        description: Rol del usuario actualizado correctamente.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Rol actualizado correctamente"
      404:
        description: Usuario no encontrado.
      400:
        description: Datos inválidos o faltantes.
    """
    data = request.get_json()
    usuario_id = data.get("id")
    nuevo_rol = data.get("rol")
    departamento_id = data.get("departamento_id")

    # Validar que el rol sea uno de los permitidos
    roles_permitidos = ["usuario", "admin_departamento", "super_admin"]
    if nuevo_rol not in roles_permitidos:
        return jsonify({"message": f"Rol inválido. Debe ser uno de: {', '.join(roles_permitidos)}"}), 400

    usuario = mongo.db.usuarios.find_one({"_id": ObjectId(usuario_id)})
    if not usuario:
        return jsonify({"message": "Usuario no encontrado"}), 404

    update_data = {"rol": nuevo_rol}
    
    # Si se proporciona departamento_id, validar que existe
    if departamento_id:
        try:
            departamento = db_departamentos.find_one({"_id": ObjectId(departamento_id)})
            if not departamento:
                return jsonify({"message": "Departamento no encontrado"}), 400
            update_data["departamento_id"] = ObjectId(departamento_id)
        except Exception:
            return jsonify({"message": "ID de departamento inválido"}), 400
    elif "departamento_id" in data and data["departamento_id"] is None:
        # Permitir eliminar el departamento_id si se envía explícitamente como None
        update_data["departamento_id"] = None

    mongo.db.usuarios.update_one(
        {"_id": ObjectId(usuario_id)},
        {"$set": update_data}
    )

    return jsonify({"message": "Rol actualizado correctamente"}), 200

@app.route("/contexto_departamento", methods=["GET"])
@allow_cors
@token_required
def obtener_contexto_departamento(user):
    """
    Endpoint para obtener el contexto actual del departamento.
    Solo disponible para super_admin. Retorna el departamento_id del contexto
    si está activo, o null si no hay contexto activo.
    ---
    tags:
      - Departamentos
    parameters:
      - in: header
        name: X-Department-Context
        required: false
        description: ID del departamento para el contexto (solo para super_admin)
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
    responses:
      200:
        description: Contexto del departamento obtenido con éxito.
        schema:
          type: object
          properties:
            departamento_id:
              type: string
              nullable: true
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
            usando_contexto:
              type: boolean
              example: true
            departamento:
              type: object
              nullable: true
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
        description: Solo super_admin puede usar este endpoint.
    """
    # Solo super_admin puede usar este endpoint
    if user.get("role") != "super_admin":
        return jsonify({"message": "Solo super_admin puede usar este endpoint"}), 403
    
    # Obtener el contexto del header
    dept_context = request.headers.get("X-Department-Context")
    usando_contexto = False
    departamento = None
    
    if dept_context:
        try:
            dept_id_obj = ObjectId(dept_context.strip())
            # Validar que el departamento existe
            dept = db_departamentos.find_one({"_id": dept_id_obj})
            if dept:
                usando_contexto = True
                departamento = {
                    "_id": str(dept["_id"]),
                    "nombre": dept.get("nombre", ""),
                    "descripcion": dept.get("descripcion", ""),
                    "codigo": dept.get("codigo", ""),
                }
        except Exception:
            # Si hay error, no hay contexto válido
            pass
    
    return jsonify({
        "departamento_id": dept_context.strip() if dept_context and usando_contexto else None,
        "usando_contexto": usando_contexto,
        "departamento": departamento
    }), 200

@app.route("/asignar_rol", methods=["PATCH"])
@token_required
def asignar_rol():
    """
    Endpoint para asignar un rol a un usuario.
    ---
    tags:
      - Roles
    parameters:
      - in: body
        name: body
        required: true
        description: Datos para asignar el rol.
        schema:
          type: object
          properties:
            user_id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
            rol_id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f8"
    responses:
      200:
        description: Rol asignado con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Rol asignado con éxito"
    """
    data = request.get_json()
    user_id = data["user_id"]
    rol_id = data["rol_id"]
    db_usuarios.update_one(
        {"_id": ObjectId(user_id)}, {"$set": {"rol_id": ObjectId(rol_id)}}
    )
    return jsonify({"message": "Rol asignado con éxito"}), 200


@app.route("/crear_proyecto", methods=["POST"])
@allow_cors
@token_required
@validar_datos(
    {"nombre": str, "descripcion": str, "fecha_inicio": str, "fecha_fin": str}
)
def crear_proyecto(user):
    """
    Endpoint para crear un nuevo proyecto.
    ---
    tags:
      - Proyectos
    parameters:
      - in: body
        name: body
        required: true
        description: Datos del proyecto a crear.
        schema:
          type: object
          properties:
            nombre:
              type: string
              example: "Proyecto de Desarrollo"
            descripcion:
              type: string
              example: "Descripción del proyecto"
            fecha_inicio:
              type: string
              example: "2025-01-01"
            fecha_fin:
              type: string
              example: "2025-12-31"
            departamento_id:
              type: string
              required: false
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
              description: ID del departamento al que pertenece el proyecto (opcional)
    responses:
      201:
        description: Proyecto creado con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Proyecto creado con éxito"
            _id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
      400:
        description: Error en los datos enviados o departamento no encontrado.
    """
    current_user = user["sub"]
    data = request.get_json()
    
    # Manejar departamento_id
    departamento_id = None
    
    # Si se proporciona departamento_id en el body, validarlo
    if "departamento_id" in data and data["departamento_id"]:
        try:
            dept_id_obj = ObjectId(data["departamento_id"])
            # Validar que el departamento existe
            departamento = db_departamentos.find_one({"_id": dept_id_obj})
            if not departamento:
                return jsonify({"message": "Departamento no encontrado"}), 400
            departamento_id = dept_id_obj
        except Exception:
            return jsonify({"message": "ID de departamento inválido"}), 400
    # Si no se proporciona, usar el departamento del usuario si tiene contexto o es admin_departamento
    elif user.get("_using_dept_context") and "departamento_id" in user:
        # Usuario super_admin con contexto de departamento
        try:
            departamento_id = ObjectId(user["departamento_id"])
        except Exception:
            pass
    elif user.get("role") == "admin_departamento" and "departamento_id" in user:
        # Usuario admin_departamento, usar su departamento
        try:
            departamento_id = ObjectId(user["departamento_id"])
        except Exception:
            pass
    
    # Preparar datos del proyecto
    data["miembros"] = []
    data["balance"] = 000
    data["balance_inicial"] = 000
    if "status" not in data or not isinstance(data["status"], dict):
      data["status"] = {
          "actual": 1,
          "completado": []
      }
    data["show"] = {"status": False}
    data["owner"] = ObjectId(current_user)
    data["user"] = user
    
    # Asignar departamento_id si se determinó uno
    if departamento_id:
        data["departamento_id"] = departamento_id
    
    project = db_proyectos.insert_one(data)
    message_log = "Usuario %s ha creado el proyecto" % user["nombre"]
    agregar_log(project.inserted_id, message_log)
    return jsonify({"message": "Proyecto creado con éxito", "_id": str(project.inserted_id)}), 201


@app.route("/actualizar_proyecto/<project_id>", methods=["PUT"])
@token_required
@validar_datos(
    {"nombre": str, "descripcion": str}
)
def actualizar_proyecto(user, project_id):
    """
    Endpoint para actualizar un proyecto existente.
    ---
    tags:
      - Proyectos
    parameters:
      - in: path
        name: project_id
        required: true
        description: ID del proyecto a actualizar.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
      - in: body
        name: body
        required: true
        description: Datos a actualizar del proyecto.
        schema:
          type: object
          properties:
            nombre:
              type: string
              example: "Proyecto A Actualizado"
            descripcion:
              type: string
              example: "Descripción actualizada del proyecto A"
            fecha_inicio:
              type: string
              example: "2025-05-01"
            fecha_fin:
              type: string
              example: "2025-12-31"
    responses:
      200:
        description: Proyecto actualizado con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Proyecto actualizado con éxito"
      404:
        description: Proyecto no encontrado.
    """
    data = request.get_json()

    # Check if project exists
    project = db_proyectos.find_one({"_id": ObjectId(project_id)})
    if not project:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    # Ensure only the owner can update the project
    # if project["owner"] != ObjectId(current_user):
    #     return jsonify({"message": "No tienes permiso para actualizar este proyecto"}), 403

    # Update specified fields
    for key, value in data.items():
        project[key] = value

    # Save updated project
    db_proyectos.update_one({"_id": ObjectId(project_id)}, {"$set": project})

    message_log = "Usuario %s ha actualizado el proyecto" % user["nombre"]
    agregar_log(project_id, message_log)

    return jsonify({"message": "Proyecto actualizado con éxito"}), 200


@app.route("/asignar_usuario_proyecto", methods=["PATCH"])
@allow_cors
@token_required
def asignar_usuario_proyecto(user):
    """
    Endpoint para asignar un usuario a un proyecto.
    ---
    tags:
      - Proyectos
    parameters:
      - in: body
        name: body
        required: true
        description: Datos para asignar un usuario al proyecto.
        schema:
          type: object
          properties:
            proyecto_id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
            usuario:
              type: object
              properties:
                _id:
                  type: string
                  example: "64b8f3e2c9d1a2b3c4d5e6f8"
                nombre:
                  type: string
                  example: "Juan"
                role:
                  type: object
                  properties:
                    value:
                      type: string
                      example: "lider"
                    label:
                      type: string
                      example: "Líder"
    responses:
      200:
        description: Usuario asignado al proyecto con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Usuario asignado al proyecto con éxito"
      400:
        description: El usuario ya es miembro del proyecto.
    """
    data = request.get_json()
    proyecto_id = data["proyecto_id"]
    usuario = data["usuario"]
    fecha_hora_actual = datetime.now(timezone.utc)
    data["fecha_ingreso"] = fecha_hora_actual.strftime("%d/%m/%Y %H:%M")
    # Verificar si el usuario ya está en la lista de miembros
    proyecto = db_proyectos.find_one({"_id": ObjectId(proyecto_id)})
    miembros = proyecto["miembros"]

    if any(
        miembro["usuario"]["_id"]["$oid"] == data["usuario"]["_id"]["$oid"]
        for miembro in miembros
    ):
        return jsonify({"message": "El usuario ya es miembro del proyecto"}), 400

    new_status = {}
    query = {"$push": {"miembros": data}}
    # Agregar el usuario a la lista de miembros
    if 2 not in proyecto["status"]["completado"]:
        new_status, _ = actualizar_pasos(proyecto["status"], 2)

    if data["role"]["value"] == "lider":
        new_status, _ = actualizar_pasos(proyecto["status"], 3)

    if bool(new_status):
        query["$set"] = {"status": new_status}

    db_proyectos.update_one({"_id": ObjectId(proyecto_id)}, query)
    message_log = f'{usuario["nombre"]} fue asignado al proyecto por {user["nombre"]} con el rol {data["role"]["label"]}'
    agregar_log(proyecto_id, message_log)
    return jsonify({"message": "Usuario asignado al proyecto con éxito"}), 200


@app.route("/eliminar_usuario_proyecto", methods=["PATCH"])
@allow_cors
@token_required
def eliminar_usuario_proyecto(user):
    """
    Endpoint para eliminar un usuario de un proyecto.
    ---
    tags:
      - Proyectos
    parameters:
      - in: body
        name: body
        required: true
        description: Datos para eliminar un usuario del proyecto.
        schema:
          type: object
          properties:
            proyecto_id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
            usuario_id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f8"
    responses:
      200:
        description: Usuario eliminado del proyecto con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Usuario eliminado del proyecto con éxito"
      400:
        description: El usuario no es miembro del proyecto.
    """
    data = request.get_json()
    proyecto_id = data["proyecto_id"]
    usuario_id = data["usuario_id"]

    # Verificar si el usuario ya está en la lista de miembros
    proyecto = db_proyectos.find_one({"_id": ObjectId(proyecto_id)})
    miembros = proyecto["miembros"]
    usuario = None
    for miembro in miembros:
        if miembro["usuario"]["_id"]["$oid"] == usuario_id:
            usuario = miembro["usuario"]
            break
    if usuario is None:
        return jsonify({"message": "El usuario no es miembro del proyecto"}), 400

    # Eliminar el usuario de la lista de miembros
    db_proyectos.update_one(
        {"_id": ObjectId(proyecto_id)},
        {"$pull": {"miembros": {"usuario._id.$oid": usuario_id}}},
    )
    message_log = f'{usuario["nombre"]} fue eliminado del proyecto por {user["nombre"]}'
    agregar_log(proyecto_id, message_log)
    return jsonify({"message": "Usuario eliminado del proyecto con éxito"}), 200


@app.route("/asignar_regla_distribucion", methods=["POST"])
@allow_cors
@token_required
def asignar_regla_distribucion(user):
    """
    Endpoint para asignar una regla de distribución a un proyecto.
    ---
    tags:
      - Proyectos
    parameters:
      - in: body
        name: body
        required: true
        description: Datos para asignar la regla de distribución.
        schema:
          type: object
          properties:
            proyecto_id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
            regla_distribucion:
              type: object
              additionalProperties:
                type: number
              example:
                lider: 50
                miembro: 50
    responses:
      200:
        description: Regla de distribución asignada con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Regla de distribución establecida con éxito"
      400:
        description: El proyecto ya cuenta con una regla de distribución.
    """
    data = request.get_json()
    proyecto_id = data["proyecto_id"]
    regla_distribucion = data["regla_distribucion"]
    proyecto = db_proyectos.find_one({"_id": ObjectId(proyecto_id)})

    if 4 not in proyecto["status"]["completado"]:
        new_status, _ = actualizar_pasos(proyecto["status"], 4)
        db_proyectos.update_one(
            {"_id": ObjectId(proyecto_id)},
            {"$set": {"status": new_status, "reglas": regla_distribucion}},
        )

        message_log = (
            f'{user["nombre"]} establecio las reglas de distribucion del proyecto'
        )
        agregar_log(proyecto_id, message_log)
        return jsonify({"message": "Regla de distribución establecida con éxito"}), 200

    return jsonify({"message": "El proyecto ya cuenta con regla de distribución"}), 200


@app.route("/crear_solicitud_regla_fija", methods=["POST"])
@token_required
def crear_solicitud_regla_fija(user):
    """
    Endpoint para crear una solicitud de regla fija.
    ---
    tags:
      - Reglas fijas
    parameters:
      - in: body
        name: body
        required: true
        description: Datos para crear la solicitud de regla fija.
        schema:
          type: object
          properties:
            name:
              type: string
              example: "Regla Fija 1"
            items:
              type: array
              items:
                type: object
                properties:
                  nombre_regla:
                    type: string
                    example: "Regla 1"
                  monto:
                    type: number
                    example: 1000
    responses:
      200:
        description: Solicitud de regla fija creada con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Solicitud de regla creada con éxito"
    """
    data = request.get_json()
    solicitud_regla = {}
    items = data["items"]
    for item in items:
        item["monto"] = item["monto"] * 100

    solicitud_regla["nombre"] = data["name"]
    solicitud_regla["reglas"] = items
    solicitud_regla["status"] = "new"
    solicitud_regla["usuario"] = user
    db_solicitudes.insert_one(solicitud_regla)
    return jsonify({"message": "Solicitud de regla creada con éxito"}), 200


# TODO: Agregar el tema de los decorators con multiples ID


@app.route("/eliminar_solicitud_regla_fija/<string:id>", methods=["POST"])
@allow_cors
def eliminar_solicitud_regla_fija(id):
    """
    Endpoint para eliminar una solicitud de regla fija.
    ---
    tags:
      - Reglas fijas
    parameters:
      - in: path
        name: id
        required: true
        description: ID de la solicitud de regla fija a eliminar.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
    responses:
      200:
        description: Solicitud de regla fija eliminada con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Solicitud de regla eliminada con éxito"
      400:
        description: No se pudo eliminar la solicitud de regla fija.
    """
    query = {"_id": ObjectId(id)}
    result = db_solicitudes.delete_one(query)
    if result.deleted_count == 1:
        return jsonify({"message": "Solicitud de regla eliminada con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar la regla"}), 400


@app.route("/completar_solicitud_regla_fija/<string:id>", methods=["POST"])
@allow_cors
def completar_solicitud_regla_fija(id):
    """
    Endpoint para completar una solicitud de regla fija.
    ---
    tags:
      - Reglas fijas
    parameters:
      - in: path
        name: id
        required: true
        description: ID de la solicitud de regla fija a completar.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
      - in: body
        name: body
        required: true
        description: Resolución de la solicitud.
        schema:
          type: object
          properties:
            resolution:
              type: string
              example: "completed"
    responses:
      200:
        description: Solicitud de regla fija completada con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Solicitud de regla eliminada con éxito"
      400:
        description: No se pudo completar la solicitud de regla fija.
    """
    data = request.get_json()
    resolution = data["resolution"]
    query = {"_id": ObjectId(id)}
    result = db_solicitudes.update_one(query, {"$set": {"status": resolution}})
    if result.modified_count == 1:
        return jsonify({"message": "Solicitud de regla eliminada con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar la regla"}), 400


@app.route("/asignar_balance", methods=["PATCH"])
@allow_cors
@token_required
def asignar_balance(user):
    """
    Endpoint para asignar balance a un proyecto.
    ---
    tags:
      - Proyectos
    parameters:
      - in: body
        name: body
        required: true
        description: Datos para asignar balance al proyecto.
        schema:
          type: object
          properties:
            project_id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
            balance:
              type: string
              example: "1000.00"
    responses:
      200:
        description: Balance asignado con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Balance asignado con éxito"
    """
    data = request.get_json()
    proyecto_id = data["project_id"]
    proyecto = db_proyectos.find_one({"_id": ObjectId(proyecto_id)})
    data_balance = string_to_int(data["balance"])
    balance = data_balance + int(proyecto["balance"])
    new_changes = {"balance": balance}

    if 1 not in proyecto["status"]["completado"]:
        new_status, _ = actualizar_pasos(proyecto["status"], 1)
        print(new_status)
        new_changes["status"] = new_status
        new_changes["balance_inicial"] = balance

    db_proyectos.update_one({"_id": ObjectId(proyecto_id)}, {"$set": new_changes})
    data_acciones = {}
    data_acciones["project_id"] = ObjectId(proyecto_id)
    data_acciones["user"] = "Prueba"
    data_acciones["type"] = "Fondeo"
    data_acciones["amount"] = data_balance
    data_acciones["total_amount"] = balance
    data_acciones["created_at"] = datetime.utcnow()
    db_acciones.insert_one(data_acciones)
    message_log = f'{user["nombre"]} agrego balance al proyecto por un monto de: ${int_to_string(data_balance)}'
    agregar_log(proyecto_id, message_log)

    return jsonify({"message": "Balance asignado con éxito"}), 200


@app.route("/roles", methods=["GET"])
@allow_cors
def roles():
    """
    Endpoint para obtener la lista de roles.
    ---
    tags:
      - Roles
    responses:
      200:
        description: Lista de roles obtenida con éxito.
        schema:
          type: array
          items:
            type: object
            properties:
              _id:
                type: string
                example: "64b8f3e2c9d1a2b3c4d5e6f7"
              nombre:
                type: string
                example: "Administrador"
              permisos:
                type: array
                items:
                  type: string
                example: ["crear_usuario", "editar_usuario"]
    """
    roles = db_roles.find({})
    list_cursor = list(roles)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    # Remover las barras invertidas
    list_json = json.loads(list_dump.replace("\\", ""))
    list_json = jsonify(list_json)
    return list_json


@app.route("/mostrar_usuarios", methods=["GET"])
@allow_cors
def mostrar_usuarios():
    """
    Endpoint para obtener la lista de usuarios.
    ---
    tags:
      - Usuarios
    parameters:
      - in: query
        name: page
        required: false
        description: Número de página para la paginación.
        schema:
          type: integer
          example: 1
      - in: query
        name: limit
        required: false
        description: Límite de usuarios por página.
        schema:
          type: integer
          example: 10
      - in: query
        name: text
        required: false
        description: Texto para filtrar usuarios por nombre o email.
        schema:
          type: string
          example: "Juan"
    responses:
      200:
        description: Lista de usuarios obtenida con éxito.
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
                    example: "64b8f3e2c9d1a2b3c4d5e6f7"
                  nombre:
                    type: string
                    example: "Juan"
                  email:
                    type: string
                    example: "juan@example.com"
            count:
              type: integer
              example: 100
    """
    params = request.args
    skip = int(params.get("page")) if params.get("page") else 0
    limit = params.get("limit") if params.get("limit") else 10
    text = params.get("text")

    query = {}
    if text:
        query = {
            "$or": [
                {"nombre": {"$regex": text, "$options": "i"}},
                {"email": {"$regex": text, "$options": "i"}},
            ]
        }
    list_users = db_usuarios.find().skip(skip * limit).limit(limit)
    quantity = db_usuarios.count_documents(query)
    list_cursor = list(list_users)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    # Remover las barras invertidas
    list_json = json.loads(list_dump.replace("\\", ""))
    list_json = jsonify(request_list=list_json, count=quantity)
    return list_json


@app.route("/mostrar_proyectos", methods=["GET"])
@allow_cors
@token_required
def mostrar_proyectos(user):
    """
    Endpoint para obtener la lista de proyectos.
    ---
    tags:
      - Proyectos
    parameters:
      - in: query
        name: page
        required: false
        description: Número de página para la paginación.
        schema:
          type: integer
          example: 1
      - in: query
        name: limit
        required: false
        description: Límite de proyectos por página.
        schema:
          type: integer
          example: 10
    responses:
      200:
        description: Lista de proyectos obtenida con éxito.
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
                    example: "64b8f3e2c9d1a2b3c4d5e6f7"
                  nombre:
                    type: string
                    example: "Proyecto A"
                  descripcion:
                    type: string
                    example: "Descripción del proyecto A"
                  balance:
                    type: string
                    example: "1000.00"
            count:
              type: integer
              example: 50
    """
    # Obtener el número de página actual
    params = request.args
    skip = int(params.get("page")) if params.get("page") else 0
    limit = params.get("limit") if params.get("limit") else 10
    query = {
        "$or": [
            {"owner": user["sub"]},  # Check for project owner
            {
                "miembros": {
                    "$elemMatch": {  # Check for membership in any project
                        "usuario._id.$oid": user["sub"]  # Match user ID
                    }
                }
            },
        ]
    }
    dir(user)
    # Super admin puede ver todos los proyectos, a menos que tenga contexto de departamento
    if user.get("role") == "super_admin":
        # Si super_admin tiene contexto de departamento, filtrar como admin_departamento
        if user.get("_using_dept_context") and "departamento_id" in user:
            try:
                dept_id = ObjectId(user["departamento_id"])
                query = {
                    "$or": [
                        {"departamento_id": dept_id},  # Proyectos del departamento del contexto
                        {"owner": user["sub"]},  # Proyectos que es dueño
                        {
                            "miembros": {
                                "$elemMatch": {
                                    "usuario._id.$oid": user["sub"]
                                }
                            }
                        },  # Proyectos donde es miembro
                    ]
                }
            except Exception:
                # Si hay error con el ObjectId, mantener la query original (ver todos)
                query = {}
        else:
            # Sin contexto, ver todos los proyectos
            query = {}
    # Admin de departamento puede ver proyectos de su departamento, además de los que es dueño o miembro
    elif user.get("role") == "admin_departamento" and "departamento_id" in user:
        try:
            dept_id = ObjectId(user["departamento_id"])
            query = {
                "$or": [
                    {"departamento_id": dept_id},  # Proyectos de su departamento
                    {"owner": user["sub"]},  # Proyectos que es dueño
                    {
                        "miembros": {
                            "$elemMatch": {
                                "usuario._id.$oid": user["sub"]
                            }
                        }
                    },  # Proyectos donde es miembro
                ]
            }
        except Exception:
            # Si hay error con el ObjectId, mantener la query original
            pass

    # Optional: Projection to exclude password field
    projection = {"miembros.usuario.password": 0}  # Exclude password

    list_verification_request = (
        db_proyectos.find(query, projection=projection).skip(skip).limit(limit)
    )

    quantity = db_proyectos.count_documents(query)
    list_cursor = list(list_verification_request)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    # Remover las barras invertidas
    list_json = json.loads(list_dump.replace("\\", ""))
    list_json = jsonify(request_list=list_json, count=quantity)
    return list_json

@app.route('/proyecto/<string:proyecto_id>/objetivos', methods=['GET'])
@token_required
def obtener_objetivos_especificos(_, proyecto_id):
    
    """
    Obtener los objetivos específicos de un proyecto.

    ---
    tags:
      - Proyectos
    parameters:
      - name: proyecto_id
        in: path
        type: string
        required: true
        description: ID del proyecto

    responses:
      200:
        description: Lista de objetivos específicos
        schema:
          type: object
          properties:
            objetivos_especificos:
              type: array
              items:
                type: string
      404:
        description: Proyecto no encontrado
      401:
        description: Token inválido o no autorizado
    """
    proyecto = db_proyectos.find_one({"_id": ObjectId(proyecto_id)}, {
        "objetivos_especificos": 1
    })

    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    return jsonify({
        "objetivos_especificos": proyecto.get("objetivos_especificos", [])
    })


@app.route("/proyecto/<string:id>/acciones", methods=["GET"])
@allow_cors
def acciones_proyecto(id):
    """
    Endpoint para obtener las acciones de un proyecto.
    ---
    tags:
      - Proyectos
    parameters:
      - in: path
        name: id
        required: true
        description: ID del proyecto.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
      - in: query
        name: page
        required: false
        description: Número de página para la paginación.
        schema:
          type: integer
          example: 1
      - in: query
        name: limit
        required: false
        description: Límite de acciones por página.
        schema:
          type: integer
          example: 10
    responses:
      200:
        description: Lista de acciones obtenida con éxito.
        schema:
          type: object
          properties:
            request_list:
              type: array
              items:
                type: object
                properties:
                  type:
                    type: string
                    example: "Fondeo"
                  amount:
                    type: number
                    example: 1000
                  total_amount:
                    type: number
                    example: 5000
            count:
              type: integer
              example: 5
    """
    id = ObjectId(id)

    params = request.args
    skip = int(params.get("page")) if params.get("page") else 0
    limit = params.get("limit") if params.get("limit") else 10
    acciones = db_acciones.find({"project_id": id}).skip(skip * limit).limit(limit)
    acciones = map(map_to_doc, acciones)
    quantity = db_acciones.count_documents({"project_id": id}) / 10
    quantity = math.ceil(quantity)
    list_cursor = list(acciones)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    # Remover las barras invertidas
    list_json = json.loads(list_dump.replace("\\", ""))
    list_json = jsonify(request_list=list_json, count=quantity)
    return list_json


@app.route("/proyecto/<string:id>", methods=["GET"])
@allow_cors
def proyecto(id):
    """
    Endpoint para obtener las acciones de un proyecto.
    ---
    tags:
      - Proyectos
    parameters:
      - in: path
        name: id
        required: true
        description: ID del proyecto.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
      - in: query
        name: page
        required: false
        description: Número de página para la paginación.
        schema:
          type: integer
          example: 1
      - in: query
        name: limit
        required: false
        description: Límite de acciones por página.
        schema:
          type: integer
          example: 10
    responses:
      200:
        description: Lista de acciones obtenida con éxito.
        schema:
          type: object
          properties:
            request_list:
              type: array
              items:
                type: object
                properties:
                  type:
                    type: string
                    example: "Fondeo"
                  amount:
                    type: number
                    example: 1000
                  total_amount:
                    type: number
                    example: 5000
            count:
              type: integer
              example: 5
    """
    # Convertir el ID a ObjectId
    try:
        id = ObjectId(id.strip())  # <-- limpiamos espacios invisibles
    except Exception:
        return {"message": "ID de proyecto inválido"}, 400

    # Buscar el producto por ID en la base de datos
    proyecto = db_proyectos.find_one({"_id": id})

    # Si el proyecto no existe, devolver un error 404
    if not proyecto:
        return jsonify({"error": "proyecto no encontrado"}), 404

    # Convertir el ObjectId a una cadena de texto
    proyecto["_id"] = str(proyecto["_id"])
    balance = int_to_string(proyecto["balance"])
    balance_inicial = int_to_string(proyecto["balance_inicial"])
    proyecto["balance"] = balance
    proyecto["owner"] = str(proyecto["owner"])
    proyecto["balance_inicial"] = balance_inicial

    # Devolver el proyecto como una respuesta JSON
    if "regla_fija" in proyecto:
        proyecto["regla_fija"]["_id"] = str(proyecto["regla_fija"]["_id"])
    return jsonify(proyecto)


@app.route("/proyecto/<string:id>/documentos", methods=["GET"])
@allow_cors
def mostrar_documentos_proyecto(id):
    """
    Endpoint para obtener los documentos de un proyecto.
    ---
    tags:
      - Proyectos
    parameters:
      - in: path
        name: id
        required: true
        description: ID del proyecto.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
      - in: query
        name: page
        required: false
        description: Número de página para la paginación.
        schema:
          type: integer
          example: 1
      - in: query
        name: limit
        required: false
        description: Límite de documentos por página.
        schema:
          type: integer
          example: 10
    responses:
      200:
        description: Lista de documentos obtenida con éxito.
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
                    example: "64b8f3e2c9d1a2b3c4d5e6f7"
                  descripcion:
                    type: string
                    example: "Presupuesto inicial"
                  monto:
                    type: string
                    example: "1000.00"
            count:
              type: integer
              example: 5
    """
    id = ObjectId(id)

    params = request.args
    skip = int(params.get("page")) if params.get("page") else 0
    limit = params.get("limit") if params.get("limit") else 10
    documentos = db_documentos.find({"project_id": id}).skip(skip * limit).limit(limit)
    # acciones = map(map_to_doc, acciones)
    quantity = db_documentos.count_documents({"project_id": id}) / 10
    quantity = math.ceil(quantity)
    list_cursor = list(documentos)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    # Remover las barras invertidas
    list_json = json.loads(list_dump.replace("\\", ""))
    list_json = jsonify(request_list=list_json, count=quantity)
    return list_json


@app.route("/documento_crear", methods=["POST"])
@allow_cors
@token_required
def crear_presupuesto(user):
    """
    Endpoint para crear un presupuesto en un proyecto.
    ---
    tags:
      - Presupuestos
    parameters:
      - in: formData
        name: proyecto_id
        required: true
        description: ID del proyecto al que pertenece el presupuesto.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
      - in: formData
        name: descripcion
        required: true
        description: Descripción del presupuesto.
        schema:
          type: string
          example: "Presupuesto inicial"
      - in: formData
        name: monto
        required: true
        description: Monto del presupuesto.
        schema:
          type: string
          example: "1000.00"
      - in: formData
        name: files
        required: false
        description: Archivos relacionados con el presupuesto.
        schema:
          type: array
          items:
            type: file
      - in: formData
        name: objetivo_especifico
        required: true
        description: En el caso de que el presupuesto sea para un objetivo específico.
        schema:
          type: string
          example: "Compra de materiales"
    responses:
      201:
        description: Presupuesto creado con éxito.
        schema:
          type: object
          properties:
            mensaje:
              type: string
              example: "Archivos subidos exitosamente"
      400:
        description: Error en los datos enviados.
    """
    # Get project ID and other details from request
    project_id = request.form.get("proyecto_id")
    descripcion = request.form.get("descripcion")
    monto = request.form.get("monto")
    objetivo_especifico = request.form.get("objetivo_especifico")

    # Validate required fields
    print(project_id, descripcion, monto)
    if not project_id or not descripcion or not monto:
        return jsonify({"error": "Missing required fields"}), 400
        

    # Generate unique presupuesto ID
    presupuesto_id = str(ObjectId())

    # Create folders with project ID and presupuesto ID in Cloudinary
    # folder_path = f"budgets/{project_id}/{presupuesto_id}"
    # try:
    #     print('Folder Path')
    #     print(folder_path)
    #     api.create_folder(path=folder_path)
    # except Exception as e:
    #     print(f"Error creating folder: {e}")
    #     return jsonify({'error': 'Error creating folders'}), 400

    # Create budget object
    presupuesto = {
        "project_id": ObjectId(project_id),
        "presupuesto_id": presupuesto_id,
        "descripcion": descripcion,
        # Replace with your string to int conversion function
        "monto": string_to_int(monto),
        "status": "new",
        "objetivo_especifico": objetivo_especifico,
        "archivos": [],
        "created_at": datetime.utcnow(),
    }

    # Get uploaded files from request
    archivos = request.files.getlist("files")

    # Track successfully uploaded files and error messages
    uploaded_files = []
    error_messages = []

    # Upload files to Cloudinary within presupuesto folder
    for archivo in archivos:
        # Generate unique public ID (optional)
        public_id = f"budgets/{project_id}/{presupuesto_id}/{archivo.filename}"
        file_buffer = BytesIO(archivo.read())
        upload_result = upload_file(file_buffer, public_id)

        if upload_result is not None:
            uploaded_files.append(upload_result["download_url"])
            presupuesto["archivos"].append(
                {"nombre": archivo.filename, "public_id": upload_result["fileId"], "download_url": upload_result["download_url"]}
            )
        else:
            error_messages.append(
                f"Error uploading file {archivo.filename}: {upload_result['error']}"
            )

    # Handle upload errors (if any)
    if error_messages:
        # Delete successfully uploaded files if any
        # for uploaded_file in uploaded_files:
        #     api.destroy(public_id=uploaded_file)

        # Return error response with messages
        return jsonify({"error": error_messages}), 400

    # Insert presupuesto object into database
    result = db_documentos.insert_one(presupuesto)

    # Handle database errors
    if not result.acknowledged:
        return jsonify({"error": "Error saving presupuesto"}), 500

    # Log document creation
    message_log = (
        f'{user["nombre"]} agrego el presupuesto {descripcion} con un monto de ${monto}'
    )
    agregar_log(project_id, message_log)

    # Return success response
    return jsonify({"mensaje": "Archivos subidos exitosamente", "_id": str(result.inserted_id)}), 201


@app.route("/documento_cerrar", methods=["POST"])
@allow_cors
@token_required
def cerrar_presupuesto(user):
    """
    Endpoint para cerrar un presupuesto en un proyecto.
    ---
    tags:
      - Presupuestos
    parameters:
      - in: formData
        name: proyecto_id
        required: true
        description: ID del proyecto al que pertenece el presupuesto.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
      - in: formData
        name: doc_id
        required: true
        description: ID del documento del presupuesto.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f8"
      - in: formData
        name: monto
        required: true
        description: Monto aprobado del presupuesto.
        schema:
          type: string
          example: "500.00"
      - in: formData
        name: description
        required: true
        description: Descripción del cierre del presupuesto.
        schema:
          type: string
          example: "Cierre del presupuesto inicial"
      - in: formData
        name: files
        required: false
        description: Archivos relacionados con el cierre del presupuesto.
        schema:
          type: array
          items:
            type: file
    responses:
      201:
        description: Presupuesto cerrado con éxito.
        schema:
          type: object
          properties:
            mensaje:
              type: string
              example: "proyecto ajustado exitosamente"
      400:
        description: Error en los datos enviados.
    """

    id = request.form.get("proyecto_id")
    doc_id = request.form.get("doc_id")
    data_balance = request.form.get("monto")
    data_descripcion = request.form.get("description")
    carpeta_proyecto = os.path.join("files", id)
    referencia=request.form.get("referencia")
    monto_transferencia=request.form.get("monto_transferencia")
    banco=request.form.get("banco")
    cuenta_contable=request.form.get("cuenta_contable")

    # Crear la carpeta del proyecto si no existe
    if not os.path.exists(carpeta_proyecto):
        os.makedirs(carpeta_proyecto)
    # Actualizas balance
    proyecto = db_proyectos.find_one({"_id": ObjectId(id)})
    data_balance = string_to_int(data_balance)
    proyecto_balance = int(proyecto["balance"])
    balance = proyecto_balance - data_balance
    print(f"Balance: {balance}, Proyecto Balance: {proyecto_balance}, Data Balance: {data_balance}")
    if data_balance > proyecto_balance:
      return jsonify({"error": "El monto aprobado excede el saldo disponible del proyecto."}), 400
    
    db_proyectos.update_one({"_id": ObjectId(id)}, {"$set": {"balance": balance}})

    # Agregas la accion a las actividades
    data_acciones = {}
    data_acciones["project_id"] = ObjectId(id)
    data_acciones["user"] = user["nombre"]
    data_acciones["type"] = f"Retiro {data_descripcion}"
    data_acciones["amount"] = data_balance * -1
    data_acciones["total_amount"] = balance
    data_acciones["referencia"] = referencia
    data_acciones["monto_transferencia"] = monto_transferencia
    data_acciones["banco"] = banco
    data_acciones["cuenta_contable"] = cuenta_contable
    data_acciones["created_at"] = datetime.utcnow()
    db_acciones.insert_one(data_acciones)

    # Cierras el presupuesto

    archivos = request.files.getlist("files")
    archivos_guardados = []
    # Guardar los archivos en las subcarpetas del proyecto
    for archivo in archivos:
        # Obtener el nombre del archivo
        nombre_archivo = archivo.filename

        # Crear la carpeta del presupuesto
        presupuesto_id = str(ObjectId())
        carpeta_presupuesto = os.path.join(carpeta_proyecto, presupuesto_id)
        os.makedirs(carpeta_presupuesto)

        # Guardar el archivo en la carpeta del presupuesto
        archivo.save(os.path.join(carpeta_presupuesto, nombre_archivo))

        # Agregar el archivo al arreglo de archivos del presupuesto
        archivos_guardados.append(
            {
                "nombre": nombre_archivo,
                "ruta": os.path.join(carpeta_presupuesto, nombre_archivo),
            }
        )

    db_documentos.update_one(
        {"_id": ObjectId(doc_id)},
        {
            "$set": {
                "status": "finished",
                "monto_aprobado": data_balance,
                "archivos_aprobado": archivos_guardados,
                "description": data_descripcion,
                "referencia": referencia,
                "monto_transferencia": monto_transferencia,
                "banco": "banco",
                "cuenta_contable": cuenta_contable
            }
        },
    )

    message_log = f'{user["nombre"]} cerro el presupuesto {data_descripcion} con un monto de ${int_to_string(data_balance)}'
    agregar_log(id, message_log)

    return jsonify({"mensaje": "proyecto ajustado exitosamente"}), 201


@app.route("/documento_eliminar", methods=["POST"])
@allow_cors
@token_required
def eliminar_presupuesto(user):
    """
    Endpoint para eliminar un presupuesto de un proyecto.
    ---
    tags:
      - Presupuestos
    parameters:
      - in: body
        name: body
        required: true
        description: Datos para eliminar el presupuesto.
        schema:
          type: object
          properties:
            budget_id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f8"
            project_id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
    responses:
      200:
        description: Presupuesto eliminado con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Solicitud de regla eliminada con éxito"
      401:
        description: El presupuesto está finalizado y no se puede eliminar.
      404:
        description: Presupuesto no encontrado.
      400:
        description: No se pudo eliminar el presupuesto.
    """
    data = request.get_json()
    presupuesto_id = data["budget_id"]
    id = data["project_id"]
    documento = db_documentos.find_one({"_id": ObjectId(presupuesto_id)})
    if documento is None:
        return jsonify({"message": "Presupuesto no encontrado"}), 404

    descripcion = documento["descripcion"]
    monto = documento["monto"]
    if documento["status"] == "finished":
        return jsonify(
            {"mensaje": "Presupuesto esta finalizado, no se puede eliminar"}
        ), 401

    result = db_documentos.delete_one({"_id": ObjectId(presupuesto_id)})
    if result.deleted_count == 1:
        message_log = f'{user["nombre"]} elimino el presupuesto {descripcion} con un monto de ${int_to_string(monto)}'
        agregar_log(id, message_log)

        return jsonify({"message": "Solicitud de regla eliminada con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar la regla"}), 400


@app.route("/eliminar_proyecto", methods=["POST"])
@allow_cors
@token_required
def eliminar_proyecto(user):
    """
    Endpoint para eliminar un presupuesto de un proyecto.
    ---
    tags:
      - Presupuestos
    parameters:
      - in: body
        name: body
        required: true
        description: Datos para eliminar el presupuesto.
        schema:
          type: object
          properties:
            budget_id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f8"
            project_id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
    responses:
      200:
        description: Presupuesto eliminado con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Solicitud de regla eliminada con éxito"
      401:
        description: El presupuesto está finalizado y no se puede eliminar.
      404:
        description: Presupuesto no encontrado.
      400:
        description: No se pudo eliminar el presupuesto.
    """
    data = request.get_json()
    id = data["proyecto_id"]
    documento = db_proyectos.find_one({"_id": ObjectId(id)})
    if documento is None:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    result = db_proyectos.delete_one({"_id": ObjectId(id)})
    if result.deleted_count == 1:
        return jsonify({"message": "Proyecto eliminado con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar la regla"}), 400


@app.route("/finalizar_proyecto", methods=["POST"])
@allow_cors
@token_required
def finalizar_proyecto(user):
    """
    Endpoint para finalizar un proyecto.
    ---
    tags:
      - Proyectos
    parameters:
      - in: body
        name: body
        required: true
        description: Datos para finalizar el proyecto.
        schema:
          type: object
          properties:
            proyecto_id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
    responses:
      200:
        description: Proyecto finalizado con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Proyecto finalizado con éxito"
      404:
        description: Proyecto no encontrado.
      400:
        description: Error en los datos enviados.
    """
    data = request.get_json()
    proyecto_id = data.get("proyecto_id")

    proyecto = db_proyectos.find_one({"_id": ObjectId(proyecto_id)})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    # 1. Obtener movimientos monetarios
    movimientos = list(db_acciones.find({"proyecto_id": ObjectId(proyecto_id)}))
    movimientos_simple = [
        {"type": m.get("type"), "amount": m.get("amount", 0), "user": m.get("user", "N/A")}
        for m in movimientos
    ]

    # 2. Obtener logs del sistema
    logs = list(db_logs.find({"proyecto_id": ObjectId(proyecto_id)}))
    logs_simple = [
        {"fecha": str(ls.get("fecha_creacion")), "mensaje": ls.get("mensaje")}
        for ls in logs
    ]

    # 3. Obtener presupuestos relacionados
    presupuestos = list(db_documentos.find({"proyecto_id": ObjectId(proyecto_id)}))
    presupuestos_simple = [
        {"descripcion": b.get("descripcion", ""), "monto_aprobado": b.get("monto_aprobado", 0)}
        for b in presupuestos
    ]

    # 4. Actualizar proyecto como finalizado
    db_proyectos.update_one(
        {"_id": ObjectId(proyecto_id)},
        {
            "$set": {
                "status.finished": True,
                "fecha_fin": datetime.utcnow()
            }
        }
    )

    # 5. Volver a cargar proyecto actualizado (por si agregaste fecha_fin)
    proyecto = db_proyectos.find_one({"_id": ObjectId(proyecto_id)})

    # 6. Generar el PDF del acta
    try:
        pdf_bytes = generar_acta_finalizacion_pdf(
            proyecto,
            movements=movimientos_simple,
            logs=logs_simple,
            budgets=presupuestos_simple
        )

        # 7. Subir PDF a Backblaze
        file_name = f"actas/acta_finalizacion_{str(proyecto['_id'])}.pdf"
        upload_result = upload_file(BytesIO(pdf_bytes), file_name)

        # 8. Guardar URL del acta en el proyecto
        db_proyectos.update_one(
            {"_id": ObjectId(proyecto["_id"])},
            {
                "$set": {
                    "acta_finalizacion": {
                        "fecha": datetime.utcnow(),
                        "documento_url": upload_result["download_url"],
                        "file_id": upload_result["fileId"]
                    }
                }
            }
        )
    except Exception as e:
        print(f"❌ Error generando o subiendo acta finalización: {e}")

    return jsonify({"message": "Proyecto finalizado exitosamente."}), 200


@app.route("/proyecto/<string:id>/logs", methods=["GET"])
@allow_cors
def obtener_logs(id):
    """
    Endpoint para obtener los logs de un proyecto.
    ---
    tags:
      - Proyectos
    parameters:
      - in: path
        name: id
        required: true
        description: ID del proyecto.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
      - in: query
        name: page
        required: false
        description: Número de página para la paginación.
        schema:
          type: integer
          example: 1
      - in: query
        name: limit
        required: false
        description: Límite de logs por página.
        schema:
          type: integer
          example: 10
    responses:
      200:
        description: Lista de logs obtenida con éxito.
        schema:
          type: object
          properties:
            request_list:
              type: array
              items:
                type: object
                properties:
                  id_proyecto:
                    type: string
                    example: "64b8f3e2c9d1a2b3c4d5e6f7"
                  mensaje:
                    type: string
                    example: "Usuario X realizó una acción"
                  fecha_creacion:
                    type: string
                    example: "2025-04-22T12:00:00Z"
            count:
              type: integer
              example: 5
    """
    id = ObjectId(id)

    params = request.args
    skip = int(params.get("page")) if params.get("page") else 0
    limit = params.get("limit") if params.get("limit") else 10
    acciones = db_logs.find({"id_proyecto": id}).skip(skip * limit).limit(limit)
    quantity = db_logs.count_documents({"id_proyecto": id}) / 10
    quantity = math.ceil(quantity)

    list_cursor = list(acciones)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    # Remover las barras invertidas
    list_json = json.loads(list_dump.replace("\\", ""))
    list_json = jsonify(request_list=list_json, count=quantity)
    return list_json, 200
    # return jsonify(list_json), 200


@app.route("/mostrar_solicitudes", methods=["GET"])
@allow_cors
@token_required
def mostrar_solicitudes(user):
    """
    Endpoint para obtener la lista de solicitudes.
    ---
    tags:
      - Solicitudes
    parameters:
      - in: query
        name: page
        required: false
        description: Número de página para la paginación.
        schema:
          type: integer
          example: 1
      - in: query
        name: limit
        required: false
        description: Límite de solicitudes por página.
        schema:
          type: integer
          example: 10
    responses:
      200:
        description: Lista de solicitudes obtenida con éxito.
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
                    example: "64b8f3e2c9d1a2b3c4d5e6f7"
                  nombre:
                    type: string
                    example: "Solicitud 1"
                  status:
                    type: string
                    example: "new"
            count:
              type: integer
              example: 50
    """
    # Obtener el número de página actual
    params = request.args
    skip = int(params.get("page")) if params.get("page") else 0
    limit = params.get("limit") if params.get("limit") else 10
    # query = {"_id": ObjectId(request_id)}
    list_verification_request = db_solicitudes.find({}).skip(skip * limit).limit(limit)
    quantity = db_solicitudes.count_documents({})
    list_cursor = list(list_verification_request)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    # Remover las barras invertidas
    list_json = json.loads(list_dump.replace("\\", ""))
    list_json = jsonify(request_list=list_json, count=quantity)
    return list_json


@app.route("/mostrar_reglas_fijas", methods=["GET"])
@allow_cors
@token_required
def mostrar_reglas_fijas(user):
    """
    Endpoint para obtener la lista de reglas fijas completadas.
    ---
    tags:
      - Reglas fijas
    responses:
      200:
        description: Lista de reglas fijas obtenida con éxito.
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
                    example: "64b8f3e2c9d1a2b3c4d5e6f7"
                  nombre:
                    type: string
                    example: "Regla Fija 1"
                  status:
                    type: string
                    example: "completed"
    """
    list_request = db_solicitudes.find({"status": "completed"})
    list_cursor = list(list_request)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    list_json = jsonify(
        request_list=list_json,
    )

    return list_json, 200


@app.route("/proyecto/<string:id>/movimientos/descargar", methods=["GET"])
@allow_cors
def descargar_movimientos(id):
    """
    Endpoint para descargar los movimientos de un proyecto.
    ---
    tags:
      - Proyectos
    parameters:
      - in: path
        name: id
        required: true
        description: ID del proyecto.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
      - in: query
        name: formato
        required: false
        description: Formato de descarga (csv o json).
        schema:
          type: string
          example: "csv"
    responses:
      200:
        description: Archivo descargado con éxito.
        content:
          application/json:
            schema:
              type: string
              example: "Archivo descargado con éxito"
      400:
        description: Formato no válido.
    """
    id_proyecto = ObjectId(id)

    # 1. Recuperar los movimientos del proyecto desde la base de datos
    movimientos = db_acciones.find({"project_id": id_proyecto})
    movimientos_lista = list(movimientos)

    # 2. Determinar el formato de descarga (CSV o JSON)
    formato = request.args.get("formato", "csv").lower()  # Por defecto a CSV

    if formato == "csv":
        return generar_csv(movimientos_lista)
    elif formato == "json":
        return generar_json(movimientos_lista)
    else:
        return jsonify({"error": "Formato no válido. Use 'csv' o 'json'."}), 400

@app.route("/proyecto/<string:id>/fin", methods=["GET"])
@allow_cors
def mostrar_finalizacion(id):
    """
    Endpoint para obtener los datos finales de un proyecto.
    ---
    tags:
      - Proyectos
    parameters:
      - in: path
        name: id
        required: true
        description: ID del proyecto.
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
    responses:
      200:
        description: Datos finales del proyecto obtenidos con éxito.
        schema:
          type: object
          properties:
            logs:
              type: array
              items:
                type: object
                properties:
                  mensaje:
                    type: string
                    example: "Usuario X realizó una acción"
                  fecha_creacion:
                    type: string
                    example: "2025-04-22T12:00:00Z"
            documentos:
              type: array
              items:
                type: object
                properties:
                  descripcion:
                    type: string
                    example: "Presupuesto inicial"
                  monto:
                    type: string
                    example: "1000.00"
            movimientos:
              type: array
              items:
                type: object
                properties:
                  type:
                    type: string
                    example: "Fondeo"
                  amount:
                    type: number
                    example: 1000
    """
    id = ObjectId(id)
    movs = db_acciones.find({"project_id": id})
    docs = db_documentos.find({"project_id": id})
    logs = db_logs.find({"id_proyecto": id})

    movs_cursor = list(movs)
    movs_dump = json_util.dumps(movs_cursor)
    movs_json = json.loads(movs_dump.replace("\\", ""))

    docs_cursor = list(docs)
    docs_dump = json_util.dumps(docs_cursor)
    docs_json = json.loads(docs_dump.replace("\\", ""))

    logs_cursor = list(logs)
    logs_dump = json_util.dumps(logs_cursor)
    list_json = json.loads(logs_dump.replace("\\", ""))
    data_response = jsonify(logs=list_json, documentos=docs_json, movimientos=movs_json)
    return data_response, 200


@app.route("/asignar_regla_fija/", methods=["POST"])
@allow_cors
@token_required
def asignar_regla_fija(user):
    """
    Endpoint para asignar una regla fija a un proyecto.
    ---
    tags:
      - Reglas fijas
    parameters:
      - in: body
        name: body
        required: true
        description: Datos para asignar la regla fija.
        schema:
          type: object
          properties:
            proyecto_id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f7"
            regla_id:
              type: string
              example: "64b8f3e2c9d1a2b3c4d5e6f8"
    responses:
      200:
        description: Regla fija asignada con éxito.
        schema:
          type: object
          properties:
            message:
              type: string
              example: "La regla se asignó correctamente"
      404:
        description: Proyecto o regla fija no encontrada.
      400:
        description: Error en los datos enviados.
    """
    data = request.get_json()
    proyecto_id = data["proyecto_id"]
    regla_id = data["regla_id"]

    # Encontrar Regla
    proyecto = db_proyectos.find_one({"_id": ObjectId(proyecto_id)})
    if proyecto is None:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    # Encontrara Proyecto
    regla = db_solicitudes.find_one({"_id": ObjectId(regla_id)})
    if regla is None:
        return jsonify({"message": "Regla fija no encontrada"}), 404

    # Verificar proyecto tiene balance inicial
    if proyecto["balance_inicial"] == 0:
        return jsonify(
            {"message": "Antes de asignar regla tienes que asignar balance"}
        ), 400

    balance = int(proyecto["balance"])  # comienza con el balance inicial del proyecto

    for x in regla["reglas"]:
        balance -= x["monto"]  # actualizar balance acumulativo

        db_proyectos.update_one(
            {"_id": ObjectId(proyecto_id)}, {"$set": {"balance": balance}}
        )

        data_acciones = {
            "project_id": ObjectId(proyecto_id),
            "user": user["nombre"],
            "type": x["nombre_regla"],
            "amount": x["monto"] * -1,
            "total_amount": balance,
            "created_at": datetime.utcnow()
        }
        db_acciones.insert_one(data_acciones)

        message_log = f'{user["nombre"]} asigno la regla: {regla["nombre"]} con el item {x["nombre_regla"]} con un monto de ${int_to_string(x["monto"])}'
        agregar_log(proyecto_id, message_log)

    # Asignar la regla
    new_status, _ = actualizar_pasos(proyecto["status"], 5)

    db_proyectos.update_one(
        {"_id": ObjectId(proyecto_id)},
        {"$set": {"regla_fija": regla, "status": new_status}},
    )

    # Dar respuesta que todo esta ok
    return jsonify({"message": "La regla se asigno correctamente"}), 200

@app.route('/reporte/proyecto/<string:proyecto_id>', methods=['GET'])
@token_required
def generar_reporte_proyecto(data, proyecto_id):
    """
    Generar un reporte resumen del proyecto.

    ---
    tags:
      - Reportes
    parameters:
      - name: proyecto_id
        in: path
        type: string
        required: true
        description: ID del proyecto para generar el reporte.
    responses:
      200:
        description: Reporte generado exitosamente
        schema:
          type: object
          properties:
            saldo_inicial:
              type: number
              description: Monto inicial del proyecto.
            saldo_restante:
              type: number
              description: Saldo disponible restante.
            presupuestos_totales:
              type: integer
              description: Número total de presupuestos creados.
            monto_total_presupuestado:
              type: number
              description: Suma de los montos de todos los presupuestos.
            monto_total_aprobado:
              type: number
              description: Suma de los montos aprobados.
            top_presupuestos:
              type: array
              items:
                type: object
                properties:
                  descripcion:
                    type: string
                    description: Descripción del presupuesto.
                  monto_aprobado:
                    type: number
                    description: Monto aprobado en el presupuesto.
                  objetivo_especifico:
                    type: string
                    description: Objetivo específico relacionado.
      404:
        description: Proyecto no encontrado
      401:
        description: Token inválido o no autorizado
    """
    proyecto = db_proyectos.find_one({"_id": ObjectId(proyecto_id)})
    if not proyecto:
        return jsonify({"error": "Proyecto no encontrado"}), 404

    presupuestos = list(db_documentos.find({"proyecto_id": ObjectId(proyecto_id)}))

    saldo_inicial = proyecto.get("balance_inicial", 0)
    saldo_restante = proyecto.get("balance", 0)

    monto_total_presupuestado = sum(p.get("monto", 0) for p in presupuestos)
    monto_total_aprobado = sum(p.get("monto_aprobado", 0) for p in presupuestos if p.get("status") == "finished")
    presupuestos_totales = len(presupuestos)

    top_presupuestos = sorted(
        [p for p in presupuestos if p.get("status") == "finished"],
        key=lambda x: x.get("monto_aprobado", 0),
        reverse=True
    )[:5]

    top_presupuestos_simple = [
        {
            "descripcion": p.get("descripcion"),
            "monto_aprobado": p.get("monto_aprobado"),
            "objetivo_especifico": p.get("objetivo_especifico")
        }
        for p in top_presupuestos
    ]

    reporte = {
        "saldo_inicial": saldo_inicial,
        "saldo_restante": saldo_restante,
        "presupuestos_totales": presupuestos_totales,
        "monto_total_presupuestado": monto_total_presupuestado,
        "monto_total_aprobado": monto_total_aprobado,
        "top_presupuestos": top_presupuestos_simple
    }

    return jsonify(reporte), 200

@app.route('/proyecto/<id>/reporte', methods=['GET'])
def obtener_reporte_proyecto(id):
    """
    Devuelve un resumen del proyecto con evolución del saldo, egresos por tipo y totales.
    ---
    tags:
      - Reportes
    parameters:
      - name: id
        in: path
        required: true
        type: string
        description: ID del proyecto
    responses:
      200:
        description: Reporte generado con éxito.
        schema:
          type: object
          properties:
            balance_history:
              type: array
              items:
                type: object
                properties:
                  fecha:
                    type: string
                    example: "2025-06-03"
                  saldo:
                    type: number
                    example: 5200
            egresos_tipo:
              type: array
              items:
                type: object
                properties:
                  tipo:
                    type: string
                    example: "Transporte"
                  monto:
                    type: number
                    example: 1000
            resumen:
              type: object
              properties:
                ingresos:
                  type: number
                  example: 45000
                egresos:
                  type: number
                  example: 38000
                represupuestos:
                  type: number
                  example: 3
                miembros:
                  type: number
                  example: 4
      404:
        description: Proyecto no encontrado.
    """
    try:
        project_id = ObjectId(id)
    except Exception:
        return jsonify({"message": "ID de proyecto inválido"}), 400

    acciones = list(db_acciones.find({"project_id": project_id}).sort("created_at", 1))
    proyecto = db_proyectos.find_one({"_id": project_id})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    # --------------------------
    # Evolución del saldo
    # --------------------------
    balance_history = [
        {
            "fecha": acc["created_at"].strftime("%Y-%m-%d"),
            "saldo": acc["total_amount"] / 100
        }
        for acc in acciones if "created_at" in acc
    ]

    # --------------------------
    # Egresos agrupados por tipo
    # --------------------------
    egresos_por_tipo = defaultdict(float)
    for acc in acciones:
        if acc.get("amount", 0) < 0:
            egresos_por_tipo[acc.get("type", "Sin tipo")] += abs(acc["amount"]) / 100

    egresos_tipo = [{"tipo": tipo, "monto": monto} for tipo, monto in egresos_por_tipo.items()] 

    # --------------------------
    # Resumen general
    # --------------------------
    resumen = {
        "ingresos": sum(acc.get("amount", 0) for acc in acciones if acc.get("amount", 0) > 0) / 100,
        "egresos": -sum(acc.get("amount", 0) for acc in acciones if acc.get("amount", 0) < 0) / 100,
        "presupuestos": db_documentos.count_documents({
            "project_id": ObjectId(project_id),
            "status": "finished"
        }),
        "represupuestos": db_documentos.count_documents({
            "project_id": ObjectId(project_id),
            "status": "new"
        }),
        "miembros": len(proyecto.get("miembros", [])),
    }

    return jsonify({
        "balanceHistory": balance_history,
        "egresosPorTipo": egresos_tipo,
        "resumen": resumen
    })

@app.route('/dashboard_global', methods=['GET'])
@allow_cors
@token_required
def resumen_general(user):
    """
    Endpoint para obtener un resumen general del sistema.
    ---
    tags:
      - Dashboard
    parameters:
      - name: range
        in: query
        type: string
        enum: [1m, 6m, 1y, all]
        default: 6m
        description: Rango de tiempo a mostrar.
      - in: header
        name: X-Department-Context
        required: false
        description: ID del departamento para el contexto (solo para super_admin)
        schema:
          type: string
          example: "64b8f3e2c9d1a2b3c4d5e6f7"
    responses:
      200:
        description: Resumen general del sistema
        schema:
          type: object
          properties:
            balanceHistory:
              type: array
              items:
                type: object
                properties:
                  fecha:
                    type: string
                    example: "2025-06"
                  saldo:
                    type: number
                    example: 12345
            categorias:
              type: array
              items:
                type: object
                properties:
                  categoria:
                    type: string
                    example: "donacion"
                  count:
                    type: integer
                    example: 2
            totales:
              type: object
              properties:
                ingresos:
                  type: number
                  example: 12000
                egresos:
                  type: number
                  example: 5000
            resumen:
              type: object
              properties:
                proyectos:
                  type: integer
                  example: 8
                miembros:
                  type: integer
                  example: 25
                presupuestos:
                  type: integer
                  example: 10
                presupuestos_finalizados:
                  type: integer
                  example: 4
                balance_total:
                  type: number
                  example: 40000
    """
    range_str = request.args.get("range", "6m")
    now = datetime.utcnow()

    if range_str == "1m":
      date_limit = now - relativedelta(months=1)
    elif range_str == "6m":
      date_limit = now - relativedelta(months=6)
    elif range_str == "1y":
      date_limit = now - relativedelta(years=1)
    else:
      date_limit = None  # mostrar todo

    # Determinar filtro de proyectos según el contexto del usuario
    filtro_proyectos = {}
    filtro_proyectos_ids = None  # Para filtrar acciones y documentos
    
    # Super admin puede ver todos los proyectos, a menos que tenga contexto de departamento
    if user.get("role") == "super_admin":
        # Si super_admin tiene contexto de departamento, filtrar como admin_departamento
        if user.get("_using_dept_context") and "departamento_id" in user:
            try:
                dept_id = ObjectId(user["departamento_id"])
                filtro_proyectos = {"departamento_id": dept_id}
                # Obtener IDs de proyectos del departamento para filtrar acciones y documentos
                proyectos_dept = list(db_proyectos.find(filtro_proyectos, {"_id": 1}))
                filtro_proyectos_ids = [p["_id"] for p in proyectos_dept]
            except Exception:
                # Si hay error con el ObjectId, mantener sin filtro (ver todos)
                pass
    # Admin de departamento puede ver proyectos de su departamento
    elif user.get("role") == "admin_departamento" and "departamento_id" in user:
        try:
            dept_id = ObjectId(user["departamento_id"])
            filtro_proyectos = {"departamento_id": dept_id}
            # Obtener IDs de proyectos del departamento para filtrar acciones y documentos
            proyectos_dept = list(db_proyectos.find(filtro_proyectos, {"_id": 1}))
            filtro_proyectos_ids = [p["_id"] for p in proyectos_dept]
        except Exception:
            # Si hay error con el ObjectId, mantener sin filtro
            pass

    # Totales de ingresos y egresos
    filtro_fecha = {}
    if date_limit:
        filtro_fecha = { "created_at": { "$gte": date_limit } }
    
    # Filtrar acciones por proyectos del departamento si aplica
    filtro_acciones = {**filtro_fecha}
    if filtro_proyectos_ids:
        filtro_acciones["project_id"] = {"$in": filtro_proyectos_ids}
    
    ingresos = 0
    egresos = 0
    for a in db_acciones.find(filtro_acciones):
        monto = a.get("amount", 0)
        if monto >= 0:
            ingresos += monto
        else:
            egresos += abs(monto)

    # Categorías de proyectos (filtrar por departamento si aplica)
    pipeline_categorias = []
    if filtro_proyectos:
        pipeline_categorias.append({"$match": filtro_proyectos})
    pipeline_categorias.extend([
        {"$group": {"_id": "$categoria", "count": {"$sum": 1}}},
        {"$project": {"categoria": "$_id", "count": 1, "_id": 0}}
    ])
    categorias = list(db_proyectos.aggregate(pipeline_categorias))
    
    # Encontrar ocurrencias de usuarios en proyectos
    conteo_usuarios = defaultdict(lambda: {"name": "", "projects": 0})

    # Consulta proyectos (ya filtrados por departamento si aplica)
    proyectos = list(db_proyectos.find(filtro_proyectos))

    for proyecto in proyectos:
        miembros = proyecto.get("miembros", [])
        for miembro in miembros:
            user_info = miembro.get("usuario", {})
            user_id = str(user_info.get("_id", {}).get("$oid")) if isinstance(user_info.get("_id"), dict) else str(user_info.get("_id"))
            nombre = user_info.get("nombre", "Desconocido")

            if user_id:
                conteo_usuarios[user_id]["name"] = nombre
                conteo_usuarios[user_id]["projects"] += 1

    # Transformar a arreglo final
    ocurrencias = [{"id": user_id, "name": info["name"], "projects": info["projects"]}
                  for user_id, info in conteo_usuarios.items()]
    
    # Resumen global (filtrar por departamento si aplica)
    filtro_documentos = {**filtro_fecha}
    if filtro_proyectos_ids:
        filtro_documentos["proyecto_id"] = {"$in": filtro_proyectos_ids}
    
    filtro_documentos_finalizados = {"status": "finished", **filtro_fecha}
    if filtro_proyectos_ids:
        filtro_documentos_finalizados["proyecto_id"] = {"$in": filtro_proyectos_ids}
    
    # Filtrar miembros: solo usuarios del departamento si hay contexto
    filtro_usuarios = {}
    if filtro_proyectos and "departamento_id" in filtro_proyectos:
        filtro_usuarios = {"departamento_id": filtro_proyectos["departamento_id"]}
    
    resumen = {
        "proyectos": db_proyectos.count_documents(filtro_proyectos),
        "miembros": db_usuarios.count_documents(filtro_usuarios),
        "presupuestos": db_documentos.count_documents(filtro_documentos),
        "presupuestos_finalizados": db_documentos.count_documents(filtro_documentos_finalizados),
        "ocurrencias": ocurrencias,
        "balance_total": sum(p.get("balance", 0) for p in proyectos)
    }

    # Historial de saldo mensual (filtrar por proyectos del departamento si aplica)
    balance_acumulado = 0
    balanceHistory = []

    # Últimos 6 meses
    hoy = datetime.today().replace(day=1)
    meses = [(hoy - relativedelta(months=i)).strftime("%Y-%m") for i in reversed(range(6))]

    for mes in meses:
        inicio_mes = datetime.strptime(mes, "%Y-%m")
        fin_mes = (inicio_mes + relativedelta(months=1))

        filtro_movimientos = {
            "created_at": {"$gte": inicio_mes, "$lt": fin_mes}
        }
        if filtro_proyectos_ids:
            filtro_movimientos["project_id"] = {"$in": filtro_proyectos_ids}

        movimientos = db_acciones.find(filtro_movimientos)

        for m in movimientos:
            balance_acumulado += m.get("amount", 0)

        balanceHistory.append({
            "fecha": mes,
            "saldo": round(balance_acumulado / 100, 2)
        })

    return jsonify({
        "balanceHistory": balanceHistory,
        "categorias": categorias,
        "totales": {
            "ingresos": round(ingresos / 100, 2),
            "egresos": round(egresos / 100, 2)
        },
        "resumen": resumen
    })

@app.route("/", methods=["GET"])
@allow_cors
def index():
    """
    Endpoint para saber si el servidor está aceptando requests.
    ---
    responses:
      200:
        description: retorna un string llamado pong porque le estás haciendo ping
        schema:
            type: string
            example: "pong"
    """
    return "pong"


# Manejo de errores


@app.errorhandler(400)
def error_400(e):
    return jsonify({"message": "Solicitud incorrecta"}), 400


@app.errorhandler(401)
def error_401(e):
    return jsonify({"message": "No autorizado"}), 401


@app.errorhandler(404)
def error_404(e):
    print(e)
    return jsonify({"message": "No encontrado"}), 404


@app.errorhandler(500)
def error_500(e):
    print(e)
    return jsonify({"message": "Error interno del servidor"}), 500


if __name__ == "__main__":
    app.run()


#pb# -----------------------
#load_dotenv() # Carga las variables de entorno

#app = FastAPI()
# 🚨 La variable 'mail' DEBE definirse aquí, antes de las funciones
mail = Mail(app)

class EmailSchema(BaseModel):
    recipient: str
    subject: str
    body: str

async def send_email_async(subject: str, email_to: str, body: str):
    message = MessageSchema(
        subject=subject,
        recipients=[email_to],
        body=body,
        subtype="plain"  # o "html" para contenido HTML
    )
    fm = FastMail(app)
    await fm.send_message(message)

#@app.post("/api/send-notification")
@app.route("/send-notification", methods=["POST"])
def send_notification_endpoint():
    """
    Endpoint para enviar una notificación por correo electrónico con plantilla.
    ---
    tags:
      - Email
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            recipient:
              type: string
              example: "usuario@example.com"
            template:
              type: string
              example: "notificacion.html"
            variables:
              type: object
              example: {
                "nombre": "Juan",
                "titulo": "Notificación importante",
                "mensaje": "Este es un mensaje de prueba"
              }
            subject:
              type: string
              example: "Notificación del sistema"
    responses:
      200:
        description: Email enviándose en segundo plano
      400:
        description: Faltan datos requeridos
    """
    data = request.get_json()
    recipient = data.get("recipient")
    subject = data.get("subject")
    template_name = data.get("template")
    variables = data.get("variables", {})
    
    if not recipient or not subject or not template_name:
        return jsonify({"message": "Faltan datos: recipient, subject, template"}), 400

    try:
        send_email_notification(subject, recipient, template_name, variables)
        return jsonify({"message": "Email enviándose en segundo plano"}), 200
    except Exception as e:
        return jsonify({"message": f"Error: {str(e)}"}), 500# Coloca estas funciones DESPUÉS de 'mail = Mail(app)'
# Coloca estas funciones DESPUÉS de 'mail = Mail(app)'

# index.py (Colocadas después de mail = Mail(app))

# NOTA: La variable global 'mail' se define ANTES de estas funciones
# mail = Mail(app) 

def send_async_email(app, mail_obj, subject, recipient, body, is_html=True):
    """Esta función se ejecuta en un hilo separado."""
    with app.app_context(): 
        msg = Message(subject, recipients=[recipient])
        
        if is_html:
            msg.html = body
        else:
            msg.body = body
            
        mail_obj.send(msg) 

def send_email_notification(subject, recipient, template_name, template_vars):
    """
    Llamada desde el endpoint de Flask.
    - template_name: nombre del archivo (ej: 'notificacion.html')
    - template_vars: dict con variables para la plantilla (ej: {'nombre': 'Juan', 'mensaje': '...'})
    """
    
    # Renderizar la plantilla
    html_body = render_template(f'emails/{template_name}', **template_vars)
    
    thr = threading.Thread(
        target=send_async_email, 
        args=[app, mail, subject, recipient, html_body, True]
    )
    thr.start()
    return thr

