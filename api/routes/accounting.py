from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bson import ObjectId
from flask import Blueprint, jsonify, request

from api.extensions import mongo
from api.services.accounting_service import (
    AccountCatalogService,
    AccountScopeService,
    SeedService,
    DEFAULT_YEAR,
    AccountingIndexes,
)
from api.util.common import agregar_log
from api.util.decorators import allow_cors, token_required


accounting_bp = Blueprint("accounting", __name__)


def _parse_year() -> int:
    value = request.args.get("year", str(DEFAULT_YEAR))
    try:
        return int(value)
    except Exception:
        return DEFAULT_YEAR


def _is_super_admin(user: Dict[str, Any]) -> bool:
    return user.get("role") == "super_admin"


def _user_department_id(user: Dict[str, Any]) -> Optional[str]:
    dep = user.get("departmentId") or user.get("departamento_id")
    if not dep:
        return None
    return str(dep)


def _forbidden(msg: str = "No autorizado"):
    return jsonify({"message": msg}), 403


def _can_access_department(user: Dict[str, Any], department_id: str) -> bool:
    if _is_super_admin(user):
        return True
    user_dep = _user_department_id(user)
    return bool(user_dep and str(user_dep) == str(department_id))


def _project_member_match(project: Dict[str, Any], user_id: str) -> bool:
    if str(project.get("owner")) == str(user_id):
        return True

    for member in project.get("miembros", []):
        usuario = member.get("usuario") or {}
        user_ref = usuario.get("_id")
        if isinstance(user_ref, dict):
            oid = user_ref.get("$oid")
            if oid and str(oid) == str(user_id):
                return True
        elif user_ref and str(user_ref) == str(user_id):
            return True
    return False


def _can_access_project(user: Dict[str, Any], project_id: str):
    project_obj_id = None
    try:
        project_obj_id = ObjectId(str(project_id))
    except Exception:
        return False, None

    project = mongo.db.proyectos.find_one({"_id": project_obj_id})
    if not project:
        return False, None

    if _is_super_admin(user):
        return True, project

    user_dep = _user_department_id(user)
    project_dep = str(project.get("departamento_id")) if project.get("departamento_id") else None
    if user_dep and project_dep and user_dep == project_dep:
        return True, project

    if _project_member_match(project, user.get("sub")):
        return True, project

    return False, project


def _allow_negative_balances() -> bool:
    return os.getenv("ACCOUNTING_ALLOW_NEGATIVE", "true").strip().lower() in {"1", "true", "yes", "si"}


@accounting_bp.route("/accounts/tree", methods=["GET"])
@accounting_bp.route("/api/accounts/tree", methods=["GET"])
@allow_cors
@token_required
def get_accounts_tree(user):
    year = _parse_year()
    group = request.args.get("group")
    tree = AccountCatalogService.tree(year=year, group=group)
    return jsonify({"year": year, "group": group, "tree": tree}), 200


@accounting_bp.route("/accounts/search", methods=["GET"])
@accounting_bp.route("/api/accounts/search", methods=["GET"])
@allow_cors
@token_required
def search_accounts(user):
    year = _parse_year()
    group = request.args.get("group")
    q = request.args.get("q", "")
    limit = int(request.args.get("limit", "50")) if request.args.get("limit") else 50
    rows = AccountCatalogService.search(year=year, q=q, group=group, limit=limit)
    return jsonify({"year": year, "group": group, "q": q, "results": rows}), 200


@accounting_bp.route("/departments/<string:department_id>/accounts", methods=["GET"])
@accounting_bp.route("/api/departments/<string:department_id>/accounts", methods=["GET"])
@allow_cors
@token_required
def get_department_accounts(user, department_id):
    if not _can_access_department(user, department_id):
        return _forbidden("No autorizado para consultar este departamento")

    year = _parse_year()
    group = request.args.get("group")
    payload = AccountScopeService.get_scope_accounts(year=year, scope_type="department", scope_id=department_id, group=group)
    return jsonify(payload), 200


@accounting_bp.route("/departments/<string:department_id>/accounts/init", methods=["POST"])
@accounting_bp.route("/api/departments/<string:department_id>/accounts/init", methods=["POST"])
@allow_cors
@token_required
def init_department_accounts(user, department_id):
    if not _can_access_department(user, department_id):
        return _forbidden("No autorizado para inicializar cuentas de este departamento")

    year = _parse_year()
    mode = request.args.get("mode", "detail_only")
    try:
        result = AccountScopeService.init_scope(year=year, scope_type="department", scope_id=department_id, mode=mode)
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    return jsonify(result), 200


