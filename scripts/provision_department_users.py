import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api import create_app
from api.extensions import bcrypt, mongo

TARGET_USERS = [
    {
        "department_name": "GSU",
        "email": "usuario.gsu@deu.local",
        "password": "DeuTemp#GSU2026",
        "nombre": "Usuario GSU",
    },
    {
        "department_name": "DRI",
        "email": "usuario.dri@deu.local",
        "password": "DeuTemp#DRI2026",
        "nombre": "Usuario DRI",
    },
    {
        "department_name": "DAA",
        "email": "usuario.daa@deu.local",
        "password": "DeuTemp#DAA2026",
        "nombre": "Usuario DAA",
    },
    {
        "department_name": "DECP",
        "email": "usuario.decp@deu.local",
        "password": "DeuTemp#DECP2026",
        "nombre": "Usuario DECP",
    },
]


def _extract_department_id(user):
    value = user.get("departamento_id") or user.get("departmentId")
    if not value:
        return None
    if isinstance(value, dict) and "$oid" in value:
        return str(value.get("$oid"))
    return str(value)


def _extract_role(user):
    role = user.get("rol") or user.get("role")
    if role:
        return str(role).strip()
    if user.get("is_admin"):
        return "super_admin"
    return "usuario"


def _password_matches(user, plain_password):
    hashed_password = user.get("password")
    if not hashed_password:
        return False
    try:
        return bool(bcrypt.check_password_hash(hashed_password, plain_password))
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Provisiona usuarios por departamento (idempotente)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No escribe cambios; solo simula el resultado.",
    )
    args = parser.parse_args()

    summary = {
        "created": [],
        "updated": [],
        "skipped": [],
        "missing_departments": [],
    }

    app = create_app()
    with app.app_context():
        for target in TARGET_USERS:
            department = mongo.db.departamentos.find_one(
                {"nombre": target["department_name"]},
                {"_id": 1, "nombre": 1},
            )
            if not department:
                summary["missing_departments"].append(target["department_name"])
                continue

            department_id = department["_id"]
            user = mongo.db.usuarios.find_one({"email": target["email"]})

            if not user:
                summary["created"].append(
                    {
                        "email": target["email"],
                        "department": target["department_name"],
                    }
                )
                if args.dry_run:
                    continue

                mongo.db.usuarios.insert_one(
                    {
                        "nombre": target["nombre"],
                        "email": target["email"],
                        "password": bcrypt.generate_password_hash(target["password"]).decode("utf-8"),
                        "rol": "usuario",
                        "departamento_id": department_id,
                    }
                )
                continue

            update_fields = {}
            if user.get("nombre") != target["nombre"]:
                update_fields["nombre"] = target["nombre"]

            if _extract_role(user) != "usuario":
                update_fields["rol"] = "usuario"

            current_department_id = _extract_department_id(user)
            if current_department_id != str(department_id):
                update_fields["departamento_id"] = department_id

            if not _password_matches(user, target["password"]):
                update_fields["password"] = bcrypt.generate_password_hash(target["password"]).decode("utf-8")

            if not update_fields:
                summary["skipped"].append(
                    {
                        "email": target["email"],
                        "department": target["department_name"],
                    }
                )
                continue

            summary["updated"].append(
                {
                    "email": target["email"],
                    "department": target["department_name"],
                    "fields": sorted(update_fields.keys()),
                }
            )
            if args.dry_run:
                continue

            mongo.db.usuarios.update_one(
                {"_id": user["_id"]},
                {"$set": update_fields},
            )

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
