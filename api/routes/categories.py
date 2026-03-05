import random
import re
from datetime import datetime, timezone

from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
from pymongo.errors import DuplicateKeyError

from api.extensions import mongo
from api.util.decorators import allow_cors, token_required

categories_bp = Blueprint('categories', __name__)


def _parse_bool_arg(key, default=False):
    raw_value = request.args.get(key)
    if raw_value is None:
        return default
    return str(raw_value).strip().lower() in {"1", "true", "yes", "si", "on"}


def _utcnow():
    return datetime.now(timezone.utc)


def _is_super_admin(user):
    return user.get("role") == "super_admin"


def _slugify(value):
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "categoria"


def _normalize_color(value):
    if value is None:
        return None
    color = str(value).strip().lstrip("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}", color):
        return None
    return color


def _random_color():
    return "".join(random.choices("0123456789ABCDEF", k=6))


def _category_name_normalized(name):
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _parse_object_id(value):
    try:
        return ObjectId(str(value).strip())
    except Exception:
        return None


def _safe_create_index(spec, **kwargs):
    try:
        mongo.db.categorias.create_index(spec, **kwargs)
    except Exception as exc:
        current_app.logger.warning("No se pudo crear índice de categorías: %s", exc)


def _ensure_category_indexes():
    _safe_create_index([("value", 1)], unique=True, sparse=True)
    _safe_create_index([("nombre_normalizado", 1)], unique=True, sparse=True)
    _safe_create_index([("activo", 1), ("eliminado", 1)])


def _generate_unique_value(base_value, exclude_id=None):
    base = _slugify(base_value)
    suffix = 0
    while True:
        candidate = base if suffix == 0 else f"{base}-{suffix + 1}"
        query = {"value": candidate}
        if exclude_id:
            query["_id"] = {"$ne": exclude_id}
        exists = mongo.db.categorias.find_one(query, {"_id": 1})
        if not exists:
            return candidate
        suffix += 1


def _serialize_category(category, include_stats=False, project_counts=None):
    category_id = str(category.get("_id"))
    nombre = (category.get("nombre") or "").strip()
    value = category.get("value") or _slugify(nombre)
    label = category.get("label") or nombre
    payload = {
        "_id": category_id,
        "nombre": nombre,
        "value": value,
        "label": label,
        "color": _normalize_color(category.get("color")) or _random_color(),
        "activo": False if category.get("activo") is False else True,
        "eliminado": True if category.get("eliminado") is True else False,
        "createdAt": category.get("createdAt") or category.get("created_at"),
        "updatedAt": category.get("updatedAt") or category.get("updated_at"),
        "deletedAt": category.get("deletedAt") or category.get("deleted_at"),
    }
    if include_stats:
        counts = project_counts or {}
        payload["projectCount"] = int(counts.get(category_id, 0)) + int(counts.get(value, 0))
    return payload


def _project_count_map():
    counts = {}
    pipeline = [
        {"$group": {"_id": "$categoria", "count": {"$sum": 1}}},
    ]
    for row in mongo.db.proyectos.aggregate(pipeline):
        key = row.get("_id")
        if key in (None, ""):
            continue
        if isinstance(key, ObjectId):
            normalized_key = str(key)
        elif isinstance(key, dict) and "$oid" in key:
            normalized_key = str(key.get("$oid"))
        else:
            normalized_key = str(key)
        counts[normalized_key] = int(row.get("count", 0))
    return counts


@categories_bp.route("/mostrar_categorias", methods=["GET"])
@allow_cors
def obtener_categorias():
    """
    Listar categorías con búsqueda opcional
    ---
    tags:
      - Categorías
    parameters:
      - in: query
        name: text
        type: string
        description: Texto para buscar en el nombre de la categoría
        example: "Material"
    responses:
      200:
        description: Lista de categorías
        schema:
          type: array
          items:
            type: object
            properties:
              _id:
                type: string
                example: "507f1f77bcf86cd799439011"
              nombre:
                type: string
                example: "Material de Oficina"
              color:
                type: string
                example: "FF5733"
                description: Color en formato hexadecimal
    """
    _ensure_category_indexes()
    search_text = (request.args.get("text") or "").strip()
    active_only = _parse_bool_arg("activeOnly", default=False)
    include_inactive = _parse_bool_arg("includeInactive", default=True)
    include_deleted = _parse_bool_arg("includeDeleted", default=False)
    include_stats = _parse_bool_arg("includeStats", default=False)

    query = {}
    if search_text:
        query["nombre"] = {"$regex": re.escape(search_text), "$options": "i"}

    if active_only:
        query["activo"] = {"$ne": False}
        query["eliminado"] = {"$ne": True}
    else:
        if not include_inactive:
            query["activo"] = {"$ne": False}
        if not include_deleted:
            query["eliminado"] = {"$ne": True}

    cursor = mongo.db.categorias.find(query).sort("nombre", 1)
    rows = list(cursor)
    project_counts = _project_count_map() if include_stats else None
    payload = [
        _serialize_category(row, include_stats=include_stats, project_counts=project_counts)
        for row in rows
    ]
    return jsonify(payload), 200

