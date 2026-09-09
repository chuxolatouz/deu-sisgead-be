import re
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from pymongo.errors import DuplicateKeyError

from api.extensions import mongo
from api.services.requirement_service import (
    RequirementCatalogService,
    _now_utc,
    _parse_object_id,
)
from api.util.decorators import allow_cors, token_required


requirements_bp = Blueprint("requirements", __name__)


def _is_super_admin(user):
    return user.get("role") == "super_admin"


def _parse_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "si", "on"}


def _parse_year(value):
    try:
        return int(value or datetime.now(timezone.utc).year)
    except (TypeError, ValueError):
        return None


def _usage_counts(requirement_id):
    reference = str(requirement_id)
    project_count = mongo.db.proyectos.count_documents(
        {"requerimientos.requirementId": reference}
    )
    activity_count = mongo.db.documentos.count_documents(
        {"requerimientos.requirementId": reference}
    )
    return project_count, activity_count


def _account_from_payload(data):
    account_code = str(data.get("accountCode") or data.get("cuenta_contable") or "").strip()
    year = _parse_year(data.get("accountReferenceYear") or data.get("year"))
    if not account_code:
        return None, None, "La cuenta contable es requerida"
    if year is None:
        return None, None, "El año contable es inválido"
    account = RequirementCatalogService.find_account(year, account_code)
    if not account:
        return None, None, "La cuenta contable no existe para el año indicado"
    if str(account.get("group") or "").upper() != "EGRESO":
        return None, None, "El requerimiento debe asociarse a una cuenta de egreso"
    return account, year, None


@requirements_bp.route("/requerimientos", methods=["GET"])
@allow_cors
@token_required
def listar_requerimientos(user):
    RequirementCatalogService.ensure_indexes()
    search_text = str(request.args.get("text") or "").strip()
    active_only = _parse_bool(request.args.get("activeOnly"), default=False)
    include_inactive = _parse_bool(request.args.get("includeInactive"), default=True)
    include_deleted = _parse_bool(request.args.get("includeDeleted"), default=False)
    include_stats = _parse_bool(request.args.get("includeStats"), default=False)

    query = {}
    if search_text:
        query["$or"] = [
            {"nombre": {"$regex": re.escape(search_text), "$options": "i"}},
            {"descripcion": {"$regex": re.escape(search_text), "$options": "i"}},
            {"accountCode": {"$regex": re.escape(search_text)}},
        ]
    if active_only:
        query["activo"] = {"$ne": False}
        query["eliminado"] = {"$ne": True}
    else:
        if not include_inactive:
            query["activo"] = {"$ne": False}
        if not include_deleted:
            query["eliminado"] = {"$ne": True}

    payload = []
    for requirement in RequirementCatalogService.collection().find(query).sort("nombre", 1):
        project_count, activity_count = (0, 0)
        if include_stats:
            project_count, activity_count = _usage_counts(requirement.get("_id"))
        payload.append(
            RequirementCatalogService.serialize(
                requirement,
                project_count=project_count,
                activity_count=activity_count,
            )
        )
    return jsonify(payload), 200


@requirements_bp.route("/requerimientos/<string:requirement_id>", methods=["GET"])
@allow_cors
@token_required
def obtener_requerimiento(user, requirement_id):
    object_id = _parse_object_id(requirement_id)
    if not object_id:
        return jsonify({"message": "ID de requerimiento inválido"}), 400
    requirement = RequirementCatalogService.collection().find_one({"_id": object_id})
    if not requirement:
        return jsonify({"message": "Requerimiento no encontrado"}), 404
    project_count, activity_count = _usage_counts(object_id)
    return jsonify(
        RequirementCatalogService.serialize(
            requirement,
            project_count=project_count,
            activity_count=activity_count,
        )
    ), 200


@requirements_bp.route("/requerimientos", methods=["POST"])
@allow_cors
@token_required
def crear_requerimiento(user):
    if not _is_super_admin(user):
        return jsonify({"message": "Solo super_admin puede administrar requerimientos"}), 403

    RequirementCatalogService.ensure_indexes()
    data = request.get_json(silent=True) or {}
    nombre = str(data.get("nombre") or "").strip()
    if not nombre:
        return jsonify({"message": "El nombre es requerido"}), 400

    normalized_name = RequirementCatalogService.normalize_name(nombre)
    if RequirementCatalogService.collection().find_one(
        {"nombre_normalizado": normalized_name, "eliminado": {"$ne": True}}
    ):
        return jsonify({"message": "Ya existe un requerimiento con ese nombre"}), 409

    account, year, account_error = _account_from_payload(data)
    if account_error:
        return jsonify({"message": account_error}), 400

    now = _now_utc()
    requirement = {
        "nombre": nombre,
        "nombre_normalizado": normalized_name,
        "descripcion": str(data.get("descripcion") or "").strip(),
        "accountCode": account["code"],
        "account": RequirementCatalogService.account_snapshot(account),
        "accountReferenceYear": year,
        "activo": True,
        "eliminado": False,
        "createdAt": now,
        "updatedAt": now,
        "deletedAt": None,
    }
    try:
        result = RequirementCatalogService.collection().insert_one(requirement)
    except DuplicateKeyError:
        return jsonify({"message": "Ya existe un requerimiento con ese nombre"}), 409
    requirement["_id"] = result.inserted_id
    return jsonify({
        "message": "Requerimiento creado con éxito",
        "_id": str(result.inserted_id),
        "requirement": RequirementCatalogService.serialize(requirement),
    }), 201


