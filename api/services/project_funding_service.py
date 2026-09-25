from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
import uuid

from bson import ObjectId
from pymongo import ReturnDocument

from api.extensions import mongo
from api.services.accounting_service import (
    AccountingIndexes,
    AccountScopeService,
    DEFAULT_YEAR,
)
from api.util.common import agregar_log
from api.util.utils import actualizar_pasos


PROJECT_FUNDING_VERSION = 3


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _amount_to_cents(amount: Any) -> int:
    return int(round(float(amount or 0) * 100))


def _cents_to_units(amount: Any) -> float:
    return round(float(amount or 0) / 100, 2)


def _to_object_id(value: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(value).strip())
    except Exception:
        return None


def _sort_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _build_default_model() -> Dict[str, Any]:
    return {
        "version": PROJECT_FUNDING_VERSION,
        "status": "pooled",
        "configuredAt": None,
        "migratedAt": None,
        "migratedBy": None,
        "initialAssignedAmount": 0,
        "legacyCurrentBalanceSnapshot": None,
        "legacyInitialBalanceSnapshot": None,
        "migrationNote": None,
    }


class ProjectFundingService:
    @staticmethod
    def ensure_model(project: Dict[str, Any], persist: bool = False) -> Dict[str, Any]:
        model = deepcopy(project.get("fundingModel") or {})
        changed = False
        project_id = project.get("_id")

        if not model:
            has_legacy_balance = int(project.get("balance", 0) or 0) != 0 or int(project.get("balance_inicial", 0) or 0) != 0
            if has_legacy_balance:
                model = {
                    **_build_default_model(),
                    "status": "legacy",
                    "legacyCurrentBalanceSnapshot": int(project.get("balance", 0) or 0),
                    "legacyInitialBalanceSnapshot": int(project.get("balance_inicial", 0) or 0),
                }
            else:
                model = _build_default_model()
                if project_id:
                    model["version"] = 2
                    model["status"] = "active"
            changed = True

        for key, value in _build_default_model().items():
            if key not in model:
                # Existing models without a version are segmented models, not
                # new pooled projects. Keep that distinction for migration.
                model[key] = 2 if key == "version" and project_id else value
                changed = True

        if model.get("status") == "legacy":
            current_snapshot = int(project.get("balance", 0) or 0)
            initial_snapshot = int(project.get("balance_inicial", 0) or 0)
            if model.get("legacyCurrentBalanceSnapshot") is None:
                model["legacyCurrentBalanceSnapshot"] = current_snapshot
                changed = True
            if model.get("legacyInitialBalanceSnapshot") is None:
                model["legacyInitialBalanceSnapshot"] = initial_snapshot
                changed = True

        project["fundingModel"] = model

        if persist and changed and project_id:
            mongo.db.proyectos.update_one({"_id": project_id}, {"$set": {"fundingModel": model}})

        return model

    @staticmethod
    def _pool_state_filter(project_id: Any, year: int) -> Dict[str, Any]:
        return {"year": int(year), "projectId": str(project_id)}

    @staticmethod
    def _empty_pool_state(project_id: Any, year: int) -> Dict[str, Any]:
        now = _now_utc()
        return {
            "year": int(year),
            "projectId": str(project_id),
            "currency": "VES",
            "initialFundedCents": 0,
            "totalFundedCents": 0,
            "liquidatedCents": 0,
            "availableCents": 0,
            "movementsCount": 0,
            "createdAt": now,
            "updatedAt": now,
            "lastMovementAt": None,
        }

    @staticmethod
    def get_pool_state(project_id: Any, year: int = DEFAULT_YEAR, *, create: bool = False) -> Dict[str, Any]:
        AccountingIndexes.ensure_indexes()
        state_filter = ProjectFundingService._pool_state_filter(project_id, year)
        state = mongo.db.project_fund_state.find_one(state_filter, {"_id": 0})
        if state or not create:
            return state or ProjectFundingService._empty_pool_state(project_id, year)

        empty_state = ProjectFundingService._empty_pool_state(project_id, year)
        mongo.db.project_fund_state.update_one(
            state_filter,
            {"$setOnInsert": empty_state},
            upsert=True,
        )
        return mongo.db.project_fund_state.find_one(state_filter, {"_id": 0}) or empty_state

    @staticmethod
    def _pool_totals(project: Dict[str, Any], year: int) -> Dict[str, Any]:
        state = ProjectFundingService.get_pool_state(project.get("_id"), year, create=False)
        last_movement = state.get("lastMovementAt")
        sources = list(
            mongo.db.project_fund_movements.find(
                {
                    "year": int(year),
                    "projectId": str(project.get("_id")),
                    "type": "funding",
                },
                {"sourceAccountCode": 1},
            )
        )
        source_codes = {
            str(item.get("sourceAccountCode") or "").strip()
            for item in sources
            if item.get("sourceAccountCode")
        }
        return {
            "currentAvailable": _cents_to_units(state.get("availableCents")),
            "initialAssigned": _cents_to_units(state.get("totalFundedCents")),
            "totalFunded": _cents_to_units(state.get("totalFundedCents")),
            "totalLiquidated": _cents_to_units(state.get("liquidatedCents")),
            "fundingSourcesCount": len(source_codes),
            "fundedAccountsCount": 0,
            "lastMovementAt": last_movement,
        }

    @staticmethod
    def get_project_detail_states(project_id: str, year: int = DEFAULT_YEAR) -> List[Dict[str, Any]]:
        AccountingIndexes.ensure_indexes()
        states = list(
            mongo.db.account_scope_state.find(
                {"year": int(year), "scopeType": "project", "scopeId": str(project_id)},
                {"_id": 0, "accountCode": 1, "balance": 1, "movementsCount": 1, "lastMovementAt": 1},
            )
        )
        if not states:
            return []

        codes = [state["accountCode"] for state in states]
        accounts = list(
            mongo.db.master_accounts.find(
                {"year": int(year), "code": {"$in": codes}},
                {"_id": 0, "code": 1, "description": 1, "group": 1, "is_header": 1, "level": 1, "parent_code": 1},
            )
        )
        account_by_code = {item["code"]: item for item in accounts}

        rows = []
        for state in states:
            account = account_by_code.get(state["accountCode"])
            if not account or account.get("is_header"):
                continue
            rows.append({**account, **state})
        return rows

    @staticmethod
    def _historical_initial_assigned(project: Dict[str, Any], year: int = DEFAULT_YEAR) -> float:
        project_object_id = project.get("_id")
        project_id = str(project_object_id) if project_object_id else ""
        if not project_id:
            return 0.0

        ledger_total = 0.0
        ledger_rows = list(
            mongo.db.ledger_movements.find(
                {
                    "year": int(year),
                    "scopeType": "project",
                    "scopeId": project_id,
                    "type": "debit",
                },
                {"amount": 1, "reference": 1},
            )
        )
        for row in ledger_rows:
            reference = row.get("reference") or {}
            funding_type = _clean_str(reference.get("fundingType")).lower()
            kind = _clean_str(reference.get("kind")).lower()
            if funding_type in {"funding", "migration"} or kind == "transfer":
                ledger_total += float(row.get("amount", 0) or 0)

        if ledger_total > 0:
            return round(ledger_total, 2)

        legacy_total = 0.0
        legacy_actions = list(
            mongo.db.acciones.find(
                {
                    "$or": [
                        {"project_id": project_object_id},
                        {"project_id": project_id},
                        {"proyecto_id": project_object_id},
                        {"proyecto_id": project_id},
                    ]
                },
                {"amount": 1, "type": 1},
            )
        )
        for row in legacy_actions:
            action_type = _clean_str(row.get("type")).lower()
            if action_type == "fondeo":
                legacy_total += _cents_to_units(row.get("amount", 0))

        return round(legacy_total, 2)

    @staticmethod
    def _derived_totals(project: Dict[str, Any], year: int = DEFAULT_YEAR) -> Dict[str, Any]:
        ProjectFundingService.ensure_model(project, persist=True)
        project_id = str(project.get("_id"))
        model = project["fundingModel"]
        if int(model.get("version") or 0) >= PROJECT_FUNDING_VERSION and model.get("status") == "pooled":
            return ProjectFundingService._pool_totals(project, year)

        rows = ProjectFundingService.get_project_detail_states(project_id, year=year)

        if model.get("status") in {"legacy", "pending_migration"} and not rows:
            current_available = _cents_to_units(model.get("legacyCurrentBalanceSnapshot"))
            initial_assigned = _cents_to_units(
                model.get("initialAssignedAmount") or model.get("legacyInitialBalanceSnapshot")
            )
            funded_accounts_count = 0
            last_movement_at = None
        else:
            current_available = round(sum(float(item.get("balance", 0) or 0) for item in rows), 2)
            initial_assigned = _cents_to_units(model.get("initialAssignedAmount"))
            if initial_assigned <= 0:
                initial_assigned = ProjectFundingService._historical_initial_assigned(project, year=year)
                # Backward compatibility for rows without persisted initial amount or movement history.
                if initial_assigned <= 0 and current_available > 0:
                    initial_assigned = current_available
            funded_accounts_count = sum(1 for item in rows if float(item.get("balance", 0) or 0) > 0)
            last_movement_at = None
            dates = [item.get("lastMovementAt") for item in rows if item.get("lastMovementAt")]
            if dates:
                last_movement_at = max(dates, key=_sort_datetime)

        return {
            "currentAvailable": current_available,
            "initialAssigned": initial_assigned,
            "totalFunded": initial_assigned,
            "totalLiquidated": max(round(initial_assigned - current_available, 2), 0),
            "fundingSourcesCount": 0,
            "fundedAccountsCount": funded_accounts_count,
            "lastMovementAt": last_movement_at,
        }

    @staticmethod
    def permissions_for_user(project: Dict[str, Any], user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not user:
            return {"canFund": False, "allowedSources": [], "reason": "No autenticado"}

        role = user.get("role")
        department_id = str(project.get("departamento_id")) if project.get("departamento_id") else ""
        user_department_id = str(user.get("departmentId") or user.get("departamento_id") or "")

        if role == "super_admin":
            allowed_sources = ["department"]
            if department_id:
                allowed_sources.append("global")
            elif "global" not in allowed_sources:
                allowed_sources = ["global"]
            return {"canFund": True, "allowedSources": allowed_sources, "reason": ""}

        if role == "admin_departamento" and department_id and user_department_id == department_id:
            return {"canFund": True, "allowedSources": ["department"], "reason": ""}

        return {
            "canFund": False,
            "allowedSources": [],
            "reason": "Solo super admin o el administrador del departamento propietario puede asignar fondos.",
        }

    @staticmethod
    def build_summary(project: Dict[str, Any], year: int = DEFAULT_YEAR, user: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ProjectFundingService.ensure_model(project, persist=True)
        model = deepcopy(project["fundingModel"])
        totals = ProjectFundingService._derived_totals(project, year=year)
        permissions = ProjectFundingService.permissions_for_user(project, user)
        return {
            "projectId": str(project.get("_id")),
            "model": {
                "version": model.get("version", PROJECT_FUNDING_VERSION),
                "status": model.get("status", "pooled"),
                "migrationRequired": (
                    int(model.get("version") or 0) < PROJECT_FUNDING_VERSION
                    or model.get("status") in {"legacy", "pending_migration", "active"}
                ),
            },
            "permissions": permissions,
            "totals": totals,
        }

    @staticmethod
    def decorate_project(project: Dict[str, Any], year: int = DEFAULT_YEAR, user: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = deepcopy(project)
        model = ProjectFundingService.ensure_model(payload, persist=True)
        summary = ProjectFundingService.build_summary(payload, year=year, user=user)
        payload["fundingModel"] = model
        payload["fundingSummary"] = summary
        payload["balance"] = summary["totals"]["currentAvailable"]
        payload["balance_inicial"] = summary["totals"]["initialAssigned"]
        if payload.get("departamento_id"):
            payload["departmentId"] = str(payload["departamento_id"])
        return payload

    @staticmethod
    def _complete_funding_step(project: Dict[str, Any]) -> None:
        if 1 in (project.get("status") or {}).get("completado", []):
            return
        new_status, _ = actualizar_pasos(project["status"], 1)
        mongo.db.proyectos.update_one({"_id": project["_id"]}, {"$set": {"status": new_status}})
        project["status"] = new_status

    @staticmethod
    def _apply_model_update_after_funding(
        project: Dict[str, Any],
        *,
        funding_cents: int,
        mode: str,
        user: Dict[str, Any],
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        model = ProjectFundingService.ensure_model(project, persist=True)
        now = _now_utc()
        update_fields: Dict[str, Any] = {}

        if mode == "migration":
            update_fields["fundingModel.status"] = "active"
            update_fields["fundingModel.migratedAt"] = now
            update_fields["fundingModel.migratedBy"] = str(user.get("sub"))
            update_fields["fundingModel.migrationNote"] = note or ""
            if not model.get("configuredAt"):
                update_fields["fundingModel.configuredAt"] = now
            initial_amount = int(model.get("legacyInitialBalanceSnapshot") or 0) or int(funding_cents)
            update_fields["fundingModel.initialAssignedAmount"] = initial_amount
        else:
            if not model.get("configuredAt"):
                update_fields["fundingModel.configuredAt"] = now
            if int(model.get("initialAssignedAmount") or 0) <= 0:
                update_fields["fundingModel.initialAssignedAmount"] = int(funding_cents)

        if update_fields:
            mongo.db.proyectos.update_one({"_id": project["_id"]}, {"$set": update_fields})
            project = mongo.db.proyectos.find_one({"_id": project["_id"]}) or project

        ProjectFundingService._complete_funding_step(project)
        return project

    @staticmethod
    def _project_balance_for_account(project_id: str, account_code: str, year: int = DEFAULT_YEAR) -> float:
        state = mongo.db.account_scope_state.find_one(
            {
                "year": int(year),
                "scopeType": "project",
                "scopeId": str(project_id),
                "accountCode": str(account_code),
            },
            {"balance": 1},
        )
        return float((state or {}).get("balance", 0) or 0)

    @staticmethod
    def _record_pool_movement(
        project: Dict[str, Any],
        *,
        year: int,
        movement_type: str,
        amount_cents: int,
        user: Dict[str, Any],
        description: str,
        reference: Optional[Dict[str, Any]] = None,
        source_scope_type: Optional[str] = None,
        source_scope_id: Optional[str] = None,
        source_account_code: Optional[str] = None,
        expense_account_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        if movement_type not in {"funding", "liquidation", "rule", "adjustment", "reversal"}:
            raise ValueError("Tipo de movimiento de fondos inválido")
        if int(amount_cents) <= 0:
            raise ValueError("El monto debe ser mayor que 0")

        ProjectFundingService.ensure_model(project, persist=True)
        state_filter = ProjectFundingService._pool_state_filter(project.get("_id"), year)
        now = _now_utc()
        operation_id = str(uuid.uuid4())
        is_inflow = movement_type in {"funding", "reversal"}
        delta_cents = int(amount_cents) if is_inflow else -int(amount_cents)
        query = dict(state_filter)
        if not is_inflow:
            query["availableCents"] = {"$gte": int(amount_cents)}

        increments = {
            "availableCents": delta_cents,
            "movementsCount": 1,
        }
        if movement_type == "funding":
            increments["initialFundedCents"] = int(amount_cents)
            increments["totalFundedCents"] = int(amount_cents)
        elif movement_type in {"liquidation", "rule"}:
            increments["liquidatedCents"] = int(amount_cents)
        elif movement_type == "reversal":
            increments["liquidatedCents"] = -int(amount_cents)

        counter_fields = {
            "initialFundedCents",
            "totalFundedCents",
            "liquidatedCents",
            "availableCents",
            "movementsCount",
        }
        insert_defaults = {
            "year": int(year),
            "projectId": str(project.get("_id")),
            "currency": "VES",
            "createdAt": now,
            **{field: 0 for field in counter_fields if field not in increments},
        }
        state = mongo.db.project_fund_state.find_one_and_update(
            query,
            {
                # Do not repeat incremented fields in $setOnInsert: MongoDB
                # rejects updates that modify the same path twice.
                "$setOnInsert": insert_defaults,
                "$inc": increments,
                "$set": {"lastMovementAt": now, "updatedAt": now},
            },
            upsert=movement_type == "funding",
            return_document=ReturnDocument.AFTER,
        )
        if not state:
            raise ValueError("El monto aprobado excede el saldo disponible del proyecto")

        movement = {
            "operationId": operation_id,
            "year": int(year),
            "projectId": str(project.get("_id")),
            "type": movement_type,
            "amountCents": int(amount_cents),
            "deltaCents": delta_cents,
            "balanceAfterCents": int(state.get("availableCents", 0) or 0),
            "currency": "VES",
            "description": description or "",
            "reference": reference or {},
            "sourceScopeType": source_scope_type,
            "sourceScopeId": str(source_scope_id) if source_scope_id else None,
            "sourceAccountCode": source_account_code,
            "expenseAccountCode": expense_account_code,
            "createdBy": str(user.get("sub") or ""),
            "actorName": user.get("nombre", "Usuario"),
            "createdAt": now,
        }
        try:
            mongo.db.project_fund_movements.insert_one(movement)
        except Exception:
            rollback = {key: -value for key, value in increments.items()}
            mongo.db.project_fund_state.update_one(
                state_filter,
                {"$inc": rollback, "$set": {"updatedAt": _now_utc()}},
            )
            raise

        movement.pop("_id", None)
        return {"movement": movement, "state": state}

    @staticmethod
    def migrate_to_pool(
        project: Dict[str, Any],
        *,
        year: int,
        user: Dict[str, Any],
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        project = mongo.db.proyectos.find_one({"_id": project["_id"]}) or project
        model = ProjectFundingService.ensure_model(project, persist=True)
        if int(model.get("version") or 0) >= PROJECT_FUNDING_VERSION and model.get("status") == "pooled":
            return {
                "projectId": str(project["_id"]),
                "operation": "migration",
                "alreadyMigrated": True,
                "fundingSummary": ProjectFundingService.build_summary(project, year=year, user=user),
            }

        legacy_totals = ProjectFundingService._derived_totals(project, year=year)
        available_cents = _amount_to_cents(legacy_totals.get("currentAvailable"))
        funded_cents = _amount_to_cents(legacy_totals.get("initialAssigned"))
        funded_cents = max(funded_cents, available_cents)
        liquidated_cents = max(funded_cents - available_cents, 0)
        now = _now_utc()
        state_filter = ProjectFundingService._pool_state_filter(project["_id"], year)
        state = {
            **ProjectFundingService._empty_pool_state(project["_id"], year),
            "initialFundedCents": funded_cents,
            "totalFundedCents": funded_cents,
            "liquidatedCents": liquidated_cents,
            "availableCents": available_cents,
            "movementsCount": 1,
            "lastMovementAt": now,
            "updatedAt": now,
        }
        existing_state = mongo.db.project_fund_state.find_one(state_filter)
        if not existing_state:
            mongo.db.project_fund_state.insert_one(state)
            mongo.db.project_fund_movements.insert_one({
                "operationId": str(uuid.uuid4()),
                "year": int(year),
                "projectId": str(project["_id"]),
                "type": "migration",
                "amountCents": 0,
                "deltaCents": 0,
                "balanceAfterCents": available_cents,
                "currency": "VES",
                "description": note or "Consolidación del saldo anterior en la bolsa única",
                "reference": {
                    "kind": "pool_migration",
                    "previousVersion": int(model.get("version") or 0),
                    "previousStatus": model.get("status"),
                },
                "createdBy": str(user.get("sub") or ""),
                "actorName": user.get("nombre", "Usuario"),
                "createdAt": now,
            })

        new_model = {
            **model,
            "version": PROJECT_FUNDING_VERSION,
            "status": "pooled",
            "migratedAt": now,
            "migratedBy": str(user.get("sub") or ""),
            "migrationNote": note or "Consolidación a bolsa única",
            "initialAssignedAmount": funded_cents,
        }
        mongo.db.proyectos.update_one(
            {"_id": project["_id"]},
            {
                "$set": {
                    "fundingModel": new_model,
                    "balance": available_cents,
                    "balance_inicial": funded_cents,
                }
            },
        )
        project["fundingModel"] = new_model
        agregar_log(
            project["_id"],
            f'{user.get("nombre", "Usuario")} consolidó el saldo del proyecto en una bolsa única',
        )
        return {
            "projectId": str(project["_id"]),
            "operation": "migration",
            "alreadyMigrated": False,
            "fundingSummary": ProjectFundingService.build_summary(project, year=year, user=user),
        }

    @staticmethod
    def _validate_source_scope(project: Dict[str, Any], user: Dict[str, Any], source_scope_type: str, source_scope_id: str) -> None:
        role = user.get("role")
        project_department_id = str(project.get("departamento_id")) if project.get("departamento_id") else ""
        source_scope_type = _clean_str(source_scope_type)
        source_scope_id = _clean_str(source_scope_id)

        if source_scope_type not in {"department", "global"}:
            raise ValueError("sourceScopeType debe ser department o global")

        if role == "super_admin":
            if source_scope_type == "department" and project_department_id and source_scope_id != project_department_id:
                raise ValueError("El departamento origen debe coincidir con el departamento propietario del proyecto")
            if source_scope_type == "global" and source_scope_id != "global":
                raise ValueError("sourceScopeId debe ser global para origen global")
            return

        if role == "admin_departamento":
            user_department_id = str(user.get("departmentId") or user.get("departamento_id") or "")
            if source_scope_type != "department":
                raise ValueError("Solo super_admin puede fondear desde scope global")
            if not project_department_id or user_department_id != project_department_id or source_scope_id != project_department_id:
                raise ValueError("Solo puedes fondear proyectos de tu propio departamento desde ese mismo departamento")
            return

        raise ValueError("No autorizado para asignar fondos")

    @staticmethod
    def allocate_funds(
        project: Dict[str, Any],
        *,
        year: int,
        source_scope_type: str,
        source_scope_id: str,
        allocations: Iterable[Dict[str, Any]],
        user: Dict[str, Any],
        allow_negative: bool,
        migration: bool = False,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        project = mongo.db.proyectos.find_one({"_id": project["_id"]}) or project
        model = ProjectFundingService.ensure_model(project, persist=True)
        if migration:
            permissions = ProjectFundingService.permissions_for_user(project, user)
            if not permissions.get("canFund"):
                raise ValueError(permissions.get("reason") or "No autorizado para consolidar fondos")
            return ProjectFundingService.migrate_to_pool(
                project,
                year=year,
                user=user,
                note=note,
            )
        if int(model.get("version") or 0) < PROJECT_FUNDING_VERSION or model.get("status") != "pooled":
            raise ValueError("Este proyecto debe consolidar su saldo en la bolsa única antes de recibir fondos")

        ProjectFundingService._validate_source_scope(project, user, source_scope_type, source_scope_id)

        normalized_allocations = []
        grouped_source: Dict[str, float] = {}
        total_units = 0.0
        for item in allocations or []:
            from_account_code = _clean_str(item.get("fromAccountCode"))
            amount = float(item.get("amount") or 0)
            description = _clean_str(item.get("description"))
            if not from_account_code or amount <= 0:
                raise ValueError("Cada asignación requiere fromAccountCode y amount mayor a 0")
            normalized_allocations.append(
                {
                    "fromAccountCode": from_account_code,
                    "amount": amount,
                    "description": description,
                }
            )
            grouped_source[from_account_code] = grouped_source.get(from_account_code, 0.0) + amount
            total_units += amount

        if not normalized_allocations:
            raise ValueError("allocations es requerido")

        for account_code, total_amount in grouped_source.items():
            state = mongo.db.account_scope_state.find_one(
                {
                    "year": int(year),
                    "scopeType": source_scope_type,
                    "scopeId": str(source_scope_id),
                    "accountCode": account_code,
                },
                {"balance": 1},
            )
            balance = float((state or {}).get("balance", 0) or 0)
            if not allow_negative and (balance - total_amount) < 0:
                raise ValueError(f"Saldo insuficiente en la cuenta origen {account_code}")

        results = []
        for item in normalized_allocations:
            reference = {
                "kind": "project_pool_funding",
                "fundingType": "funding",
                "projectId": str(project["_id"]),
                "projectName": project.get("nombre", ""),
                "actorName": user.get("nombre", "Usuario"),
                "title": "Asignación de fondos a la bolsa del proyecto",
                "sourceScopeType": source_scope_type,
                "sourceScopeId": str(source_scope_id),
                "toScopeType": "project_pool",
                "toScopeId": str(project["_id"]),
            }
            description = item["description"] or reference["title"]
            source_result = AccountScopeService.create_movement(
                year=int(year),
                scope_type=source_scope_type,
                scope_id=str(source_scope_id),
                account_code=item["fromAccountCode"],
                movement_type="credit",
                amount=float(item["amount"]),
                description=description,
                reference=reference,
                created_by=str(user.get("sub")),
                allow_negative=allow_negative,
            )
            try:
                pool_result = ProjectFundingService._record_pool_movement(
                    project,
                    year=year,
                    movement_type="funding",
                    amount_cents=_amount_to_cents(item["amount"]),
                    user=user,
                    description=description,
                    reference=reference,
                    source_scope_type=source_scope_type,
                    source_scope_id=source_scope_id,
                    source_account_code=item["fromAccountCode"],
                )
            except Exception:
                AccountScopeService.create_movement(
                    year=int(year),
                    scope_type=source_scope_type,
                    scope_id=str(source_scope_id),
                    account_code=item["fromAccountCode"],
                    movement_type="debit",
                    amount=float(item["amount"]),
                    description=f"Reverso automático: {description}",
                    reference={**reference, "kind": "project_pool_funding_reversal"},
                    created_by=str(user.get("sub")),
                    allow_negative=True,
                )
                raise
            results.append({
                "fromAccountCode": item["fromAccountCode"],
                "amount": float(item["amount"]),
                "sourceState": source_result.get("state"),
                "poolState": pool_result.get("state"),
            })

            agregar_log(
                project["_id"],
                f'{user.get("nombre", "Usuario")} asignó fondos a la bolsa del proyecto '
                f'desde la cuenta {item["fromAccountCode"]} por Bs. {float(item["amount"]):.2f}',
            )

        project = ProjectFundingService._apply_model_update_after_funding(
            project,
            funding_cents=_amount_to_cents(total_units),
            mode="funding",
            user=user,
            note=note,
        )

        pool_state = ProjectFundingService.get_pool_state(project["_id"], year, create=True)
        mongo.db.proyectos.update_one(
            {"_id": project["_id"]},
            {
                "$set": {
                    "balance": int(pool_state.get("availableCents", 0) or 0),
                    "balance_inicial": int(pool_state.get("totalFundedCents", 0) or 0),
                }
            },
        )

        return {
            "projectId": str(project["_id"]),
            "operation": "funding",
            "allocations": results,
            "fundingSummary": ProjectFundingService.build_summary(project, year=year, user=user),
        }

    @staticmethod
    def consume_project_account(
        project: Dict[str, Any],
        *,
        year: int,
        account_code: str,
        amount: float,
        user: Dict[str, Any],
        description: str,
        reference: Optional[Dict[str, Any]],
        allow_negative: bool,
        log_message: str,
    ) -> Dict[str, Any]:
        if not account_code:
            raise ValueError("accountCode es requerido")
        if float(amount) <= 0:
            raise ValueError("amount debe ser mayor que 0")

        model = ProjectFundingService.ensure_model(project, persist=True)
        if int(model.get("version") or 0) < PROJECT_FUNDING_VERSION or model.get("status") != "pooled":
            raise ValueError("Este proyecto debe consolidar su saldo en la bolsa única antes de registrar cierres")

        account = mongo.db.master_accounts.find_one(
            {"year": int(year), "code": str(account_code)},
            {"_id": 0, "is_header": 1, "group": 1},
        )
        if not account:
            raise ValueError("La cuenta contable no existe para el año indicado")
        if account.get("is_header"):
            raise ValueError("Selecciona una cuenta detalle para liquidar el gasto")
        if str(account.get("group") or "").upper() != "EGRESO":
            raise ValueError("La liquidación debe imputarse a una cuenta de egreso")

        reference_data = reference or {}
        movement_type = "rule" if reference_data.get("kind") == "fixed_rule" else "liquidation"
        result = ProjectFundingService._record_pool_movement(
            project,
            year=year,
            movement_type=movement_type,
            amount_cents=_amount_to_cents(amount),
            user=user,
            description=description,
            reference=reference_data,
            expense_account_code=str(account_code),
        )

        state = result.get("state") or {}
        mongo.db.proyectos.update_one(
            {"_id": project["_id"]},
            {"$set": {"balance": int(state.get("availableCents", 0) or 0)}},
        )

        agregar_log(project["_id"], log_message)
        return result

    @staticmethod
    def build_timeline(project: Dict[str, Any], year: int = DEFAULT_YEAR) -> List[Dict[str, Any]]:
        project = mongo.db.proyectos.find_one({"_id": project["_id"]}) or project
        model = ProjectFundingService.ensure_model(project, persist=True)
        project_id = str(project["_id"])
        project_object_id = project["_id"]

        ledger_rows = list(
            mongo.db.ledger_movements.find(
                {"year": int(year), "scopeType": "project", "scopeId": project_id}
            )
        )
        ledger_rows.sort(key=lambda item: _sort_datetime(item.get("createdAt")))

        balance = 0.0
        timeline: List[Dict[str, Any]] = []
        for row in ledger_rows:
            delta = float(row.get("amount", 0) or 0)
            if row.get("type") == "credit":
                delta *= -1
            balance = round(balance + delta, 2)
            reference = row.get("reference") or {}
            funding_type = reference.get("fundingType")
            if funding_type == "migration":
                event_type = "migration"
            elif reference.get("kind") == "fixed_rule":
                event_type = "rule"
            elif reference.get("kind") == "project_expense":
                event_type = "expense"
            elif row.get("type") == "debit":
                event_type = "funding"
            else:
                event_type = "adjustment"

            title = reference.get("title") or {
                "migration": "Migración de saldo legacy",
                "rule": "Consumo por regla fija",
                "expense": "Consumo por actividad",
                "funding": "Asignación de fondos",
                "adjustment": "Ajuste contable",
            }.get(event_type, "Movimiento contable")

            timeline.append(
                {
                    "id": str(row.get("_id") or reference.get("id") or f"{project_id}-{len(timeline)}"),
                    "occurredAt": row.get("createdAt"),
                    "type": event_type,
                    "source": "ledger",
                    "title": title,
                    "description": row.get("description", ""),
                    "amount": delta,
                    "projectBalanceAfter": balance,
                    "accountCode": row.get("accountCode"),
                    "accountDescription": reference.get("accountDescription", ""),
                    "fromScopeType": reference.get("fromScopeType") or reference.get("sourceScopeType"),
                    "fromScopeId": reference.get("fromScopeId") or reference.get("sourceScopeId"),
                    "toScopeType": reference.get("toScopeType") or ("project" if event_type in {"funding", "migration"} else None),
                    "toScopeId": reference.get("toScopeId") or (project_id if event_type in {"funding", "migration"} else None),
                    "actorName": reference.get("actorName") or row.get("createdBy", ""),
                    "reference": reference,
                }
            )

        pool_rows = list(
            mongo.db.project_fund_movements.find(
                {"year": int(year), "projectId": project_id}
            )
        )
        pool_rows.sort(key=lambda item: _sort_datetime(item.get("createdAt")))
        for row in pool_rows:
            pool_type = row.get("type")
            event_type = {
                "funding": "funding",
                "liquidation": "expense",
                "rule": "rule",
                "migration": "migration",
                "reversal": "adjustment",
                "adjustment": "adjustment",
            }.get(pool_type, "adjustment")
            reference = row.get("reference") or {}
            title = reference.get("title") or {
                "funding": "Asignación a la bolsa del proyecto",
                "expense": "Liquidación de actividad",
                "rule": "Liquidación de regla fija",
                "migration": "Consolidación a bolsa única",
                "adjustment": "Ajuste de la bolsa del proyecto",
            }.get(event_type, "Movimiento de fondos")
            timeline.append(
                {
                    "id": str(row.get("_id") or row.get("operationId")),
                    "occurredAt": row.get("createdAt"),
                    "type": event_type,
                    "source": "project_pool",
                    "title": title,
                    "description": row.get("description", ""),
                    "amount": _cents_to_units(row.get("deltaCents")),
                    "projectBalanceAfter": _cents_to_units(row.get("balanceAfterCents")),
                    "accountCode": row.get("expenseAccountCode") or row.get("sourceAccountCode"),
                    "accountDescription": reference.get("accountDescription", ""),
                    "fromScopeType": row.get("sourceScopeType"),
                    "fromScopeId": row.get("sourceScopeId"),
                    "toScopeType": "project_pool" if event_type == "funding" else None,
                    "toScopeId": project_id if event_type == "funding" else None,
                    "actorName": row.get("actorName") or row.get("createdBy", ""),
                    "reference": reference,
                }
            )

        include_legacy_actions = model.get("status") in {"legacy", "pending_migration"}
        migrated_at = model.get("migratedAt")
        actions = list(
            mongo.db.acciones.find({"$or": [{"project_id": project_object_id}, {"proyecto_id": project_object_id}]})
        )
        if actions:
            actions.sort(key=lambda item: _sort_datetime(item.get("created_at")))
        if actions and (include_legacy_actions or migrated_at):
            for row in actions:
                created_at = row.get("created_at")
                if migrated_at and created_at and created_at >= migrated_at:
                    continue
                amount = _cents_to_units(row.get("amount", 0))
                total_amount = _cents_to_units(row.get("total_amount", 0))
                action_type = _clean_str(row.get("type"))
                if action_type.lower() == "fondeo":
                    event_type = "funding"
                elif action_type.lower().startswith("retiro"):
                    event_type = "expense"
                else:
                    event_type = "adjustment"
                timeline.append(
                    {
                        "id": str(row.get("_id") or f"legacy-{len(timeline)}"),
                        "occurredAt": created_at,
                        "type": event_type,
                        "source": "legacy_action",
                        "title": action_type or "Movimiento legacy",
                        "description": action_type or "",
                        "amount": amount,
                        "projectBalanceAfter": total_amount,
                        "accountCode": row.get("accountCode") or row.get("cuenta_contable"),
                        "accountDescription": "",
                        "fromScopeType": None,
                        "fromScopeId": None,
                        "toScopeType": "project" if event_type == "funding" else None,
                        "toScopeId": project_id if event_type == "funding" else None,
                        "actorName": row.get("user", ""),
                        "reference": {},
                    }
                )

        timeline.sort(key=lambda item: _sort_datetime(item.get("occurredAt")), reverse=True)
        return timeline

    @staticmethod
    def timeline_response(
        project: Dict[str, Any],
        *,
        year: int = DEFAULT_YEAR,
        page: int = 0,
        limit: int = 20,
    ) -> Dict[str, Any]:
        rows = ProjectFundingService.build_timeline(project, year=year)
        if limit <= 0:
            limit = 20
        if page < 0:
            page = 0
        start = page * limit
        end = start + limit
        return {"request_list": rows[start:end], "count": len(rows)}

    @staticmethod
    def report_payload(project: Dict[str, Any], year: int = DEFAULT_YEAR) -> Dict[str, Any]:
        summary = ProjectFundingService.build_summary(project, year=year)
        timeline_asc = ProjectFundingService.build_timeline(project, year=year)
        timeline_asc.sort(key=lambda item: _sort_datetime(item.get("occurredAt")))

        balance_history = []
        egresos_por_tipo: Dict[str, float] = {}
        ingresos = 0.0
        egresos = 0.0
        for item in timeline_asc:
            occurred_at = item.get("occurredAt")
            if occurred_at:
                balance_history.append(
                    {
                        "fecha": occurred_at.strftime("%Y-%m-%d"),
                        "saldo": item.get("projectBalanceAfter", 0),
                    }
                )
            amount = float(item.get("amount", 0) or 0)
            if amount > 0:
                ingresos += amount
            elif amount < 0:
                egresos += abs(amount)
                label = {
                    "expense": "Actividades",
                    "rule": "Reglas fijas",
                    "migration": "Migración",
                    "adjustment": "Ajustes",
                }.get(item.get("type"), "Otros")
                egresos_por_tipo[label] = round(egresos_por_tipo.get(label, 0) + abs(amount), 2)

        budgets = list(
            mongo.db.documentos.find({"$or": [{"project_id": project["_id"]}, {"proyecto_id": project["_id"]}]})
        )
        new_activities = [item for item in budgets if item.get("status") == "new"]
        administrative_closed_activities = [item for item in budgets if item.get("status") == "in_progress"]
        finished_budgets = [item for item in budgets if item.get("status") == "finished"]

        return {
            "balance_history": balance_history,
            "egresos_tipo": [{"tipo": key, "monto": value} for key, value in egresos_por_tipo.items()],
            "resumen": {
                "ingresos": round(ingresos, 2),
                "egresos": round(egresos, 2),
                "actividades_totales": len(budgets),
                "actividades_nuevas": len(new_activities),
                "actividades_cierre_administrativo": len(administrative_closed_activities),
                "actividades_finalizadas": len(finished_budgets),
                "presupuestos": len(finished_budgets),
                "represupuestos": len(new_activities) + len(administrative_closed_activities),
                "miembros": len(project.get("miembros") or []),
            },
            "saldo_inicial": summary["totals"]["initialAssigned"],
            "saldo_restante": summary["totals"]["currentAvailable"],
        }
