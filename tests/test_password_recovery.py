import re
from types import SimpleNamespace

from api import create_app
from api.routes import auth as auth_routes


class UsersCollection:
    def __init__(self):
        self.rows = []

    def find_one(self, query):
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                return dict(row)
        return None

    def update_one(self, query, update):
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                row.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


class MongoStub:
    def __init__(self):
        self.db = SimpleNamespace(usuarios=UsersCollection())


def test_password_recovery_generates_temporary_password_and_forces_change(monkeypatch):
    app = create_app()
    mongo_stub = MongoStub()
    sent = {}
    monkeypatch.setattr(auth_routes, "mongo", mongo_stub)
    monkeypatch.setattr(
        auth_routes,
        "send_email_notification_thread",
        lambda **kwargs: sent.update(kwargs),
    )

    user = {
        "_id": "user-1",
        "nombre": "Usuario",
        "email": "usuario@example.com",
        "password": auth_routes.bcrypt.generate_password_hash("anterior").decode("utf-8"),
        "rol": "usuario",
    }
    mongo_stub.db.usuarios.rows.append(user)

    with app.test_request_context(
        "/olvido_contraseña",
        method="POST",
        json={"email": "usuario@example.com"},
    ):
        response, status_code = auth_routes.olvido_contraseña()

    assert status_code == 200
    stored = mongo_stub.db.usuarios.rows[0]
    assert stored["mustChangePassword"] is True
    assert stored["temporaryPasswordExpiresAt"] is not None
    assert sent["recipient"] == "usuario@example.com"

    match = re.search(r"Contraseña temporal:</strong>\s*([^<]+)", sent["body"])
    assert match
    temporary_password = match.group(1).strip()

    with app.test_request_context(
        "/login",
        method="POST",
        json={"email": "usuario@example.com", "password": temporary_password},
    ):
        response, status_code = auth_routes.login()

    assert status_code == 200
    payload = response.get_json()
    assert payload["mustChangePassword"] is True

    token = payload["token"]
    with app.test_request_context(
        "/change-password",
        method="POST",
        json={"currentPassword": temporary_password, "newPassword": "nueva123"},
        headers={"Authorization": f"Bearer {token}"},
    ):
        response, status_code = auth_routes.change_password()

    assert status_code == 200
    stored = mongo_stub.db.usuarios.rows[0]
    assert stored["mustChangePassword"] is False
    assert auth_routes.bcrypt.check_password_hash(stored["password"], "nueva123")


def test_password_recovery_unknown_email_returns_404(monkeypatch):
    app = create_app()
    mongo_stub = MongoStub()
    monkeypatch.setattr(auth_routes, "mongo", mongo_stub)

    with app.test_request_context(
        "/olvido_contraseña",
        method="POST",
        json={"email": "nadie@example.com"},
    ):
        response, status_code = auth_routes.olvido_contraseña()

    assert status_code == 404
    assert response.get_json()["message"] == "El email electrónico no está registrado"