@requirements_bp.route("/requerimientos/<string:requirement_id>", methods=["PUT"])
@allow_cors
@token_required
def actualizar_requerimiento(user, requirement_id):
    if not _is_super_admin(user):
        return jsonify({"message": "Solo super_admin puede administrar requerimientos"}), 403
    object_id = _parse_object_id(requirement_id)
    if not object_id:
        return jsonify({"message": "ID de requerimiento inválido"}), 400
    current = RequirementCatalogService.collection().find_one({"_id": object_id})
    if not current:
        return jsonify({"message": "Requerimiento no encontrado"}), 404

    data = request.get_json(silent=True) or {}
    updates = {}
    if "nombre" in data:
        nombre = str(data.get("nombre") or "").strip()
        if not nombre:
            return jsonify({"message": "El nombre es requerido"}), 400
        normalized_name = RequirementCatalogService.normalize_name(nombre)
        if RequirementCatalogService.collection().find_one({
            "_id": {"$ne": object_id},
            "nombre_normalizado": normalized_name,
            "eliminado": {"$ne": True},
        }):
            return jsonify({"message": "Ya existe un requerimiento con ese nombre"}), 409
        updates.update({"nombre": nombre, "nombre_normalizado": normalized_name})
    if "descripcion" in data:
        updates["descripcion"] = str(data.get("descripcion") or "").strip()
    if any(key in data for key in ("accountCode", "cuenta_contable", "accountReferenceYear", "year")):
        merged = {
            "accountCode": data.get("accountCode") or data.get("cuenta_contable") or current.get("accountCode"),
            "accountReferenceYear": data.get("accountReferenceYear") or data.get("year") or current.get("accountReferenceYear"),
        }
        account, year, account_error = _account_from_payload(merged)
        if account_error:
            return jsonify({"message": account_error}), 400
        updates.update({
            "accountCode": account["code"],
            "account": RequirementCatalogService.account_snapshot(account),
            "accountReferenceYear": year,
        })
    if not updates:
        return jsonify({"message": "No hay campos para actualizar"}), 400
    updates["updatedAt"] = _now_utc()
    RequirementCatalogService.collection().update_one({"_id": object_id}, {"$set": updates})
    updated = RequirementCatalogService.collection().find_one({"_id": object_id})
    return jsonify({
        "message": "Requerimiento actualizado con éxito",
        "requirement": RequirementCatalogService.serialize(updated),
    }), 200


@requirements_bp.route("/requerimientos/<string:requirement_id>/estado", methods=["PATCH"])
@allow_cors
@token_required
def cambiar_estado_requerimiento(user, requirement_id):
    if not _is_super_admin(user):
        return jsonify({"message": "Solo super_admin puede administrar requerimientos"}), 403
    object_id = _parse_object_id(requirement_id)
    requirement = RequirementCatalogService.collection().find_one({"_id": object_id}) if object_id else None
    if not requirement:
        return jsonify({"message": "Requerimiento no encontrado"}), 404
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("activo"), bool):
        return jsonify({"message": "El campo activo debe ser booleano"}), 400
    if data["activo"] and requirement.get("eliminado") is True:
        return jsonify({"message": "Restaura el requerimiento antes de activarlo"}), 409
    RequirementCatalogService.collection().update_one(
        {"_id": object_id},
        {"$set": {"activo": data["activo"], "updatedAt": _now_utc()}},
    )
    updated = RequirementCatalogService.collection().find_one({"_id": object_id})
    return jsonify({
        "message": "Estado actualizado con éxito",
        "requirement": RequirementCatalogService.serialize(updated),
    }), 200


@requirements_bp.route("/requerimientos/<string:requirement_id>", methods=["DELETE"])
@allow_cors
@token_required
def eliminar_requerimiento(user, requirement_id):
    if not _is_super_admin(user):
        return jsonify({"message": "Solo super_admin puede administrar requerimientos"}), 403
    object_id = _parse_object_id(requirement_id)
    if not object_id or not RequirementCatalogService.collection().find_one({"_id": object_id}):
        return jsonify({"message": "Requerimiento no encontrado"}), 404
    now = _now_utc()
    RequirementCatalogService.collection().update_one(
        {"_id": object_id},
        {"$set": {"eliminado": True, "activo": False, "deletedAt": now, "updatedAt": now}},
    )
    return jsonify({"message": "Requerimiento eliminado con éxito"}), 200


@requirements_bp.route("/requerimientos/<string:requirement_id>/restaurar", methods=["POST"])
@allow_cors
@token_required
def restaurar_requerimiento(user, requirement_id):
    if not _is_super_admin(user):
        return jsonify({"message": "Solo super_admin puede administrar requerimientos"}), 403
    object_id = _parse_object_id(requirement_id)
    requirement = RequirementCatalogService.collection().find_one({"_id": object_id}) if object_id else None
    if not requirement:
        return jsonify({"message": "Requerimiento no encontrado"}), 404
    duplicate = RequirementCatalogService.collection().find_one({
        "_id": {"$ne": object_id},
        "nombre_normalizado": requirement.get("nombre_normalizado"),
        "eliminado": {"$ne": True},
    })
    if duplicate:
        return jsonify({"message": "Ya existe otro requerimiento con ese nombre"}), 409
    RequirementCatalogService.collection().update_one(
        {"_id": object_id},
        {"$set": {"eliminado": False, "activo": True, "deletedAt": None, "updatedAt": _now_utc()}},
    )
    updated = RequirementCatalogService.collection().find_one({"_id": object_id})
    return jsonify({
        "message": "Requerimiento restaurado con éxito",
        "requirement": RequirementCatalogService.serialize(updated),
    }), 200