@accounting_bp.route("/departments/<string:department_id>/movements", methods=["POST"])
@accounting_bp.route("/api/departments/<string:department_id>/movements", methods=["POST"])
@allow_cors
@token_required
def post_department_movement(user, department_id):
    if not _can_access_department(user, department_id):
        return _forbidden("No autorizado para registrar movimientos en este departamento")

    data = request.get_json(silent=True) or {}
    account_code = data.get("accountCode")
    movement_type = data.get("type")
    amount = data.get("amount")

    if not account_code or movement_type is None or amount is None:
        return jsonify({"message": "Campos requeridos: accountCode, type, amount"}), 400

    try:
        amount_value = float(amount)
        result = AccountScopeService.create_movement(
            year=_parse_year(),
            scope_type="department",
            scope_id=department_id,
            account_code=str(account_code),
            movement_type=str(movement_type),
            amount=amount_value,
            description=str(data.get("description", "")),
            reference=data.get("reference") if isinstance(data.get("reference"), dict) else {},
            created_by=str(user.get("sub")),
            allow_negative=_allow_negative_balances(),
        )
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400

    return jsonify(result), 201


@accounting_bp.route("/projects/<string:project_id>/accounts", methods=["GET"])
@accounting_bp.route("/api/projects/<string:project_id>/accounts", methods=["GET"])
@allow_cors
@token_required
def get_project_accounts(user, project_id):
    allowed, project = _can_access_project(user, project_id)
    if not allowed:
        return _forbidden("No autorizado para consultar este proyecto")

    year = _parse_year()
    group = request.args.get("group")
    payload = AccountScopeService.get_scope_accounts(year=year, scope_type="project", scope_id=str(project_id), group=group)
    return jsonify(payload), 200


@accounting_bp.route("/projects/<string:project_id>/accounts/init", methods=["POST"])
@accounting_bp.route("/api/projects/<string:project_id>/accounts/init", methods=["POST"])
@allow_cors
@token_required
def init_project_accounts(user, project_id):
    allowed, project = _can_access_project(user, project_id)
    if not allowed:
        return _forbidden("No autorizado para inicializar cuentas de este proyecto")

    year = _parse_year()
    mode = request.args.get("mode", "detail_only")
    try:
        result = AccountScopeService.init_scope(year=year, scope_type="project", scope_id=project_id, mode=mode)
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    return jsonify(result), 200


@accounting_bp.route("/projects/<string:project_id>/movements", methods=["POST"])
@accounting_bp.route("/api/projects/<string:project_id>/movements", methods=["POST"])
@allow_cors
@token_required
def post_project_movement(user, project_id):
    allowed, project = _can_access_project(user, project_id)
    if not allowed:
        return _forbidden("No autorizado para registrar movimientos en este proyecto")

    data = request.get_json(silent=True) or {}
    account_code = data.get("accountCode")
    movement_type = data.get("type")
    amount = data.get("amount")

    if not account_code or movement_type is None or amount is None:
        return jsonify({"message": "Campos requeridos: accountCode, type, amount"}), 400

    try:
        amount_value = float(amount)
        result = AccountScopeService.create_movement(
            year=_parse_year(),
            scope_type="project",
            scope_id=str(project_id),
            account_code=str(account_code),
            movement_type=str(movement_type),
            amount=amount_value,
            description=str(data.get("description", "")),
            reference=data.get("reference") if isinstance(data.get("reference"), dict) else {},
            created_by=str(user.get("sub")),
            allow_negative=_allow_negative_balances(),
        )

        try:
            agregar_log(project["_id"], f"{user.get('nombre', 'Usuario')} registró movimiento contable {movement_type} {amount_value} en cuenta {account_code}")
        except Exception:
            pass
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400

    return jsonify(result), 201


@accounting_bp.route("/admin/seed/contabilidad/2025", methods=["POST"])
@accounting_bp.route("/api/admin/seed/contabilidad/2025", methods=["POST"])
@allow_cors
@token_required
def seed_contabilidad_2025(user):
    if not _is_super_admin(user):
        return _forbidden("Solo super_admin puede ejecutar el seed")

    force = str(request.args.get("force", "false")).lower() in {"1", "true", "yes", "si"}
    dry_run = str(request.args.get("dry_run", "false")).lower() in {"1", "true", "yes", "si"}
    service = SeedService()
    result = service.seed(year=2025, force=force, dry_run=dry_run)
    return jsonify(result), 200