@categories_bp.route("/categorias", methods=["POST"])
@allow_cors
@token_required
def crear_categorias(user):
    """
    Crear una nueva categoría
    ---
    tags:
      - Categorías
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - nombre
          properties:
            nombre:
              type: string
              description: Nombre de la categoría
              example: "Material de Oficina"
    responses:
      201:
        description: Categoría creada con éxito
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Categoría creada con éxito"
            _id:
              type: string
              example: "507f1f77bcf86cd799439011"
      400:
        description: Datos inválidos
        schema:
          type: object
          properties:
            message:
              type: string
    """
    if not _is_super_admin(user):
        return jsonify({"message": "Solo super_admin puede administrar categorías"}), 403

    _ensure_category_indexes()
    data = request.get_json(silent=True) or {}
    nombre = data.get("nombre")
    if not isinstance(nombre, str) or not nombre.strip():
        return jsonify({"message": "El campo 'nombre' es requerido"}), 400

    normalized_name = _category_name_normalized(nombre)
    existing_name = mongo.db.categorias.find_one(
        {"nombre_normalizado": normalized_name, "eliminado": {"$ne": True}}
    )
    if existing_name:
        return jsonify({"message": "Ya existe una categoría con ese nombre"}), 409

    color = _normalize_color(data.get("color")) or _random_color()
    value = _generate_unique_value(data.get("value") or nombre)
    now = _utcnow()
    categoria = {
        "nombre": nombre.strip(),
        "nombre_normalizado": normalized_name,
        "value": value,
        "label": nombre.strip(),
        "color": color,
        "activo": True,
        "eliminado": False,
        "createdAt": now,
        "updatedAt": now,
        "deletedAt": None,
    }

    try:
        categoria_insertada = mongo.db.categorias.insert_one(categoria)
    except DuplicateKeyError:
        return jsonify({"message": "Ya existe una categoría con ese identificador"}), 409

    categoria["_id"] = categoria_insertada.inserted_id
    return jsonify(
        {
            "message": "Categoría creada con éxito",
            "_id": str(categoria_insertada.inserted_id),
            "category": _serialize_category(categoria),
        }
    ), 201


@categories_bp.route("/categorias/<string:category_id>", methods=["PUT"])
@allow_cors
@token_required
def actualizar_categoria(user, category_id):
    if not _is_super_admin(user):
        return jsonify({"message": "Solo super_admin puede administrar categorías"}), 403

    object_id = _parse_object_id(category_id)
    if not object_id:
        return jsonify({"message": "ID de categoría inválido"}), 400

    categoria = mongo.db.categorias.find_one({"_id": object_id})
    if not categoria:
        return jsonify({"message": "Categoría no encontrada"}), 404

    data = request.get_json(silent=True) or {}
    updates = {}

    if "nombre" in data:
        nombre = data.get("nombre")
        if not isinstance(nombre, str) or not nombre.strip():
            return jsonify({"message": "El campo 'nombre' es requerido"}), 400
        normalized_name = _category_name_normalized(nombre)
        duplicate = mongo.db.categorias.find_one(
            {
                "_id": {"$ne": object_id},
                "nombre_normalizado": normalized_name,
                "eliminado": {"$ne": True},
            },
            {"_id": 1},
        )
        if duplicate:
            return jsonify({"message": "Ya existe una categoría con ese nombre"}), 409

        updates["nombre"] = nombre.strip()
        updates["label"] = nombre.strip()
        updates["nombre_normalizado"] = normalized_name
        if not categoria.get("value"):
            updates["value"] = _generate_unique_value(nombre.strip(), exclude_id=object_id)

    if "color" in data:
        color = _normalize_color(data.get("color"))
        if not color:
            return jsonify({"message": "El color debe tener formato hexadecimal de 6 caracteres"}), 400
        updates["color"] = color

    if not updates:
        return jsonify({"message": "No hay campos para actualizar"}), 400

    updates["updatedAt"] = _utcnow()
    try:
        mongo.db.categorias.update_one({"_id": object_id}, {"$set": updates})
    except DuplicateKeyError:
        return jsonify({"message": "Ya existe una categoría con ese identificador"}), 409

    updated = mongo.db.categorias.find_one({"_id": object_id})
    return jsonify({"message": "Categoría actualizada con éxito", "category": _serialize_category(updated)}), 200


