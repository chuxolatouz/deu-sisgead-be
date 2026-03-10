from flask import Blueprint, request, jsonify, Response, current_app
from bson import ObjectId, json_util
import json
import math
from datetime import datetime, timezone
from io import BytesIO

from api.extensions import mongo
from api.util.decorators import token_required, allow_cors, validar_datos
from api.util.common import agregar_log
from api.util.utils import string_to_int, int_to_string, int_to_float, actualizar_pasos, generar_csv, generar_json, map_to_doc
from api.util.generar_acta_finalizacion import generar_acta_finalizacion_pdf
from api.util.backblaze import upload_file
from api.services.project_funding_service import ProjectFundingService
from api.util.access import (
    can_access_project,
    is_super_admin,
    parse_object_id,
    user_department_id,
    user_role,
    ROLE_SUPER_ADMIN,
)

projects_bp = Blueprint('projects', __name__)
PLACEHOLDER = "(POR DEFINIR)"


def _sanitize_filename(value):
    safe = "".join(ch for ch in (value or "proyecto") if ch.isalnum() or ch in ("-", "_"))
    return safe or "proyecto"


def _pick_value(data, *keys):
    for key in keys:
        if key in data and data.get(key) not in (None, ""):
            return data.get(key)
    return None


def _forbidden(message="No autorizado"):
    return jsonify({"message": message}), 403


def _ensure_project_access(user, project):
    if not can_access_project(user, project):
        return _forbidden("No autorizado para acceder a este proyecto")
    return None


def _extract_payload_user_id(payload_user):
    if not isinstance(payload_user, dict):
        return None

    value = payload_user.get("_id")
    if isinstance(value, dict):
        value = value.get("$oid")
    if value in (None, ""):
        return None
    return str(value)


def _extract_member_user_id(member):
    if not isinstance(member, dict):
        return None
    payload_user = member.get("usuario") or {}
    if not isinstance(payload_user, dict):
        return None
    value = payload_user.get("_id")
    if isinstance(value, dict):
        value = value.get("$oid")
    if value in (None, ""):
        return None
    return str(value)


def _normalize_project_payload(data):
    alias_map = {
        "fechaInicio": "fecha_inicio",
        "fechaFin": "fecha_fin",
        "objetivoGeneral": "objetivo_general",
        "objetivosEspecificos": "objetivos_especificos",
        "departmentId": "departamento_id",
    }
    for source_key, target_key in alias_map.items():
        if source_key in data and target_key not in data:
            data[target_key] = data[source_key]
    for source_key in alias_map.keys():
        if source_key in data:
            data.pop(source_key, None)
    return data


def _get_project_or_404(project_id):
    object_id = parse_object_id(project_id)
    if not object_id:
        return None, (jsonify({"message": "ID de proyecto inválido"}), 400)

    project = mongo.db.proyectos.find_one({"_id": object_id})
    if not project:
        return None, (jsonify({"message": "Proyecto no encontrado"}), 404)
    return project, None


def _allow_legacy_project_balance() -> bool:
    value = current_app.config.get("ALLOW_LEGACY_PROJECT_BALANCE")
    if value is None:
        value = current_app.config.get("PROJECT_FUNDING_ALLOW_LEGACY_BALANCE")
    if value is None:
        value = "true"
    return str(value).strip().lower() in {"1", "true", "yes", "si"}


def _normalize_category_reference(value):
    if value in (None, ""):
        return None
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        if "$oid" in value:
            return str(value.get("$oid"))
        if "value" in value and value.get("value"):
            return str(value.get("value"))
    return str(value).strip()


def _category_is_active(category):
    return category.get("activo") is not False


def _category_is_deleted(category):
    return category.get("eliminado") is True


def _category_matches_reference(category, reference):
    ref = _normalize_category_reference(reference)
    if not ref:
        return False
    category_id = str(category.get("_id")) if category.get("_id") else None
    category_value = str(category.get("value")) if category.get("value") else None
    return ref in {category_id, category_value}


