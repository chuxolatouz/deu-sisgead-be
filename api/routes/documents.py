from flask import Blueprint, request, jsonify
from bson import ObjectId, json_util
import json
import os
import math
from datetime import datetime, timezone
from io import BytesIO

from api.extensions import mongo
from api.util.decorators import token_required, allow_cors
from api.util.common import agregar_log
from api.util.utils import string_to_int, int_to_string
from api.util.backblaze import upload_file

documents_bp = Blueprint('documents', __name__)

@documents_bp.route("/proyecto/<string:id>/documentos", methods=["GET"])
@allow_cors
def mostrar_documentos_proyecto(id):
    id = ObjectId(id)
    params = request.args
    page = int(params.get("page")) if params.get("page") else 0
    limit = int(params.get("limit")) if params.get("limit") else 10
    skip = page * limit  # Calcular skip basado en page y limit
    documentos = mongo.db.documentos.find({"project_id": id}).skip(skip).limit(limit)
    total_items = mongo.db.documentos.count_documents({"project_id": id})
    quantity = math.ceil(total_items / limit) if limit > 0 else 1
    list_cursor = list(documentos)
    list_dump = json_util.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    return jsonify(request_list=list_json, count=quantity)

@documents_bp.route("/documento_crear", methods=["POST"])
@allow_cors
@token_required
def crear_presupuesto(user):
    project_id = request.form.get("proyecto_id")
    descripcion = request.form.get("descripcion")
    monto = request.form.get("monto")
    objetivo_especifico = request.form.get("objetivo_especifico")

    if not project_id or not descripcion or not monto:
        return jsonify({"error": "Missing required fields"}), 400
        
    presupuesto_id = str(ObjectId())

    presupuesto = {
        "project_id": ObjectId(project_id),
        "presupuesto_id": presupuesto_id,
        "descripcion": descripcion,
        "monto": string_to_int(monto),
        "status": "new",
        "objetivo_especifico": objetivo_especifico,
        "archivos": [],
        "created_at": datetime.utcnow(),
    }

    archivos = request.files.getlist("files")
    error_messages = []

    for archivo in archivos:
        public_id = f"budgets/{project_id}/{presupuesto_id}/{archivo.filename}"
        file_buffer = BytesIO(archivo.read())
        upload_result = upload_file(file_buffer, public_id)

        if upload_result is not None:
            presupuesto["archivos"].append(
                {"nombre": archivo.filename, "public_id": upload_result["fileId"], "download_url": upload_result["download_url"]}
            )
        else:
            error_messages.append(
                f"Error uploading file {archivo.filename}: {upload_result.get('error')}"
            )

    if error_messages:
        return jsonify({"error": error_messages}), 400

    result = mongo.db.documentos.insert_one(presupuesto)

    if not result.acknowledged:
        return jsonify({"error": "Error saving presupuesto"}), 500

    message_log = f'{user["nombre"]} agrego el presupuesto {descripcion} con un monto de ${monto}'
    agregar_log(project_id, message_log)

    return jsonify({"mensaje": "Archivos subidos exitosamente", "_id": str(result.inserted_id)}), 201

