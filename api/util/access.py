from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from bson import ObjectId

from api.extensions import mongo

ROLE_USUARIO = "usuario"
ROLE_ADMIN_DEPARTAMENTO = "admin_departamento"
ROLE_SUPER_ADMIN = "super_admin"
VALID_ROLES = (ROLE_USUARIO, ROLE_ADMIN_DEPARTAMENTO, ROLE_SUPER_ADMIN)


def pick_value(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data.get(key) not in (None, ""):
            return data.get(key)
    return None


def normalize_role(value: Any) -> str:
    role = str(value or "").strip()
    if not role:
        return ROLE_USUARIO
    return role


def user_role(user: Optional[Dict[str, Any]]) -> str:
    if not user:
        return ROLE_USUARIO

    role = user.get("role") or user.get("rol")
    if role:
        return normalize_role(role)

    if user.get("is_admin"):
        return ROLE_SUPER_ADMIN
    return ROLE_USUARIO


def is_super_admin(user: Optional[Dict[str, Any]]) -> bool:
    return user_role(user) == ROLE_SUPER_ADMIN


def is_admin_departamento(user: Optional[Dict[str, Any]]) -> bool:
    return user_role(user) == ROLE_ADMIN_DEPARTAMENTO


def parse_object_id(value: Any) -> Optional[ObjectId]:
    if value in (None, ""):
        return None
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, dict) and "$oid" in value:
        value = value.get("$oid")
    try:
        return ObjectId(str(value).strip())
    except Exception:
        return None


def object_id_to_str(value: Any) -> Optional[str]:
    object_id = parse_object_id(value)
    return str(object_id) if object_id else None


def user_department_id(user: Optional[Dict[str, Any]]) -> Optional[str]:
    if not user:
        return None

    dep_value = user.get("departmentId") or user.get("departamento_id")
    return object_id_to_str(dep_value)


def resolve_department_object_id(
    department_value: Any,
    *,
    required: bool,
) -> Tuple[Optional[ObjectId], Optional[str]]:
    if department_value in (None, ""):
        if required:
            return None, "El departamento es requerido"
        return None, None

    dept_object_id = parse_object_id(department_value)
    if not dept_object_id:
        return None, "ID de departamento inválido"

    department = mongo.db.departamentos.find_one({"_id": dept_object_id}, {"_id": 1})
    if not department:
        return None, "Departamento no encontrado"

    return dept_object_id, None


def ensure_role_department_policy(
    role: str,
    department_value: Any,
) -> Tuple[Optional[ObjectId], Optional[str]]:
    normalized_role = normalize_role(role)
    is_department_required = normalized_role != ROLE_SUPER_ADMIN
    return resolve_department_object_id(department_value, required=is_department_required)


def project_department_id(project: Optional[Dict[str, Any]]) -> Optional[str]:
    if not project:
        return None
    return object_id_to_str(project.get("departamento_id") or project.get("departmentId"))


def department_scope_filter(user: Dict[str, Any], field: str = "departamento_id") -> Dict[str, Any]:
    if is_super_admin(user) and not user.get("_using_dept_context"):
        return {}

    user_dep = user_department_id(user)
    if not user_dep:
        return {"_id": {"$exists": False}}
    return {field: ObjectId(user_dep)}


def can_access_department(user: Dict[str, Any], department_id: Any) -> bool:
    target_department_id = object_id_to_str(department_id)
    if not target_department_id:
        return False

    if is_super_admin(user):
        if user.get("_using_dept_context"):
            context_department_id = user_department_id(user)
            return bool(context_department_id and context_department_id == target_department_id)
        return True

    return user_department_id(user) == target_department_id


def can_access_project(user: Dict[str, Any], project: Optional[Dict[str, Any]]) -> bool:
    if not project:
        return False

    target_department_id = project_department_id(project)

    if is_super_admin(user):
        if not target_department_id:
            return not user.get("_using_dept_context")
        if user.get("_using_dept_context"):
            context_department_id = user_department_id(user)
            return bool(context_department_id and context_department_id == target_department_id)
        return True

    if not target_department_id:
        return False

    user_dep = user_department_id(user)
    return bool(user_dep and user_dep == target_department_id)
