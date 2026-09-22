from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


DEFAULT_PROJECT_MEMBER_ROLES = (
    {"value": "lider", "label": "Líder"},
    {"value": "miembro", "label": "Miembro"},
)


def normalize_project_member_role(value: Any) -> Optional[Dict[str, str]]:
    if isinstance(value, str):
        role_value = value.strip()
        role_label = role_value
    elif isinstance(value, dict):
        role_value = str(value.get("value") or value.get("nombre") or "").strip()
        role_label = str(value.get("label") or value.get("nombre") or role_value).strip()
    else:
        return None

    if not role_value or not role_label:
        return None

    for default_role in DEFAULT_PROJECT_MEMBER_ROLES:
        if role_value.lower() == default_role["value"]:
            return dict(default_role)

    return {"value": role_value, "label": role_label}


def merge_project_member_roles(items: Iterable[Any]) -> List[Dict[str, Any]]:
    roles_by_value = {
        role["value"]: dict(role)
        for role in DEFAULT_PROJECT_MEMBER_ROLES
    }

    for item in items:
        normalized = normalize_project_member_role(item)
        if not normalized:
            continue

        role = dict(item) if isinstance(item, dict) else {}
        role.update(normalized)
        roles_by_value[normalized["value"]] = role

    return list(roles_by_value.values())
