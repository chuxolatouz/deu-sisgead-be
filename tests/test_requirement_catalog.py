from types import SimpleNamespace

from bson import ObjectId

from api import create_app
from api.routes import requirements as requirements_routes
from api.routes.documents import _apply_item_requirement
from api.services import requirement_service
from api.services.accounting_service import AccountCatalogService as AccountingAccountCatalogService
from api.services.requirement_service import RequirementCatalogService


class CollectionStub:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find_one(self, query, projection=None):
        for row in self.rows:
            matches = True
            for key, value in query.items():
                current = row.get(key)
                if isinstance(value, dict) and "$ne" in value:
                    matches = matches and current != value["$ne"]
                else:
                    matches = matches and current == value
            if matches:
                if projection and projection.get("_id") == 0:
                    return {key: value for key, value in row.items() if key != "_id"}
                return dict(row)
        return None

    def find(self, query, projection=None):
        rows = []
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                if projection and projection.get("_id") == 0:
                    rows.append({key: value for key, value in row.items() if key != "_id"})
                else:
                    rows.append(dict(row))
        return rows

    def create_index(self, *_args, **_kwargs):
        return "idx"

    def insert_one(self, document):
        inserted_id = ObjectId()
        self.rows.append({**document, "_id": inserted_id})
        return SimpleNamespace(inserted_id=inserted_id)

    def update_one(self, query, update):
        target = self.find_one(query)
        if target:
            for row in self.rows:
                if row.get("_id") == target.get("_id"):
                    row.update(update.get("$set", {}))
        return SimpleNamespace(modified_count=1 if target else 0)


class MongoStub:
    def __init__(self, *, requirements=None, accounts=None):
        self.db = SimpleNamespace(
            requirement_catalog=CollectionStub(requirements),
            master_accounts=CollectionStub(accounts),
        )


def test_resuelve_requerimiento_para_proyecto_con_cuenta_del_anio(monkeypatch):
    requirement_id = ObjectId()
    mongo_stub = MongoStub(
        requirements=[{
            "_id": requirement_id,
            "nombre": "Alquiler de sonido",
            "descripcion": "Equipos para eventos",
            "accountCode": "401-01",
            "activo": True,
            "eliminado": False,
        }],
        accounts=[{
            "year": 2026,
            "code": "401-01",
            "description": "Alquileres",
            "group": "EGRESO",
            "is_header": False,
            "level": 4,
            "parent_code": "401",
        }],
    )
    monkeypatch.setattr(requirement_service, "mongo", mongo_stub)

    resolved, error = RequirementCatalogService.resolve_for_project(
        [str(requirement_id)],
        year=2026,
    )

    assert error is None
    assert resolved[0]["requirementId"] == str(requirement_id)
    assert resolved[0]["accountCode"] == "401-01"
    assert resolved[0]["account"]["level"] == 4
    assert resolved[0]["account"]["isHeader"] is False


def test_cuenta_titular_acepta_solo_descendiente_detalle(monkeypatch):
    accounts = [
        {"year": 2026, "code": "401", "is_header": True, "parent_code": None},
        {"year": 2026, "code": "401-01", "is_header": True, "parent_code": "401"},
        {"year": 2026, "code": "401-01-01", "is_header": False, "parent_code": "401-01"},
        {"year": 2026, "code": "402-01-01", "is_header": False, "parent_code": "402"},
    ]
    monkeypatch.setattr(requirement_service, "mongo", MongoStub(accounts=accounts))
    requirement = {
        "accountCode": "401",
        "account": {"isHeader": True, "level": 1},
    }

    valid, error = RequirementCatalogService.account_matches_requirement(
        requirement,
        "401-01-01",
        2026,
    )
    invalid, invalid_error = RequirementCatalogService.account_matches_requirement(
        requirement,
        "402-01-01",
        2026,
    )

    assert valid is True
    assert error is None
    assert invalid is False
    assert "no pertenece" in invalid_error


def test_busqueda_jerarquica_obtiene_todos_los_descendientes(monkeypatch):
    accounts = [
        {"year": 2026, "code": "401", "parent_code": None},
        {"year": 2026, "code": "401-01", "parent_code": "401"},
        {"year": 2026, "code": "401-01-01", "parent_code": "401-01"},
        {"year": 2026, "code": "402", "parent_code": None},
    ]
    mongo_stub = MongoStub(accounts=accounts)
    from api.services import accounting_service

    monkeypatch.setattr(accounting_service, "mongo", mongo_stub)

    descendants = AccountingAccountCatalogService.descendant_codes(2026, "401")

    assert descendants == {"401-01", "401-01-01"}


def test_item_toma_automaticamente_cuenta_detalle_del_requerimiento(monkeypatch):
    monkeypatch.setattr(
        requirement_service,
        "mongo",
        MongoStub(accounts=[{
            "year": 2026,
            "code": "401-01",
            "is_header": False,
            "parent_code": "401",
        }]),
    )
    requirement_id = str(ObjectId())
    documento = {
        "patrocinada": False,
        "requerimientos": [{
            "requirementId": requirement_id,
            "nombre": "Sonido",
            "descripcion": "Equipo de audio",
            "accountCode": "401-01",
            "account": {"isHeader": False, "level": 4},
        }],
    }
    item = {
        "requirementId": requirement_id,
        "nombre": "",
        "descripcion": "",
        "accountCode": "",
        "cuenta_contable": "",
    }

    error = _apply_item_requirement(item, documento, {}, 2026)

    assert error is None
    assert item["accountCode"] == "401-01"
    assert item["cuenta_contable"] == "401-01"
    assert item["nombre"] == "Sonido"
    assert item["requirementName"] == "Sonido"


def test_crud_crea_requerimiento_con_cuenta_y_nivel(monkeypatch):
    mongo_stub = MongoStub(accounts=[{
        "year": 2026,
        "code": "401",
        "description": "Servicios generales",
        "group": "EGRESO",
        "is_header": True,
        "level": 2,
        "parent_code": "4",
    }])
    monkeypatch.setattr(requirement_service, "mongo", mongo_stub)
    app = create_app()

    with app.test_request_context(
        "/requerimientos",
        method="POST",
        json={
            "nombre": "Sonido",
            "descripcion": "Equipos para actividades",
            "accountCode": "401",
            "accountReferenceYear": 2026,
        },
    ):
        response, status = requirements_routes.crear_requerimiento.__wrapped__.__wrapped__(
            {"role": "super_admin"}
        )

    assert status == 201
    payload = response.get_json()["requirement"]
    assert payload["nombre"] == "Sonido"
    assert payload["accountCode"] == "401"
    assert payload["account"]["isHeader"] is True
    assert payload["account"]["level"] == 2