@accounting_bp.route("/admin/sync/departments-from-units", methods=["POST"])
@accounting_bp.route("/api/admin/sync/departments-from-units", methods=["POST"])
@allow_cors
@token_required
def sync_departments_from_units(user):
    if not _is_super_admin(user):
        return _forbidden("Solo super_admin puede sincronizar departamentos")

    year = _parse_year()
    service = SeedService()
    result = service.sync_departments_from_units(year=year)
    return jsonify({"year": year, **result}), 200


@accounting_bp.route("/admin/contabilidad/consolidado", methods=["GET"])
@accounting_bp.route("/api/admin/contabilidad/consolidado", methods=["GET"])
@allow_cors
@token_required
def consolidated_accounting(user):
    if not _is_super_admin(user):
        return _forbidden("Solo super_admin puede consultar la consolidación global")

    year = _parse_year()
    scope_type = request.args.get("scopeType")
    scope_id = request.args.get("scopeId")

    if scope_type and scope_type not in {"department", "project"}:
        return jsonify({"message": "scopeType debe ser department o project"}), 400

    data = AccountCatalogService.consolidated_totals(year=year, scope_type=scope_type, scope_id=scope_id)
    return jsonify(data), 200


@accounting_bp.route("/admin/accounts", methods=["GET"])
@accounting_bp.route("/api/admin/accounts", methods=["GET"])
@allow_cors
@token_required
def admin_list_accounts(user):
    if not _is_super_admin(user):
        return _forbidden("Solo super_admin puede listar cuentas del catalogo")

    AccountingIndexes.ensure_indexes()
    year = _parse_year()
    q = request.args.get("q", "").strip()
    group = request.args.get("group", "").strip().upper()
    scope_type = request.args.get("scopeType", "").strip()
    scope_id = request.args.get("scopeId", "").strip()
    page = int(request.args.get("page", "0"))
    limit = int(request.args.get("limit", "20"))
    skip = page * limit

    query = {"year": year}
    if group:
        query["group"] = group
    if q:
        query["$or"] = [{"code": {"$regex": q}}, {"description": {"$regex": q, "$options": "i"}}]

    total = mongo.db.master_accounts.count_documents(query)
    rows = list(
        mongo.db.master_accounts.find(query, {"_id": 0}).sort("code", 1).skip(skip).limit(limit)
    )

    if not rows:
        return jsonify({"request_list": [], "count": total}), 200

    account_codes = [row["code"] for row in rows]
    state_match = {"year": year, "accountCode": {"$in": account_codes}}
    if scope_type in {"department", "project", "global"}:
        state_match["scopeType"] = scope_type
        if scope_id:
            state_match["scopeId"] = scope_id

    balances = list(
        mongo.db.account_scope_state.aggregate(
            [
                {"$match": state_match},
                {"$group": {"_id": "$accountCode", "balance": {"$sum": "$balance"}}},
            ]
        )
    )
    balance_by_code = {row["_id"]: float(row.get("balance", 0)) for row in balances}

    response_rows = []
    for row in rows:
        row_copy = dict(row)
        row_copy["balance"] = balance_by_code.get(row["code"], 0.0)
        response_rows.append(row_copy)

    return jsonify({"request_list": response_rows, "count": total}), 200


