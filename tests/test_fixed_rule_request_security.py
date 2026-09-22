from types import SimpleNamespace

from bson import ObjectId

from api.index import app
from api.routes import rules as rules_routes


class RequestsCollection:
    def __init__(self):
        self.deleted_query = None
        self.updated_query = None
        self.updated_value = None
        self.inserted = None

    def delete_one(self, query):
        self.deleted_query = query
        return SimpleNamespace(deleted_count=1)

    def update_one(self, query, update):
        self.updated_query = query
        self.updated_value = update
        return SimpleNamespace(modified_count=1)

    def insert_one(self, document):
        self.inserted = document
        return SimpleNamespace(inserted_id=ObjectId())


def _mongo_with_requests(collection):
    return SimpleNamespace(db=SimpleNamespace(solicitudes=collection))


def _super_admin():
    return {"sub": str(ObjectId()), "nombre": "Administrador", "role": "super_admin"}


def test_fixed_rule_mutations_require_token():
    request_id = ObjectId()
    client = app.test_client()

    delete_response = client.post(f"/eliminar_solicitud_regla_fija/{request_id}")
    resolve_response = client.post(
        f"/completar_solicitud_regla_fija/{request_id}",
        json={"resolution": "completed"},
    )

    assert delete_response.status_code == 403
    assert resolve_response.status_code == 403


def test_only_super_admin_can_resolve_fixed_rule_request():
    actor = {"sub": str(ObjectId()), "nombre": "Usuario", "role": "usuario"}

    with app.test_request_context(json={"resolution": "completed"}):
        response, status = rules_routes.completar_solicitud_regla_fija.__wrapped__.__wrapped__(
            actor,
            str(ObjectId()),
        )

    assert status == 403
    assert "super_admin" in response.get_json()["message"]


def test_resolve_fixed_rule_request_validates_id_and_resolution(monkeypatch):
    requests = RequestsCollection()
    monkeypatch.setattr(rules_routes, "mongo", _mongo_with_requests(requests))

    with app.test_request_context(json={"resolution": "completed"}):
        invalid_id_response, invalid_id_status = (
            rules_routes.completar_solicitud_regla_fija.__wrapped__.__wrapped__(
                _super_admin(),
                "invalid-id",
            )
        )

    with app.test_request_context(json={"resolution": "unknown"}):
        invalid_resolution_response, invalid_resolution_status = (
            rules_routes.completar_solicitud_regla_fija.__wrapped__.__wrapped__(
                _super_admin(),
                str(ObjectId()),
            )
        )

    assert invalid_id_status == 400
    assert invalid_id_response.get_json()["message"] == "ID de solicitud inválido"
    assert invalid_resolution_status == 400
    assert "completed o rejected" in invalid_resolution_response.get_json()["message"]


def test_resolve_fixed_rule_request_normalizes_status(monkeypatch):
    requests = RequestsCollection()
    monkeypatch.setattr(rules_routes, "mongo", _mongo_with_requests(requests))
    request_id = ObjectId()

    with app.test_request_context(json={"resolution": "Rejected"}):
        response, status = rules_routes.completar_solicitud_regla_fija.__wrapped__.__wrapped__(
            _super_admin(),
            str(request_id),
        )

    assert status == 200
    assert response.get_json()["message"] == "Solicitud de regla actualizada con éxito"
    assert requests.updated_query == {"_id": request_id}
    assert requests.updated_value == {"$set": {"status": "rejected"}}


def test_create_fixed_rule_request_rejects_incomplete_payload(monkeypatch):
    requests = RequestsCollection()
    monkeypatch.setattr(rules_routes, "mongo", _mongo_with_requests(requests))

    with app.test_request_context(json={"name": "Regla sin items", "items": []}):
        response, status = rules_routes.crear_solicitud_regla_fija.__wrapped__(_super_admin())

    assert status == 400
    assert "items" in response.get_json()["message"]
    assert requests.inserted is None