@categories_bp.route("/categorias/<string:category_id>/estado", methods=["PATCH"])
@allow_cors
@token_required
def cambiar_estado_categoria(user, category_id):
    if not _is_super_admin(user):
        return jsonify({"message": "Solo super_admin puede administrar categorías"}), 403

    object_id = _parse_object_id(category_id)
    if not object_id:
        return jsonify({"message": "ID de categoría inválido"}), 400

    categoria = mongo.db.categorias.find_one({"_id": object_id})
    if not categoria:
        return jsonify({"message": "Categoría no encontrada"}), 404

    data = request.get_json(silent=True) or {}
    if "activo" not in data or not isinstance(data.get("activo"), bool):
        return jsonify({"message": "El campo 'activo' es requerido y debe ser booleano"}), 400

    nuevo_estado = data.get("activo")
    if nuevo_estado and categoria.get("eliminado") is True:
        return jsonify({"message": "No se puede activar una categoría eliminada. Use restaurar."}), 409

    mongo.db.categorias.update_one(
        {"_id": object_id},
        {"$set": {"activo": nuevo_estado, "updatedAt": _utcnow()}},
    )
    updated = mongo.db.categorias.find_one({"_id": object_id})
    return jsonify({"message": "Estado actualizado con éxito", "category": _serialize_category(updated)}), 200


@categories_bp.route("/categorias/<string:category_id>", methods=["DELETE"])
@allow_cors
@token_required
def eliminar_categoria(user, category_id):
    if not _is_super_admin(user):
        return jsonify({"message": "Solo super_admin puede administrar categorías"}), 403

    object_id = _parse_object_id(category_id)
    if not object_id:
        return jsonify({"message": "ID de categoría inválido"}), 400

    categoria = mongo.db.categorias.find_one({"_id": object_id})
    if not categoria:
        return jsonify({"message": "Categoría no encontrada"}), 404

    now = _utcnow()
    mongo.db.categorias.update_one(
        {"_id": object_id},
        {
            "$set": {
                "eliminado": True,
                "activo": False,
                "deletedAt": now,
                "updatedAt": now,
            }
        },
    )
    return jsonify({"message": "Categoría eliminada con éxito"}), 200


@categories_bp.route("/categorias/<string:category_id>/restaurar", methods=["POST"])
@allow_cors
@token_required
def restaurar_categoria(user, category_id):
    if not _is_super_admin(user):
        return jsonify({"message": "Solo super_admin puede administrar categorías"}), 403

    object_id = _parse_object_id(category_id)
    if not object_id:
        return jsonify({"message": "ID de categoría inválido"}), 400

    categoria = mongo.db.categorias.find_one({"_id": object_id})
    if not categoria:
        return jsonify({"message": "Categoría no encontrada"}), 404

    normalized_name = categoria.get("nombre_normalizado") or _category_name_normalized(categoria.get("nombre", ""))
    duplicate_name = mongo.db.categorias.find_one(
        {
            "_id": {"$ne": object_id},
            "nombre_normalizado": normalized_name,
            "eliminado": {"$ne": True},
        },
        {"_id": 1},
    )
    if duplicate_name:
        return jsonify({"message": "No se puede restaurar: ya existe una categoría activa con el mismo nombre"}), 409

    value = categoria.get("value") or _generate_unique_value(categoria.get("nombre", ""), exclude_id=object_id)
    duplicate_value = mongo.db.categorias.find_one(
        {
            "_id": {"$ne": object_id},
            "value": value,
            "eliminado": {"$ne": True},
        },
        {"_id": 1},
    )
    if duplicate_value:
        value = _generate_unique_value(value, exclude_id=object_id)

    mongo.db.categorias.update_one(
        {"_id": object_id},
        {
            "$set": {
                "eliminado": False,
                "activo": True,
                "deletedAt": None,
                "updatedAt": _utcnow(),
                "value": value,
                "nombre_normalizado": normalized_name,
                "label": categoria.get("label") or categoria.get("nombre", ""),
            }
        },
    )
    updated = mongo.db.categorias.find_one({"_id": object_id})
    return jsonify({"message": "Categoría restaurada con éxito", "category": _serialize_category(updated)}), 200