@accounting_bp.route("/admin/accounts", methods=["POST"])
@accounting_bp.route("/api/admin/accounts", methods=["POST"])
@allow_cors
@token_required
def admin_create_account(user):
    if not _is_super_admin(user):
        return _forbidden("Solo super_admin puede crear cuentas del catalogo")

    AccountingIndexes.ensure_indexes()
    data = request.get_json(silent=True) or {}
    year = int(data.get("year", _parse_year()))
    code = str(data.get("code", "")).strip()
    description = str(data.get("description", "")).strip()
    group = str(data.get("group", "")).strip().upper()
    parent_code = str(data.get("parent_code", "")).strip() or None
    level = int(data.get("level", 1))
    is_header = bool(data.get("is_header", False))

    if not code or len(code) != 12 or not code.isdigit():
        return jsonify({"message": "code debe ser string numérico de 12 dígitos"}), 400
    if not description:
        return jsonify({"message": "description es requerido"}), 400
    if group not in {"PASIVO", "INGRESO", "EGRESO"}:
        return jsonify({"message": "group debe ser PASIVO, INGRESO o EGRESO"}), 400
    if parent_code and (len(parent_code) != 12 or not parent_code.isdigit()):
        return jsonify({"message": "parent_code debe ser string numérico de 12 dígitos"}), 400

    existing = mongo.db.master_accounts.find_one({"year": year, "code": code})
    if existing:
        return jsonify({"message": "Ya existe una cuenta con ese código para el año"}), 409

    now = datetime.now(timezone.utc)
    mongo.db.master_accounts.insert_one(
        {
            "year": year,
            "code": code,
            "description": description,
            "group": group,
            "is_header": is_header,
            "level": level,
            "parent_code": parent_code,
            "createdAt": now,
            "updatedAt": now,
        }
    )
    return jsonify({"message": "Cuenta creada", "code": code, "year": year}), 201


@accounting_bp.route("/admin/accounts/<string:code>", methods=["PUT"])
@accounting_bp.route("/api/admin/accounts/<string:code>", methods=["PUT"])
@allow_cors
@token_required
def admin_update_account(user, code):
    if not _is_super_admin(user):
        return _forbidden("Solo super_admin puede editar cuentas del catalogo")

    AccountingIndexes.ensure_indexes()
    year = _parse_year()
    data = request.get_json(silent=True) or {}
    update_fields = {}
    allowed = {"description", "group", "is_header", "level", "parent_code"}
    for key in allowed:
        if key in data:
            update_fields[key] = data[key]

    if "group" in update_fields:
        group = str(update_fields["group"]).upper()
        if group not in {"PASIVO", "INGRESO", "EGRESO"}:
            return jsonify({"message": "group debe ser PASIVO, INGRESO o EGRESO"}), 400
        update_fields["group"] = group

    if "parent_code" in update_fields:
        parent_code = str(update_fields["parent_code"]).strip()
        if parent_code and (len(parent_code) != 12 or not parent_code.isdigit()):
            return jsonify({"message": "parent_code debe ser string numérico de 12 dígitos"}), 400
        update_fields["parent_code"] = parent_code or None

    if not update_fields:
        return jsonify({"message": "No hay campos para actualizar"}), 400

    update_fields["updatedAt"] = datetime.now(timezone.utc)
    result = mongo.db.master_accounts.update_one({"year": year, "code": code}, {"$set": update_fields})
    if result.matched_count == 0:
        return jsonify({"message": "Cuenta no encontrada"}), 404
    return jsonify({"message": "Cuenta actualizada", "code": code, "year": year}), 200


@accounting_bp.route("/admin/accounts/<string:code>", methods=["DELETE"])
@accounting_bp.route("/api/admin/accounts/<string:code>", methods=["DELETE"])
@allow_cors
@token_required
def admin_delete_account(user, code):
    if not _is_super_admin(user):
        return _forbidden("Solo super_admin puede eliminar cuentas del catalogo")

    AccountingIndexes.ensure_indexes()
    year = _parse_year()
    has_children = mongo.db.master_accounts.count_documents({"year": year, "parent_code": code}) > 0
    if has_children:
        return jsonify({"message": "No se puede eliminar una cuenta con hijos"}), 409

    usage_count = mongo.db.ledger_movements.count_documents({"year": year, "accountCode": code})
    if usage_count > 0:
        return jsonify({"message": "No se puede eliminar una cuenta con movimientos asociados"}), 409

    mongo.db.account_scope_state.delete_many({"year": year, "accountCode": code})
    result = mongo.db.master_accounts.delete_one({"year": year, "code": code})
    if result.deleted_count == 0:
        return jsonify({"message": "Cuenta no encontrada"}), 404
    return jsonify({"message": "Cuenta eliminada", "code": code, "year": year}), 200


