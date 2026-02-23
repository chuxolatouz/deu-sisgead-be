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
    try:
        object_id = ObjectId(project_id.strip())
    except Exception:
        return None, (jsonify({"message": "ID de proyecto inválido"}), 400)

    project = mongo.db.proyectos.find_one({"_id": object_id})
    if not project:
        return None, (jsonify({"message": "Proyecto no encontrado"}), 404)
    return project, None




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
    if payload_department_id:
        try:
            dept_id_obj = ObjectId(str(payload_department_id))
            departamento = mongo.db.departamentos.find_one({"_id": dept_id_obj})
            if not departamento:
                return jsonify({"message": "Departamento no encontrado"}), 400
            departamento_id = dept_id_obj
        except Exception:
            return jsonify({"message": "ID de departamento inválido"}), 400
    elif user.get("_using_dept_context") and ("departamento_id" in user or "departmentId" in user):
        try:
            departamento_id = ObjectId(user.get("departmentId") or user.get("departamento_id"))
        except Exception:
            pass
    elif user.get("role") == "admin_departamento" and ("departamento_id" in user or "departmentId" in user):
        try:
            departamento_id = ObjectId(user.get("departmentId") or user.get("departamento_id"))
        except Exception:
            pass
    
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
    
    if departamento_id:
        data["departamento_id"] = departamento_id
    
    # Process categoria: buscar por value y convertir a ObjectId
    if "categoria" in data and data["categoria"]:
        categoria = None
        # Intentar primero si es un ObjectId válido
        try:
            categoria_id = ObjectId(data["categoria"])
            categoria = mongo.db.categorias.find_one({"_id": categoria_id})
        except Exception:
            # Si no es ObjectId, buscar por value (string como "recaudacion")
            categoria = mongo.db.categorias.find_one({"value": data["categoria"]})
        
        if categoria:
            data["categoria"] = categoria["_id"]
        else:
            return jsonify({"message": "Categoría no encontrada"}), 400
    
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
    project = mongo.db.proyectos.find_one({"_id": ObjectId(project_id)})
    if not project:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    for key, value in data.items():
        project[key] = value

    mongo.db.proyectos.update_one({"_id": ObjectId(project_id)}, {"$set": project})
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
    data = request.get_json()
    proyecto_id = _pick_value(data, "projectId", "project_id", "proyecto_id")
    usuario = _pick_value(data, "user", "usuario")
    if not proyecto_id or not usuario or "role" not in data:
        return jsonify({"message": "projectId, user y role son requeridos"}), 400
    fecha_hora_actual = datetime.now(timezone.utc)
    member_payload = {
        "usuario": usuario,
        "role": data["role"],
        "fecha_ingreso": fecha_hora_actual.strftime("%d/%m/%Y %H:%M")
    }
    
    proyecto = mongo.db.proyectos.find_one({"_id": ObjectId(proyecto_id)})
    miembros = proyecto["miembros"]

    if any(miembro["usuario"]["_id"]["$oid"] == usuario["_id"]["$oid"] for miembro in miembros):
        return jsonify({"message": "El usuario ya es miembro del proyecto"}), 400

    new_status = {}
    query = {"$push": {"miembros": member_payload}}
    
    if 2 not in proyecto["status"]["completado"]:
        new_status, _ = actualizar_pasos(proyecto["status"], 2)

    if data["role"]["value"] == "lider":
        new_status, _ = actualizar_pasos(proyecto["status"], 3)

    if bool(new_status):
        query["$set"] = {"status": new_status}

    mongo.db.proyectos.update_one({"_id": ObjectId(proyecto_id)}, query)
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
    data = request.get_json()
    proyecto_id = _pick_value(data, "projectId", "project_id", "proyecto_id")
    usuario_id = _pick_value(data, "userId", "usuario_id", "user_id")
    if not proyecto_id or not usuario_id:
        return jsonify({"message": "projectId y userId son requeridos"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": ObjectId(proyecto_id)})
    usuario = None
    for miembro in proyecto["miembros"]:
        if miembro["usuario"]["_id"]["$oid"] == usuario_id:
            usuario = miembro["usuario"]
            break
    if usuario is None:
        return jsonify({"message": "El usuario no es miembro del proyecto"}), 400

    mongo.db.proyectos.update_one(
        {"_id": ObjectId(proyecto_id)},
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
    data = request.get_json()
    proyecto_id = _pick_value(data, "projectId", "project_id", "proyecto_id")
    regla_distribucion = _pick_value(data, "distributionRule", "regla_distribucion")
    if not proyecto_id or not isinstance(regla_distribucion, dict):
        return jsonify({"message": "projectId y distributionRule son requeridos"}), 400
    proyecto = mongo.db.proyectos.find_one({"_id": ObjectId(proyecto_id)})

    if 4 not in proyecto["status"]["completado"]:
        new_status, _ = actualizar_pasos(proyecto["status"], 4)
        mongo.db.proyectos.update_one(
            {"_id": ObjectId(proyecto_id)},
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

    try:
        proyecto_object_id = ObjectId(str(proyecto_id))
    except Exception:
        return jsonify({"message": "projectId inválido"}), 400

    proyecto = mongo.db.proyectos.find_one({"_id": proyecto_object_id})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404
    if "balance" not in data:
        return jsonify({"message": "balance es requerido"}), 400

    data_balance = string_to_int(data.get("balance"))
    balance = data_balance + int(proyecto["balance"])
    new_changes = {"balance": balance}

    if 1 not in proyecto["status"]["completado"]:
        new_status, _ = actualizar_pasos(proyecto["status"], 1)
        new_changes["status"] = new_status
        new_changes["balance_inicial"] = balance

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
    
    query = {
        "$or": [
            {"owner": user["sub"]},
            {"miembros": {"$elemMatch": {"usuario._id.$oid": user["sub"]}}},
        ]
    }
    
    if user.get("role") == "super_admin":
        if user.get("_using_dept_context") and ("departamento_id" in user or "departmentId" in user):
            try:
                dept_id = ObjectId(user.get("departmentId") or user.get("departamento_id"))
                query = {
                    "$or": [
                        {"departamento_id": dept_id},
                        {"owner": user["sub"]},
                        {"miembros": {"$elemMatch": {"usuario._id.$oid": user["sub"]}}},
                    ]
                }
            except Exception:
                query = {}
        else:
            query = {} 
    elif user.get("role") == "admin_departamento" and ("departamento_id" in user or "departmentId" in user):
        try:
            dept_id = ObjectId(user.get("departmentId") or user.get("departamento_id"))
            query = {
                "$or": [
                    {"departamento_id": dept_id},
                    {"owner": user["sub"]},
                    {"miembros": {"$elemMatch": {"usuario._id.$oid": user["sub"]}}},
                ]
            }
        except Exception:
            pass

    projection = {"miembros.usuario.password": 0}

    list_verification_request = mongo.db.proyectos.find(query, projection=projection).skip(skip).limit(limit)
    quantity = mongo.db.proyectos.count_documents(query)
    list_cursor = list(list_verification_request)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
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
def obtener_objetivos_especificos(_, proyecto_id):
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
    proyecto = mongo.db.proyectos.find_one({"_id": ObjectId(proyecto_id)}, {"objetivos_especificos": 1})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404
    return jsonify({"objetivos_especificos": proyecto.get("objetivos_especificos", [])})

@projects_bp.route("/proyecto/<string:id>/acciones", methods=["GET"])
@allow_cors
def acciones_proyecto(id):
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
    id = ObjectId(id)
    params = request.args
    page = int(params.get("page")) if params.get("page") else 0
    limit = int(params.get("limit")) if params.get("limit") else 10
    skip = page * limit  # Calcular skip basado en page y limit
    acciones = mongo.db.acciones.find({"project_id": id}).skip(skip).limit(limit)
    acciones = map(map_to_doc, acciones)
    total_items = mongo.db.acciones.count_documents({"project_id": id})
    quantity = math.ceil(total_items / limit) if limit > 0 else 1
    list_cursor = list(acciones)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    return jsonify(request_list=list_json, count=quantity)

@projects_bp.route("/proyecto/<string:id>", methods=["GET"])
@allow_cors
def proyecto(id):
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
    try:
        id = ObjectId(id.strip())
    except Exception:
        return {"message": "ID de proyecto inválido"}, 400

    proyecto = mongo.db.proyectos.find_one({"_id": id})
    if not proyecto:
        return jsonify({"error": "proyecto no encontrado"}), 404

    proyecto["_id"] = str(proyecto["_id"])
    balance = int_to_float(proyecto["balance"])
    balance_inicial = int_to_float(proyecto["balance_inicial"])
    proyecto["balance"] = balance
    proyecto["owner"] = str(proyecto["owner"])
    proyecto["balance_inicial"] = balance_inicial
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
    documento = mongo.db.proyectos.find_one({"_id": ObjectId(id)})
    if documento is None:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    result = mongo.db.proyectos.delete_one({"_id": ObjectId(id)})
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
    proyecto = mongo.db.proyectos.find_one({"_id": ObjectId(proyecto_id)})
    if not proyecto:
        return jsonify({"message": "Proyecto no encontrado"}), 404

    project_object_id = ObjectId(proyecto_id)
    movimientos = list(mongo.db.acciones.find({"$or": [{"project_id": project_object_id}, {"proyecto_id": project_object_id}]}))
    movimientos_simple = [{"type": m.get("type"), "amount": m.get("amount", 0), "user": m.get("user", "N/A")} for m in movimientos]

    logs = list(mongo.db.logs.find({"$or": [{"proyecto_id": project_object_id}, {"id_proyecto": project_object_id}]}))
    logs_simple = [{"fecha": str(ls.get("fecha_creacion")), "mensaje": ls.get("mensaje")} for ls in logs]

    presupuestos = list(mongo.db.documentos.find({"$or": [{"project_id": project_object_id}, {"proyecto_id": project_object_id}]}))
    presupuestos_simple = [{"descripcion": b.get("descripcion", ""), "monto_aprobado": b.get("monto_aprobado", 0)} for b in presupuestos]

    mongo.db.proyectos.update_one(
        {"_id": ObjectId(proyecto_id)},
        {"$set": {"status.finished": True, "fecha_fin": datetime.utcnow()}}
    )

    proyecto = mongo.db.proyectos.find_one({"_id": ObjectId(proyecto_id)})
    
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
            {"_id": ObjectId(proyecto['_id'])},
            {"$set": {"acta_finalizacion": {"fecha": datetime.utcnow(), "documento_url": upload_result["download_url"], "file_id": upload_result["fileId"]}}}
        )
    except Exception as e:
        print(f"❌ Error generando o subiendo acta finalización: {e}")

    return jsonify({"message": "Proyecto finalizado exitosamente."}), 200

@projects_bp.route("/proyecto/<string:id>/logs", methods=["GET"])
@allow_cors
def obtener_logs(id):
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
    id = ObjectId(id)
    params = request.args
    page = int(params.get("page")) if params.get("page") else 0
    limit = int(params.get("limit")) if params.get("limit") else 10
    skip = page * limit  # Calcular skip basado en page y limit
    acciones = mongo.db.logs.find({"id_proyecto": id}).skip(skip).limit(limit)
    total_items = mongo.db.logs.count_documents({"id_proyecto": id})
    quantity = math.ceil(total_items / limit) if limit > 0 else 1
    list_cursor = list(acciones)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    return jsonify(request_list=list_json, count=quantity), 200

@projects_bp.route("/proyecto/<string:id>/movimientos/descargar", methods=["GET"])
@allow_cors
def descargar_movimientos(id):
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
    id_proyecto = ObjectId(id)
    movimientos = mongo.db.acciones.find({"project_id": id_proyecto})
    movimientos_lista = list(movimientos)
    formato = request.args.get("formato", "csv").lower()

    if formato == "csv":
        return generar_csv(movimientos_lista)
    elif formato == "json":
        return generar_json(movimientos_lista)
    else:
        return jsonify({"error": "Formato no válido. Use 'csv' o 'json'."}), 400

@projects_bp.route("/proyecto/<string:id>/fin", methods=["GET"])
@allow_cors
def mostrar_finalizacion(id):
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
    id = ObjectId(id)
    movs = mongo.db.acciones.find({"project_id": id})
    docs = mongo.db.documentos.find({"project_id": id})
    logs = mongo.db.logs.find({"id_proyecto": id})

    movs_json = json.loads(json_util.dumps(list(movs)).replace("\\", ""))
    docs_json = json.loads(json_util.dumps(list(docs)).replace("\\", ""))
    logs_json = json.loads(json_util.dumps(list(logs)).replace("\\", ""))

    return jsonify(logs=logs_json, documentos=docs_json, movimientos=movs_json), 200
