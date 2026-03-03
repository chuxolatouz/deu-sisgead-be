from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from bson import ObjectId

from api.extensions import mongo
from api.services.accounting_service import (
    AccountingIndexes,
    AccountScopeService,
    DEFAULT_YEAR,
)
from api.util.common import agregar_log
from api.util.utils import actualizar_pasos


PROJECT_FUNDING_VERSION = 2


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
        "status": "active",
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
            changed = True

        for key, value in _build_default_model().items():
            if key not in model:
                model[key] = value
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
    def _derived_totals(project: Dict[str, Any], year: int = DEFAULT_YEAR) -> Dict[str, Any]:
        ProjectFundingService.ensure_model(project, persist=True)
        project_id = str(project.get("_id"))
        model = project["fundingModel"]
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
            funded_accounts_count = sum(1 for item in rows if float(item.get("balance", 0) or 0) > 0)
            last_movement_at = None
            dates = [item.get("lastMovementAt") for item in rows if item.get("lastMovementAt")]
            if dates:
                last_movement_at = max(dates, key=_sort_datetime)

        return {
            "currentAvailable": current_available,
            "initialAssigned": initial_assigned,
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
                "status": model.get("status", "active"),
                "migrationRequired": model.get("status") in {"legacy", "pending_migration"},
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
            if model.get("status") not in {"legacy", "pending_migration"}:
                raise ValueError("La migración solo aplica a proyectos legacy")
        elif model.get("status") in {"legacy", "pending_migration"}:
            raise ValueError("Este proyecto requiere migración de saldo legacy antes de recibir nuevas asignaciones")

        ProjectFundingService._validate_source_scope(project, user, source_scope_type, source_scope_id)

        normalized_allocations = []
        grouped_source: Dict[str, float] = {}
        total_units = 0.0
        for item in allocations or []:
            from_account_code = _clean_str(item.get("fromAccountCode"))
            to_account_code = _clean_str(item.get("toAccountCode"))
            amount = float(item.get("amount") or 0)
            description = _clean_str(item.get("description"))
            if not from_account_code or not to_account_code or amount <= 0:
                raise ValueError("Cada asignación requiere fromAccountCode, toAccountCode y amount mayor a 0")
            normalized_allocations.append(
                {
                    "fromAccountCode": from_account_code,
                    "toAccountCode": to_account_code,
                    "amount": amount,
                    "description": description,
                }
            )
            grouped_source[from_account_code] = grouped_source.get(from_account_code, 0.0) + amount
            total_units += amount

        if not normalized_allocations:
            raise ValueError("allocations es requerido")

        if migration:
            legacy_balance_units = _cents_to_units(model.get("legacyCurrentBalanceSnapshot"))
            if round(total_units, 2) != round(legacy_balance_units, 2):
                raise ValueError("La suma asignada debe coincidir exactamente con el saldo legacy actual pendiente")

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

        operation_kind = "migration" if migration else "funding"
        results = []
        for item in normalized_allocations:
            reference = {
                "kind": "transfer",
                "fundingType": operation_kind,
                "projectId": str(project["_id"]),
                "projectName": project.get("nombre", ""),
                "actorName": user.get("nombre", "Usuario"),
                "title": "Migración de saldo legacy" if migration else "Asignación de fondos",
                "sourceScopeType": source_scope_type,
                "sourceScopeId": str(source_scope_id),
                "toScopeType": "project",
                "toScopeId": str(project["_id"]),
            }
            result = AccountScopeService.transfer_between_accounts(
                year=int(year),
                from_scope_type=source_scope_type,
                from_scope_id=str(source_scope_id),
                to_scope_type="project",
                to_scope_id=str(project["_id"]),
                from_account_code=item["fromAccountCode"],
                to_account_code=item["toAccountCode"],
                amount=float(item["amount"]),
                description=item["description"] or reference["title"],
                reference=reference,
                created_by=str(user.get("sub")),
                allow_negative=allow_negative,
            )
            results.append(result)

            action = "migro saldo legacy" if migration else "asigno fondos"
            agregar_log(
                project["_id"],
                f'{user.get("nombre", "Usuario")} {action} al proyecto en la partida {item["toAccountCode"]} '
                f'desde {source_scope_type}:{source_scope_id} por un monto de Bs. {float(item["amount"]):.2f}',
            )

        project = ProjectFundingService._apply_model_update_after_funding(
            project,
            funding_cents=_amount_to_cents(total_units),
            mode="migration" if migration else "funding",
            user=user,
            note=note,
        )

        return {
            "projectId": str(project["_id"]),
            "operation": operation_kind,
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
        if model.get("status") in {"legacy", "pending_migration"}:
            raise ValueError("Este proyecto debe migrarse a partidas antes de registrar consumos por cuenta")

        result = AccountScopeService.create_movement(
            year=int(year),
            scope_type="project",
            scope_id=str(project["_id"]),
            account_code=str(account_code),
            movement_type="credit",
            amount=float(amount),
            description=description,
            reference=reference or {},
            created_by=str(user.get("sub")),
            allow_negative=allow_negative,
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
        finished_budgets = [item for item in budgets if item.get("status") == "finished"]

        return {
            "balance_history": balance_history,
            "egresos_tipo": [{"tipo": key, "monto": value} for key, value in egresos_por_tipo.items()],
            "resumen": {
                "ingresos": round(ingresos, 2),
                "egresos": round(egresos, 2),
                "presupuestos": len(finished_budgets),
                "represupuestos": len([item for item in budgets if item.get("status") != "finished"]),
                "miembros": len(project.get("miembros") or []),
            },
            "saldo_inicial": summary["totals"]["initialAssigned"],
            "saldo_restante": summary["totals"]["currentAvailable"],
        }