@accounting_bp.route("/admin/accounts/transfer", methods=["POST"])
@accounting_bp.route("/api/admin/accounts/transfer", methods=["POST"])
@allow_cors
@token_required
def admin_transfer_between_accounts(user):
    if not _is_super_admin(user):
        return _forbidden("Solo super_admin puede transferir entre cuentas")

    data = request.get_json(silent=True) or {}
    year = int(data.get("year", _parse_year()))
    legacy_scope_type = str(data.get("scopeType", "")).strip()
    legacy_scope_id = str(data.get("scopeId", "")).strip()
    from_scope_type = str(data.get("fromScopeType", legacy_scope_type)).strip()
    to_scope_type = str(data.get("toScopeType", legacy_scope_type)).strip()
    from_scope_id = str(data.get("fromScopeId", legacy_scope_id)).strip()
    to_scope_id = str(data.get("toScopeId", legacy_scope_id)).strip()
    from_account_code = str(data.get("fromAccountCode", "")).strip()
    to_account_code = str(data.get("toAccountCode", "")).strip()
    from_account_description = str(data.get("fromAccountDescription", "")).strip()
    to_account_description = str(data.get("toAccountDescription", "")).strip()
    amount = data.get("amount")

    valid_scopes = {"department", "project", "global"}
    if from_scope_type not in valid_scopes or to_scope_type not in valid_scopes:
        return jsonify({"message": "fromScopeType y toScopeType deben ser department, project o global"}), 400
    if from_scope_type == "global" and not from_scope_id:
        from_scope_id = "global"
    if to_scope_type == "global" and not to_scope_id:
        to_scope_id = "global"
    if not from_scope_id or not to_scope_id:
        return jsonify({"message": "fromScopeId y toScopeId son requeridos"}), 400
    if not from_account_code or not to_account_code:
        return jsonify({"message": "fromAccountCode y toAccountCode son requeridos"}), 400
    if amount is None:
        return jsonify({"message": "amount es requerido"}), 400

    try:
        amount_value = float(amount)
        reference_payload = data.get("reference") if isinstance(data.get("reference"), dict) else {}
        if from_account_description:
            reference_payload["fromAccountDescription"] = from_account_description
        if to_account_description:
            reference_payload["toAccountDescription"] = to_account_description

        result = AccountScopeService.transfer_between_accounts(
            year=year,
            scope_type=legacy_scope_type or from_scope_type,
            scope_id=legacy_scope_id or from_scope_id,
            from_scope_type=from_scope_type,
            from_scope_id=from_scope_id,
            to_scope_type=to_scope_type,
            to_scope_id=to_scope_id,
            from_account_code=from_account_code,
            to_account_code=to_account_code,
            amount=amount_value,
            description=str(data.get("description", "")).strip(),
            reference=reference_payload,
            created_by=str(user.get("sub")),
            allow_negative=_allow_negative_balances(),
        )
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400

    if from_account_description:
        result["fromAccountDescription"] = from_account_description
    if to_account_description:
        result["toAccountDescription"] = to_account_description

    return jsonify(result), 201


@accounting_bp.route("/admin/accounts/movements", methods=["POST"])
@accounting_bp.route("/api/admin/accounts/movements", methods=["POST"])
@allow_cors
@token_required
def admin_create_movement(user):
    if not _is_super_admin(user):
        return _forbidden("Solo super_admin puede registrar movimientos globales")

    data = request.get_json(silent=True) or {}
    year = int(data.get("year", _parse_year()))
    scope_type = str(data.get("scopeType", "")).strip()
    scope_id = str(data.get("scopeId", "")).strip()
    account_code = str(data.get("accountCode", "")).strip()
    movement_type = str(data.get("type", "")).strip().lower()
    amount = data.get("amount")

    if scope_type not in {"department", "project", "global"}:
        return jsonify({"message": "scopeType debe ser department, project o global"}), 400
    if not scope_id:
        if scope_type == "global":
            scope_id = "global"
        else:
            return jsonify({"message": "scopeId es requerido"}), 400
    if not account_code or amount is None or movement_type not in {"debit", "credit"}:
        return jsonify({"message": "Campos requeridos: accountCode, type, amount"}), 400

    try:
        amount_value = float(amount)
        result = AccountScopeService.create_movement(
            year=year,
            scope_type=scope_type,
            scope_id=scope_id,
            account_code=account_code,
            movement_type=movement_type,
            amount=amount_value,
            description=str(data.get("description", "")).strip(),
            reference=data.get("reference") if isinstance(data.get("reference"), dict) else {},
            created_by=str(user.get("sub")),
            allow_negative=_allow_negative_balances(),
        )
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400

    return jsonify(result), 201