def _resolve_category_reference(category_reference, allow_inactive=False, allow_deleted=False, current_reference=None):
    ref = _normalize_category_reference(category_reference)
    if not ref:
        return None, None

    category = None
    try:
        category_obj_id = ObjectId(ref)
        category = mongo.db.categorias.find_one({"_id": category_obj_id})
    except Exception:
        category = None

    if not category:
        category = mongo.db.categorias.find_one({"value": ref})

    if not category:
        return None, "Categoría no encontrada"

    if _category_is_deleted(category) and not allow_deleted:
        if not _category_matches_reference(category, current_reference):
            return None, "La categoría seleccionada está eliminada y no se puede asignar"

    if not _category_is_active(category) and not allow_inactive:
        if not _category_matches_reference(category, current_reference):
            return None, "La categoría seleccionada está deshabilitada y no se puede asignar"

    return category, None




@projects_bp.route("/crear_proyecto", methods=["POST"])
@allow_cors
@token_required
@validar_datos(
    {"nombre": str, "descripcion": str, "fecha_inicio": str, "fecha_fin": str}
)
def crear_proyecto(user):
    """
    Crear nuevo proyecto
    ---
    tags:
      - Proyectos
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
            - fecha_inicio
            - fecha_fin
          properties:
            nombre:
              type: string
              example: "Proyecto Construcción"
            descripcion:
              type: string
            fecha_inicio:
              type: string
              format: date
            fecha_fin:
              type: string
              format: date
            categoria:
              type: string
            departamento_id:
              type: string
    responses:
      201:
        description: Proyecto creado
        schema:
          type: object
          properties:
            message:
              type: string
            _id:
              type: string
      400:
        description: Categoría o departamento no encontrado
    """
    current_user = user["sub"]
    data = _normalize_project_payload(request.get_json(silent=True) or {})
    
    
    departamento_id = None
    payload_department_id = _pick_value(data, "departmentId", "department_id", "departamento_id")
    if not is_super_admin(user):
        actor_department_id = user_department_id(user)
        actor_department_object_id = parse_object_id(actor_department_id)
        if not actor_department_object_id:
            return _forbidden("El usuario no tiene un departamento válido asociado")
        if payload_department_id and str(payload_department_id).strip() != str(actor_department_object_id):
            return _forbidden("Solo puedes crear proyectos en tu departamento")
        departamento_id = actor_department_object_id
    else:
        if payload_department_id:
            dept_id_obj = parse_object_id(payload_department_id)
            if not dept_id_obj:
                return jsonify({"message": "ID de departamento inválido"}), 400
            departamento = mongo.db.departamentos.find_one({"_id": dept_id_obj})
            if not departamento:
                return jsonify({"message": "Departamento no encontrado"}), 400
            departamento_id = dept_id_obj
        elif user.get("_using_dept_context") and ("departamento_id" in user or "departmentId" in user):
            departamento_id = parse_object_id(user.get("departmentId") or user.get("departamento_id"))
    
    data["miembros"] = []
    data["balance"] = 0
    data["balance_inicial"] = 0
    if "status" not in data or not isinstance(data["status"], dict):
      data["status"] = {
          "actual": 1,
          "completado": []
      }
    data["show"] = {"status": False}
    data["owner"] = ObjectId(current_user)
    data["user"] = user
    data["fundingModel"] = ProjectFundingService.ensure_model({"balance": 0, "balance_inicial": 0}, persist=False)
    
    if departamento_id:
        data["departamento_id"] = departamento_id
    
    if "categoria" in data and data["categoria"]:
        categoria, error = _resolve_category_reference(
            data.get("categoria"),
            allow_inactive=False,
            allow_deleted=False,
        )
        if error:
            return jsonify({"message": error}), 400
        data["categoria"] = categoria["_id"]
    
    project = mongo.db.proyectos.insert_one(data)

    message_log = "Usuario %s ha creado el proyecto" % user["nombre"]
    agregar_log(project.inserted_id, message_log)
    return jsonify({"message": "Proyecto creado con éxito", "_id": str(project.inserted_id)}), 201