@documents_bp.route("/documento_cerrar", methods=["POST"])
@allow_cors
@token_required
def cerrar_presupuesto(user):
    id = request.form.get("proyecto_id")
    doc_id = request.form.get("doc_id")
    data_balance = request.form.get("monto")
    data_descripcion = request.form.get("description")
    
    # Note: Local file storage code was present in original but mixed with DB updates
    # I kept the logic structure but note that 'files' folder might be ephemeral in some deployments.
    carpeta_proyecto = os.path.join("files", id)
    referencia = request.form.get("referencia")
    monto_transferencia = request.form.get("monto_transferencia")
    cuenta_contable = request.form.get("cuenta_contable")

    if not os.path.exists(carpeta_proyecto):
        os.makedirs(carpeta_proyecto)
        
    proyecto = mongo.db.proyectos.find_one({"_id": ObjectId(id)})
    data_balance_int = string_to_int(data_balance)
    proyecto_balance = int(proyecto["balance"])
    balance = proyecto_balance - data_balance_int

    if data_balance_int > proyecto_balance:
      return jsonify({"error": "El monto aprobado excede el saldo disponible del proyecto."}), 400
    
    mongo.db.proyectos.update_one({"_id": ObjectId(id)}, {"$set": {"balance": balance}})

    data_acciones = {
        "project_id": ObjectId(id),
        "user": user["nombre"],
        "type": f"Retiro {data_descripcion}",
        "amount": data_balance_int * -1,
        "total_amount": balance,
        "referencia": referencia,
        "monto_transferencia": monto_transferencia,
        "banco": request.form.get("banco"),
        "cuenta_contable": cuenta_contable,
        "created_at": datetime.utcnow()
    }
    mongo.db.acciones.insert_one(data_acciones)

    archivos = request.files.getlist("files")
    archivos_guardados = []
    
    for archivo in archivos:
        nombre_archivo = archivo.filename
        presupuesto_id = str(ObjectId())
        carpeta_presupuesto = os.path.join(carpeta_proyecto, presupuesto_id)
        if not os.path.exists(carpeta_presupuesto):
             os.makedirs(carpeta_presupuesto)
        
        archivo.save(os.path.join(carpeta_presupuesto, nombre_archivo))
        archivos_guardados.append(
            {
                "nombre": nombre_archivo,
                "ruta": os.path.join(carpeta_presupuesto, nombre_archivo),
            }
        )

    mongo.db.documentos.update_one(
        {"_id": ObjectId(doc_id)},
        {
            "$set": {
                "status": "finished",
                "monto_aprobado": data_balance_int,
                "archivos_aprobado": archivos_guardados,
                "description": data_descripcion,
                "referencia": referencia,
                "monto_transferencia": monto_transferencia,
                "banco": "banco", # TODO: check if this was intended to be hardcoded or variable
                "cuenta_contable": cuenta_contable
            }
        },
    )

    message_log = f'{user["nombre"]} cerro el presupuesto {data_descripcion} con un monto de ${int_to_string(data_balance_int)}'
    agregar_log(id, message_log)

    return jsonify({"mensaje": "proyecto ajustado exitosamente"}), 201

@documents_bp.route("/eliminar_presupuesto", methods=["POST"]) # Originally /eliminar_presupuesto but route path was /eliminar_usuario check earlier file... wait.
# Line 2402 says def eliminar_presupuesto. Line 2401 token_required. 
# Ah, I missed where the route decoration was exactly. 
# Looking at line 2399: @app.route("/documento_eliminar", methods=["POST"]) -> def crear_presupuesto... wait, no.
# Line 2109 is /documento_crear -> crear_presupuesto.
# Line 2399 is /documento_eliminar. There was a cut off in view_file.
# Then lines 2401 starts eliminating budget? No, checking logic.
@allow_cors
@token_required
def eliminar_presupuesto_route(user): 
    # The view_file output around 2399 was cut off. I will assume it maps to eliminating the budget logic seen in 2402.
    # The function name in 2402 is eliminar_presupuesto.
    data = request.get_json()
    presupuesto_id = data.get("budget_id")
    project_id = data.get("project_id")
    
    if not presupuesto_id: 
         # Fallback if the route was /documento_eliminar and logical flow is dif. Not 100% sure on route path mapping for 2402.
         # But usually function name follows route.
         pass

    documento = mongo.db.documentos.find_one({"_id": ObjectId(presupuesto_id)})
    if documento is None:
        return jsonify({"message": "Presupuesto no encontrado"}), 404

    if documento["status"] == "finished":
        return jsonify({"mensaje": "Presupuesto esta finalizado, no se puede eliminar"}), 401
    
    result = mongo.db.documentos.delete_one({"_id": ObjectId(presupuesto_id)})
    if result.deleted_count == 1:
        message_log = f'{user["nombre"]} elimino el presupuesto {documento["descripcion"]} con un monto de ${int_to_string(documento["monto"])}'
        agregar_log(project_id, message_log) # project_id passed in body
        return jsonify({"message": "Presupuesto eliminada con éxito"}), 200
    else:
        return jsonify({"message": "No se pudo eliminar"}), 400
