import argparse
import json
import random
import re
from datetime import datetime, timezone

from bson import ObjectId

from api import create_app
from api.extensions import mongo


def _utcnow():
    return datetime.now(timezone.utc)


def _slugify(value):
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "categoria"


def _normalize_name(value):
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _normalize_color(value):
    if value is None:
        return None
    color = str(value).strip().lstrip("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}", color):
        return None
    return color


def _random_color():
    return "".join(random.choices("0123456789ABCDEF", k=6))


def _safe_object_id(value):
    try:
        return ObjectId(str(value).strip())
    except Exception:
        return None


def _safe_create_index(spec, **kwargs):
    try:
        mongo.db.categorias.create_index(spec, **kwargs)
        return True
    except Exception:
        return False


def _build_unique_slug(base_slug, used):
    suffix = 0
    while True:
        candidate = base_slug if suffix == 0 else f"{base_slug}-{suffix + 1}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        suffix += 1


def migrate_categories(dry_run=False):
    now = _utcnow()
    rows = list(mongo.db.categorias.find())
    used_values = set()
    used_name_keys = set()
    updates = []

    for index, row in enumerate(rows):
        category_id = row.get("_id")
        name = (row.get("nombre") or "").strip()
        if not name:
            name = f"Categoría {index + 1}"

        base_slug = _slugify(row.get("value") or name)
        value = _build_unique_slug(base_slug, used_values)

        name_key = _normalize_name(name)
        if not name_key:
            name_key = f"categoria-{index + 1}"
        if name_key in used_name_keys:
            name_key = f"{name_key}-{index + 1}"
        used_name_keys.add(name_key)

        activo = False if row.get("activo") is False else True
        eliminado = True if row.get("eliminado") is True else False
        created_at = row.get("createdAt") or row.get("created_at") or now
        updated_at = row.get("updatedAt") or row.get("updated_at") or created_at
        deleted_at = row.get("deletedAt") or row.get("deleted_at")
        if eliminado and not deleted_at:
            deleted_at = updated_at
        if not eliminado:
            deleted_at = None

        normalized_payload = {
            "nombre": name,
            "nombre_normalizado": name_key,
            "value": value,
            "label": (row.get("label") or name).strip(),
            "color": _normalize_color(row.get("color")) or _random_color(),
            "activo": activo,
            "eliminado": eliminado,
            "createdAt": created_at,
            "updatedAt": updated_at,
            "deletedAt": deleted_at,
        }

        updates.append((category_id, normalized_payload))

    applied = 0
    if not dry_run:
        for category_id, payload in updates:
            mongo.db.categorias.update_one({"_id": category_id}, {"$set": payload})
            applied += 1

        _safe_create_index([("value", 1)], unique=True, sparse=True)
        _safe_create_index([("nombre_normalizado", 1)], unique=True, sparse=True)
        _safe_create_index([("activo", 1), ("eliminado", 1)])

    return {
        "scanned": len(rows),
        "prepared_updates": len(updates),
        "applied_updates": applied,
    }


def migrate_project_references(dry_run=False):
    categories = list(mongo.db.categorias.find({}, {"_id": 1, "value": 1}))
    by_value = {
        str(cat.get("value")): cat.get("_id")
        for cat in categories
        if cat.get("value")
    }
    valid_ids = {str(cat.get("_id")): cat.get("_id") for cat in categories}

    scanned = 0
    updated = 0

    for project in mongo.db.proyectos.find({}, {"categoria": 1}):
        scanned += 1
        project_id = project.get("_id")
        category_ref = project.get("categoria")
        target_category_id = None

        if isinstance(category_ref, ObjectId):
            continue

        if isinstance(category_ref, dict) and category_ref.get("$oid"):
            target_category_id = _safe_object_id(category_ref.get("$oid"))
        elif isinstance(category_ref, str):
            ref_clean = category_ref.strip()
            object_ref = _safe_object_id(ref_clean)
            if object_ref and ref_clean in valid_ids:
                target_category_id = object_ref
            elif ref_clean in by_value:
                target_category_id = by_value[ref_clean]
        elif category_ref in (None, ""):
            continue

        if not target_category_id:
            continue

        if dry_run:
            updated += 1
            continue

        mongo.db.proyectos.update_one(
            {"_id": project_id},
            {"$set": {"categoria": target_category_id}},
        )
        updated += 1

    return {
        "scanned_projects": scanned,
        "updated_projects": updated,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Normaliza categorías de proyectos y migra referencias legacy de proyectos."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo calcula cambios sin escribir en base de datos.",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        category_result = migrate_categories(dry_run=args.dry_run)
        project_result = migrate_project_references(dry_run=args.dry_run)
        output = {
            "dryRun": args.dry_run,
            "categories": category_result,
            "projects": project_result,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