@projects_bp.route("/actualizar_proyecto/<project_id>", methods=["PUT"])
@token_required
@validar_datos({"nombre": str, "descripcion": str})
def actualizar_proyecto(user, project_id):
    """
    Actualizar proyecto
    ---
    tags:
      - Proyectos
    security:
      - Bearer: []
    parameters:
      - in: path
        name: project_id
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
    responses:
      200:
        description: Proyecto actualizado
      404:
        description: Proyecto no encontrado
    """
    data = _normalize_project_payload(request.get_json(silent=True) or {})
    try:
        project_object_id = ObjectId(project_id)
    except Exception:
        return jsonify({"message": "ID de proyecto inválido"}), 400

    project = mongo.db.proyectos.find_one({"_id": project_object_id})
    if not project:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, project)
    if access_error:
        return access_error

    if "departamento_id" in data:
        if not is_super_admin(user):
            actor_department_object_id = parse_object_id(user_department_id(user))
            requested_department_object_id = parse_object_id(data.get("departamento_id"))
            if requested_department_object_id and actor_department_object_id and str(requested_department_object_id) != str(actor_department_object_id):
                return _forbidden("No puedes mover proyectos a otro departamento")
            data["departamento_id"] = actor_department_object_id
        else:
            department_object_id = parse_object_id(data.get("departamento_id"))
            if data.get("departamento_id") not in (None, "") and not department_object_id:
                return jsonify({"message": "ID de departamento inválido"}), 400
            if department_object_id:
                department = mongo.db.departamentos.find_one({"_id": department_object_id})
                if not department:
                    return jsonify({"message": "Departamento no encontrado"}), 400
                data["departamento_id"] = department_object_id

    update_fields = {}
    if "categoria" in data:
        categoria_value = data.get("categoria")
        if categoria_value in (None, "", " "):
            update_fields["categoria"] = None
        else:
            categoria, error = _resolve_category_reference(
                categoria_value,
                allow_inactive=False,
                allow_deleted=False,
                current_reference=project.get("categoria"),
            )
            if error:
                return jsonify({"message": error}), 400
            update_fields["categoria"] = categoria["_id"]

    for key, value in data.items():
        if key == "categoria":
            continue
        update_fields[key] = value

    if not update_fields:
        return jsonify({"message": "No hay campos para actualizar"}), 400

    mongo.db.proyectos.update_one({"_id": project_object_id}, {"$set": update_fields})
    message_log = "Usuario %s ha actualizado el proyecto" % user["nombre"]
    agregar_log(project_id, message_log)

    return jsonify({"message": "Proyecto actualizado con éxito"}), 200

