from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from bson import ObjectId

from api.extensions import mongo


def _now_utc():
    return datetime.now(timezone.utc)


def _parse_object_id(value):
    try:
        return ObjectId(str(value).strip())
    except Exception:
        return None


def _requirement_id(value):
    if isinstance(value, dict):
        value = value.get("requirementId") or value.get("requerimientoId") or value.get("_id") or value.get("id")
        if isinstance(value, dict):
            value = value.get("$oid")
    return str(value or "").strip()


def _account_snapshot(account: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "code": str(account.get("code") or "").strip(),
        "description": str(account.get("description") or "").strip(),
        "group": str(account.get("group") or "").strip(),
        "level": int(account.get("level") or 0),
        "isHeader": bool(account.get("is_header")),
        "parentCode": account.get("parent_code") or None,
        "year": int(account.get("year") or 0),
    }


class RequirementCatalogService:
    collection_name = "requirement_catalog"

    @classmethod
    def collection(cls):
        return getattr(mongo.db, cls.collection_name)

    @classmethod
    def ensure_indexes(cls):
        collection = cls.collection()
        collection.create_index([("nombre_normalizado", 1)], unique=True, sparse=True)
        collection.create_index([("activo", 1), ("eliminado", 1), ("nombre", 1)])
        collection.create_index([("accountCode", 1)])

    @staticmethod
    def normalize_name(value: Any) -> str:
        return " ".join(str(value or "").strip().lower().split())

    @staticmethod
    def account_snapshot(account: Dict[str, Any]) -> Dict[str, Any]:
        return _account_snapshot(account)

    @staticmethod
    def find_account(year: int, account_code: str):
        return mongo.db.master_accounts.find_one(
            {"year": int(year), "code": str(account_code or "").strip()},
            {"_id": 0},
        )

    @classmethod
    def serialize(cls, requirement: Dict[str, Any], *, project_count=0, activity_count=0):
        account = dict(requirement.get("account") or {})
        if not account:
            account = {
                "code": requirement.get("accountCode") or "",
                "description": requirement.get("accountDescription") or "",
                "group": requirement.get("accountGroup") or "",
                "level": requirement.get("accountLevel") or 0,
                "isHeader": bool(requirement.get("accountIsHeader")),
                "parentCode": requirement.get("accountParentCode"),
                "year": requirement.get("accountReferenceYear") or 0,
            }
        payload = {
            "_id": str(requirement.get("_id")),
            "nombre": str(requirement.get("nombre") or "").strip(),
            "descripcion": str(requirement.get("descripcion") or "").strip(),
            "accountCode": str(requirement.get("accountCode") or account.get("code") or "").strip(),
            "account": account,
            "accountReferenceYear": int(requirement.get("accountReferenceYear") or account.get("year") or 0),
            "activo": requirement.get("activo") is not False,
            "eliminado": requirement.get("eliminado") is True,
            "createdAt": requirement.get("createdAt"),
            "updatedAt": requirement.get("updatedAt"),
            "deletedAt": requirement.get("deletedAt"),
        }
        payload["projectCount"] = int(project_count or 0)
        payload["activityCount"] = int(activity_count or 0)
        return payload

    @classmethod
    def assignment_snapshot(cls, requirement: Dict[str, Any], account: Dict[str, Any]):
        return {
            "requirementId": str(requirement.get("_id")),
            "nombre": str(requirement.get("nombre") or "").strip(),
            "descripcion": str(requirement.get("descripcion") or "").strip(),
            "accountCode": str(account.get("code") or "").strip(),
            "account": _account_snapshot(account),
        }

    @classmethod
    def resolve_for_project(
        cls,
        values: Any,
        *,
        year: int,
        allowed_inactive_ids: Optional[Iterable[str]] = None,
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        if values in (None, ""):
            return [], None
        if not isinstance(values, list):
            return None, "requerimientos debe ser un arreglo"

        allowed = {str(value) for value in (allowed_inactive_ids or [])}
        snapshots = []
        seen = set()
        for raw_value in values:
            requirement_id = _requirement_id(raw_value)
            if not requirement_id or requirement_id in seen:
                continue
            seen.add(requirement_id)
            object_id = _parse_object_id(requirement_id)
            if not object_id:
                return None, f"ID de requerimiento inválido: {requirement_id}"
            requirement = cls.collection().find_one({"_id": object_id})
            if not requirement:
                return None, "Requerimiento no encontrado"
            if (requirement.get("eliminado") is True or requirement.get("activo") is False) and requirement_id not in allowed:
                return None, f'El requerimiento "{requirement.get("nombre", "")}" no está disponible'

            account_code = requirement.get("accountCode")
            account = cls.find_account(year, account_code)
            if not account:
                return None, (
                    f'La cuenta {account_code} del requerimiento "{requirement.get("nombre", "")}" '
                    f"no existe para el año {year}"
                )
            snapshots.append(cls.assignment_snapshot(requirement, account))
        return snapshots, None

    @staticmethod
    def project_requirement_ids(project: Dict[str, Any]):
        return {
            _requirement_id(item)
            for item in (project.get("requerimientos") or [])
            if _requirement_id(item)
        }

    @staticmethod
    def resolve_from_project(project: Dict[str, Any], values: Any):
        if values in (None, ""):
            return [], None
        if not isinstance(values, list):
            return None, "requerimientos debe ser un arreglo"

        by_id = {
            _requirement_id(item): dict(item)
            for item in (project.get("requerimientos") or [])
            if _requirement_id(item)
        }
        selected = []
        seen = set()
        for raw_value in values:
            requirement_id = _requirement_id(raw_value)
            if not requirement_id or requirement_id in seen:
                continue
            requirement = by_id.get(requirement_id)
            if not requirement:
                return None, "La actividad solo puede usar requerimientos asociados al proyecto"
            seen.add(requirement_id)
            selected.append(requirement)
        return selected, None

    @staticmethod
    def find_assignment(container: Dict[str, Any], requirement_id: Any):
        target = _requirement_id(requirement_id)
        return next(
            (
                dict(item)
                for item in (container.get("requerimientos") or [])
                if _requirement_id(item) == target
            ),
            None,
        )

    @classmethod
    def account_matches_requirement(cls, requirement: Dict[str, Any], account_code: str, year: int):
        selected_code = str(account_code or "").strip()
        linked_code = str(requirement.get("accountCode") or "").strip()
        account_info = requirement.get("account") or {}
        is_header = bool(account_info.get("isHeader"))

        if not is_header:
            if selected_code != linked_code:
                return False, f"La cuenta del requerimiento debe ser {linked_code}"
            return True, None

        selected = cls.find_account(year, selected_code)
        if not selected or selected.get("is_header"):
            return False, "Selecciona una cuenta detalle para el requerimiento"

        current = selected
        visited = set()
        while current and current.get("parent_code"):
            parent_code = str(current.get("parent_code"))
            if parent_code == linked_code:
                return True, None
            if parent_code in visited:
                break
            visited.add(parent_code)
            current = cls.find_account(year, parent_code)
        return False, f"La cuenta seleccionada no pertenece a la cuenta titular {linked_code}"


__all__ = ["RequirementCatalogService", "_now_utc", "_parse_object_id", "_requirement_id"]