@projects_bp.route("/asignar_usuario_proyecto", methods=["PATCH"])
@allow_cors
@token_required
def asignar_usuario_proyecto(user):
    """
    Asignar usuario a proyecto
    ---
    tags:
      - Proyectos
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - proyecto_id
            - usuario
            - role
          properties:
            proyecto_id:
              type: string
            usuario:
              type: object
            role:
              type: object
              properties:
                value:
                  type: string
                label:
                  type: string
    responses:
      200:
        description: Usuario asignado
      400:
        description: Usuario ya es miembro
    """
    data = request.get_json(silent=True) or {}
    proyecto_id = _pick_value(data, "projectId", "project_id", "proyecto_id")
    usuario = _pick_value(data, "user", "usuario")
    if not proyecto_id or not usuario or "role" not in data:
        return jsonify({"message": "projectId, user y role son requeridos"}), 400

    project_object_id = parse_object_id(proyecto_id)
    if not project_object_id:
        return jsonify({"message": "projectId inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    target_user_id = _extract_payload_user_id(usuario)
    if not target_user_id:
        return jsonify({"message": "El campo user debe incluir un _id válido"}), 400

    target_user_object_id = parse_object_id(target_user_id)
    if not target_user_object_id:
        return jsonify({"message": "ID de usuario inválido"}), 400

    target_user = mongo.db.usuarios.find_one({"_id": target_user_object_id})
    if not target_user:
        return jsonify({"message": "Usuario no encontrado"}), 404

    project_department_id = parse_object_id(proyecto.get("departamento_id"))
    target_user_department = parse_object_id(target_user.get("departamento_id") or target_user.get("departmentId"))
    target_user_role = (target_user.get("rol") or "usuario").strip()
    if project_department_id and target_user_role != ROLE_SUPER_ADMIN:
        if not target_user_department or str(target_user_department) != str(project_department_id):
            return jsonify({"message": "El usuario solo puede ser asignado a proyectos de su departamento"}), 400

    fecha_hora_actual = datetime.now(timezone.utc)
    member_payload = {
        "usuario": usuario,
        "role": data["role"],
        "fecha_ingreso": fecha_hora_actual.strftime("%d/%m/%Y %H:%M")
    }

    miembros = proyecto.get("miembros", [])
    if any(_extract_member_user_id(miembro) == target_user_id for miembro in miembros):
        return jsonify({"message": "El usuario ya es miembro del proyecto"}), 400

    new_status = {}
    query = {"$push": {"miembros": member_payload}}
    
    if 2 not in proyecto["status"]["completado"]:
        new_status, _ = actualizar_pasos(proyecto["status"], 2)

    if data["role"]["value"] == "lider":
        new_status, _ = actualizar_pasos(proyecto["status"], 3)

    if bool(new_status):
        query["$set"] = {"status": new_status}

    mongo.db.proyectos.update_one({"_id": project_object_id}, query)
    message_log = f'{usuario["nombre"]} fue asignado al proyecto por {user["nombre"]} con el rol {data["role"]["label"]}'
    agregar_log(proyecto_id, message_log)
    return jsonify({"message": "Usuario asignado al proyecto con éxito"}), 200

@projects_bp.route("/eliminar_usuario_proyecto", methods=["PATCH"])
@allow_cors
@token_required
def eliminar_usuario_proyecto(user):
    """
    Eliminar usuario de proyecto
    ---
    tags:
      - Proyectos
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - proyecto_id
            - usuario_id
          properties:
            proyecto_id:
              type: string
            usuario_id:
              type: string
    responses:
      200:
        description: Usuario eliminado del proyecto
      400:
        description: Usuario no es miembro
    """
    data = request.get_json(silent=True) or {}
    proyecto_id = _pick_value(data, "projectId", "project_id", "proyecto_id")
    usuario_id = _pick_value(data, "userId", "usuario_id", "user_id")
    if not proyecto_id or not usuario_id:
        return jsonify({"message": "projectId y userId son requeridos"}), 400

    project_object_id = parse_object_id(proyecto_id)
    if not project_object_id:
        return jsonify({"message": "projectId inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    usuario = None
    for miembro in proyecto.get("miembros", []):
        if _extract_member_user_id(miembro) == str(usuario_id):
            usuario = miembro["usuario"]
            break
    if usuario is None:
        return jsonify({"message": "El usuario no es miembro del proyecto"}), 400

    mongo.db.proyectos.update_one(
        {"_id": project_object_id},
        {"$pull": {"miembros": {"usuario._id.$oid": usuario_id}}},
    )
    message_log = f'{usuario["nombre"]} fue eliminado del proyecto por {user["nombre"]}'
    agregar_log(proyecto_id, message_log)
    return jsonify({"message": "Usuario eliminado del proyecto con éxito"}), 200

@projects_bp.route("/asignar_regla_distribucion", methods=["POST"])
@allow_cors
@token_required
def asignar_regla_distribucion(user):
    """
    Asignar regla de distribución a proyecto
    ---
    tags:
      - Proyectos
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - proyecto_id
            - regla_distribucion
          properties:
            proyecto_id:
              type: string
            regla_distribucion:
              type: object
    responses:
      200:
        description: Regla establecida
    """
    data = request.get_json(silent=True) or {}
    proyecto_id = _pick_value(data, "projectId", "project_id", "proyecto_id")
    regla_distribucion = _pick_value(data, "distributionRule", "regla_distribucion")
    if not proyecto_id or not isinstance(regla_distribucion, dict):
        return jsonify({"message": "projectId y distributionRule son requeridos"}), 400
    project_object_id = parse_object_id(proyecto_id)
    if not project_object_id:
        return jsonify({"message": "projectId inválido"}), 400
    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    if 4 not in proyecto["status"]["completado"]:
        new_status, _ = actualizar_pasos(proyecto["status"], 4)
        mongo.db.proyectos.update_one(
            {"_id": project_object_id},
            {"$set": {"status": new_status, "reglas": regla_distribucion}},
        )

        message_log = f'{user["nombre"]} establecio las reglas de distribucion del proyecto'
        agregar_log(proyecto_id, message_log)
        return jsonify({"message": "Regla de distribución establecida con éxito"}), 200

    return jsonify({"message": "El proyecto ya cuenta con regla de distribución"}), 200

@projects_bp.route("/asignar_balance", methods=["PATCH"])
@allow_cors
@token_required
def asignar_balance(user):
    """
    Asignar balance a proyecto
    ---
    tags:
      - Proyectos
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - project_id
            - balance
          properties:
            project_id:
              type: string
            balance:
              type: string
              description: Monto en formato string
    responses:
      200:
        description: Balance asignado
    """
    data = request.get_json(silent=True) or {}
    proyecto_id = _pick_value(data, "projectId", "project_id", "proyecto_id")
    if not proyecto_id:
        return jsonify({"message": "projectId es requerido"}), 400

    proyecto_object_id = parse_object_id(proyecto_id)
    if not proyecto_object_id:
        return jsonify({"message": "projectId inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": proyecto_object_id})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    if "balance" not in data:
        return jsonify({"message": "balance es requerido"}), 400

    model = ProjectFundingService.ensure_model(proyecto, persist=True)
    if model.get("status") in {"active", "pending_migration"}:
        return jsonify({"message": "Este proyecto usa fondeo por partidas. Utiliza el flujo de asignacion de fondos."}), 409
    if not _allow_legacy_project_balance():
        return jsonify({"message": "La carga manual de saldo legacy esta deshabilitada. Migra el proyecto a partidas."}), 409

    data_balance = string_to_int(data.get("balance"))
    balance = data_balance + int(proyecto["balance"])
    new_changes = {"balance": balance}

    if 1 not in proyecto["status"]["completado"]:
        new_status, _ = actualizar_pasos(proyecto["status"], 1)
        new_changes["status"] = new_status
        new_changes["balance_inicial"] = balance

    new_changes["fundingModel.legacyCurrentBalanceSnapshot"] = balance
    if int(proyecto.get("balance_inicial", 0) or 0) == 0:
        new_changes["fundingModel.legacyInitialBalanceSnapshot"] = balance

    mongo.db.proyectos.update_one({"_id": proyecto_object_id}, {"$set": new_changes})
    data_acciones = {
        "project_id": proyecto_object_id,
        "user": "Prueba", # TODO: Fix user name
        "type": "Fondeo",
        "amount": data_balance,
        "total_amount": balance,
        "created_at": datetime.utcnow()
    }
    mongo.db.acciones.insert_one(data_acciones)
    message_log = f'{user["nombre"]} agrego balance al proyecto por un monto de: Bs. {int_to_string(data_balance)}'
    agregar_log(proyecto_object_id, message_log)

    return jsonify({"message": "Balance asignado con éxito"}), 200

@projects_bp.route("/mostrar_proyectos", methods=["GET"])
@allow_cors
@token_required
def mostrar_proyectos(user):
    """
    Listar proyectos con paginación y búsqueda
    ---
    tags:
      - Proyectos
    security:
      - Bearer: []
    parameters:
      - in: query
        name: page
        type: integer
        default: 0
        description: Número de página (0-indexed)
      - in: query
        name: limit
        type: integer
        default: 10
        description: Cantidad de resultados por página
    responses:
      200:
        description: Lista de proyectos
        schema:
          type: object
          properties:
            request_list:
              type: array
              items:
                type: object
            count:
              type: integer
    """
    params = request.args
    page = int(params.get("page")) if params.get("page") else 0
    limit = int(params.get("limit")) if params.get("limit") else 10
    skip = page * limit  # Calcular skip basado en page y limit
    
    if is_super_admin(user):
        if user.get("_using_dept_context"):
            department_object_id = parse_object_id(user_department_id(user))
            query = {"departamento_id": department_object_id} if department_object_id else {"_id": {"$exists": False}}
        else:
            query = {}
    else:
        department_object_id = parse_object_id(user_department_id(user))
        query = {"departamento_id": department_object_id} if department_object_id else {"_id": {"$exists": False}}

    projection = {"miembros.usuario.password": 0}

    list_verification_request = mongo.db.proyectos.find(query, projection=projection).skip(skip).limit(limit)
    quantity = mongo.db.proyectos.count_documents(query)
    list_cursor = [ProjectFundingService.decorate_project(project) for project in list(list_verification_request)]
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump)
    for project in list_json:
        departamento_id = project.get("departamento_id")
        if isinstance(departamento_id, dict):
            departamento_id = departamento_id.get("$oid")
            project["departamento_id"] = departamento_id
        if departamento_id:
            project["departmentId"] = departamento_id
    return jsonify(request_list=list_json, count=quantity)

@projects_bp.route('/proyecto/<string:proyecto_id>/objetivos', methods=['GET'])
@token_required
def obtener_objetivos_especificos(user, proyecto_id):
    """
    Obtener objetivos específicos del proyecto
    ---
    tags:
      - Proyectos
    security:
      - Bearer: []
    parameters:
      - in: path
        name: proyecto_id
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
    """
    project_object_id = parse_object_id(proyecto_id)
    if not project_object_id:
        return jsonify({"message": "ID de proyecto inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id}, {"objetivos_especificos": 1, "departamento_id": 1})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    return jsonify({"objetivos_especificos": proyecto.get("objetivos_especificos", [])})

@projects_bp.route("/proyecto/<string:id>/acciones", methods=["GET"])
@allow_cors
@token_required
def acciones_proyecto(user, id):
    """
    Listar acciones/movimientos del proyecto
    ---
    tags:
      - Proyectos
    parameters:
      - in: path
        name: id
        type: string
        required: true
        description: ID del proyecto
      - in: query
        name: page
        type: integer
        default: 0
      - in: query
        name: limit
        type: integer
        default: 10
    responses:
      200:
        description: Lista de acciones
        schema:
          type: object
          properties:
            request_list:
              type: array
              items:
                type: object
            count:
              type: integer
    """
    project_object_id = parse_object_id(id)
    if not project_object_id:
        return jsonify({"message": "ID de proyecto inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id}, {"departamento_id": 1})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    params = request.args
    page = int(params.get("page")) if params.get("page") else 0
    limit = int(params.get("limit")) if params.get("limit") else 10
    skip = page * limit  # Calcular skip basado en page y limit
    acciones = mongo.db.acciones.find({"project_id": project_object_id}).skip(skip).limit(limit)
    acciones = map(map_to_doc, acciones)
    total_items = mongo.db.acciones.count_documents({"project_id": project_object_id})
    quantity = math.ceil(total_items / limit) if limit > 0 else 1
    list_cursor = list(acciones)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump)
    return jsonify(request_list=list_json, count=quantity)

@projects_bp.route("/proyecto/<string:id>", methods=["GET"])
@allow_cors
@token_required
def proyecto(user, id):
    """
    Obtener detalles completos de un proyecto
    ---
    tags:
      - Proyectos
    parameters:
      - in: path
        name: id
        type: string
        required: true
        description: ID del proyecto
    responses:
      200:
        description: Datos detallados del proyecto
        schema:
          type: object
      400:
        description: ID de proyecto inválido
      404:
        description: Proyecto no encontrado
    """
    project_object_id = parse_object_id(id.strip())
    if not project_object_id:
        return {"message": "ID de proyecto inválido"}, 400

    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id})
    if not proyecto:
        return jsonify({"error": "proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    proyecto = ProjectFundingService.decorate_project(proyecto, user=user)
    proyecto["_id"] = str(proyecto["_id"])
    proyecto["owner"] = str(proyecto["owner"])
    if proyecto.get("departamento_id"):
        proyecto["departamento_id"] = str(proyecto["departamento_id"])
        proyecto["departmentId"] = proyecto["departamento_id"]

    if "regla_fija" in proyecto:
        proyecto["regla_fija"]["_id"] = str(proyecto["regla_fija"]["_id"])
    return jsonify(proyecto)

@projects_bp.route("/eliminar_proyecto", methods=["POST"])
@allow_cors
@token_required
def eliminar_proyecto(user):
    """
    Eliminar un proyecto
    ---
    tags:
      - Proyectos
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - proyecto_id
          properties:
            proyecto_id:
              type: string
    responses:
      200:
        description: Proyecto eliminado exitosamente
      404:
        description: Proyecto no encontrado
    """
    data = request.get_json(silent=True) or {}
    id = _pick_value(data, "projectId", "project_id", "proyecto_id")
    if not id:
        return jsonify({"message": "projectId es requerido"}), 400
    project_object_id = parse_object_id(id)
    if not project_object_id:
        return jsonify({"message": "projectId inválido"}), 400

    documento = mongo.db.proyectos.find_one({"_id": project_object_id})
    if documento is None:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, documento)
    if access_error:
        return access_error

    result = mongo.db.proyectos.delete_one({"_id": project_object_id})
    if result.deleted_count == 1:
        return jsonify({"message": "Proyecto eliminado con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar la regla"}), 400

@projects_bp.route("/finalizar_proyecto", methods=["POST"])
@allow_cors
@token_required
def finalizar_proyecto(user):
    """
    Finalizar proyecto y generar acta
    ---
    tags:
      - Proyectos
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - proyecto_id
          properties:
            proyecto_id:
              type: string
    responses:
      200:
        description: Proyecto finalizado exitosamente
      404:
        description: Proyecto no encontrado
    """
    data = request.get_json(silent=True) or {}
    proyecto_id = _pick_value(data, "projectId", "project_id", "proyecto_id")
    if not proyecto_id:
        return jsonify({"message": "projectId es requerido"}), 400
    project_object_id = parse_object_id(proyecto_id)
    if not project_object_id:
        return jsonify({"message": "projectId inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    movimientos = ProjectFundingService.build_timeline(proyecto)
    movimientos_simple = [{"type": m.get("title"), "amount": m.get("amount", 0), "user": m.get("actorName", "N/A")} for m in movimientos]

    logs = list(mongo.db.logs.find({"$or": [{"proyecto_id": project_object_id}, {"id_proyecto": project_object_id}]}))
    logs_simple = [{"fecha": str(ls.get("fecha_creacion")), "mensaje": ls.get("mensaje")} for ls in logs]

    presupuestos = list(mongo.db.documentos.find({"$or": [{"project_id": project_object_id}, {"proyecto_id": project_object_id}]}))
    presupuestos_simple = [{"descripcion": b.get("descripcion", ""), "monto_aprobado": b.get("monto_aprobado", 0)} for b in presupuestos]

    mongo.db.proyectos.update_one(
        {"_id": project_object_id},
        {"$set": {"status.finished": True, "fecha_fin": datetime.utcnow()}}
    )

    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id})
    proyecto = ProjectFundingService.decorate_project(proyecto)
    
    try:
        pdf_bytes = generar_acta_finalizacion_pdf(
            proyecto,
            movements=movimientos_simple,
            logs=logs_simple,
            budgets=presupuestos_simple
        )
        file_name = f"actas/acta_finalizacion_{str(proyecto['_id'])}.pdf"
        upload_result = upload_file(BytesIO(pdf_bytes), file_name)
        
        mongo.db.proyectos.update_one(
            {"_id": project_object_id},
            {"$set": {"acta_finalizacion": {"fecha": datetime.utcnow(), "documento_url": upload_result["download_url"], "file_id": upload_result["fileId"]}}}
        )
    except Exception as e:
        print(f"❌ Error generando o subiendo acta finalización: {e}")

    return jsonify({"message": "Proyecto finalizado exitosamente."}), 200

@projects_bp.route("/proyecto/<string:id>/logs", methods=["GET"])
@allow_cors
@token_required
def obtener_logs(user, id):
    """
    Listar logs de auditoría del proyecto
    ---
    tags:
      - Proyectos
    parameters:
      - in: path
        name: id
        type: string
        required: true
      - in: query
        name: page
        type: integer
        default: 0
      - in: query
        name: limit
        type: integer
        default: 10
    responses:
      200:
        description: Lista de logs
        schema:
          type: object
          properties:
            request_list:
              type: array
            count:
              type: integer
    """
    project_object_id = parse_object_id(id)
    if not project_object_id:
        return jsonify({"message": "ID de proyecto inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id}, {"departamento_id": 1})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    params = request.args
    page = int(params.get("page")) if params.get("page") else 0
    limit = int(params.get("limit")) if params.get("limit") else 10
    skip = page * limit  # Calcular skip basado en page y limit
    acciones = mongo.db.logs.find({"id_proyecto": project_object_id}).skip(skip).limit(limit)
    total_items = mongo.db.logs.count_documents({"id_proyecto": project_object_id})
    quantity = math.ceil(total_items / limit) if limit > 0 else 1
    list_cursor = list(acciones)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump)
    return jsonify(request_list=list_json, count=quantity), 200

@projects_bp.route("/proyecto/<string:id>/movimientos/descargar", methods=["GET"])
@allow_cors
@token_required
def descargar_movimientos(user, id):
    """
    Descargar movimientos del proyecto
    ---
    tags:
      - Proyectos
    parameters:
      - in: path
        name: id
        type: string
        required: true
      - in: query
        name: formato
        type: string
        enum: [csv, json]
        default: csv
    responses:
      200:
        description: Archivo de movimientos (CSV o JSON)
      400:
        description: Formato no válido
    """
    id_proyecto = parse_object_id(id)
    if not id_proyecto:
        return jsonify({"message": "ID de proyecto inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": id_proyecto})
    if not proyecto:
        return jsonify({"error": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    movimientos_lista = ProjectFundingService.build_timeline(proyecto)
    formato = request.args.get("formato", "csv").lower()

    if formato == "csv":
        return generar_csv(movimientos_lista)
    elif formato == "json":
        return generar_json(movimientos_lista)
    else:
        return jsonify({"error": "Formato no válido. Use 'csv' o 'json'."}), 400

@projects_bp.route("/proyecto/<string:id>/fin", methods=["GET"])
@allow_cors
@token_required
def mostrar_finalizacion(user, id):
    """
    Obtener datos para el acta de finalización
    ---
    tags:
      - Proyectos
    parameters:
      - in: path
        name: id
        type: string
        required: true
    responses:
      200:
        description: Datos consolidados (logs, documentos, movimientos)
        schema:
          type: object
          properties:
            logs:
              type: array
            documentos:
              type: array
            movimientos:
              type: array
    """
    project_object_id = parse_object_id(id)
    if not project_object_id:
        return jsonify({"message": "ID de proyecto inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": project_object_id})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    access_error = _ensure_project_access(user, proyecto)
    if access_error:
        return access_error

    movs = ProjectFundingService.build_timeline(proyecto)
    docs = mongo.db.documentos.find({"project_id": project_object_id})
    logs = mongo.db.logs.find({"id_proyecto": project_object_id})

    movs_json = json.loads(json_util.dumps(list(movs), default=json_util.default, ensure_ascii=False))
    docs_json = json.loads(json_util.dumps(list(docs), default=json_util.default, ensure_ascii=False))
    logs_json = json.loads(json_util.dumps(list(logs), default=json_util.default, ensure_ascii=False))

    return jsonify(logs=logs_json, documentos=docs_json, movimientos=movs_json), 200
