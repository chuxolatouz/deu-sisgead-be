from types import SimpleNamespace

import pytest
from bson import ObjectId

from api.index import app
from api.routes import projects as projects_routes
from api.routes import users as users_routes


class RolesCollection:
    def __init__(self, roles=None):
        self.roles = roles or []

    def find(self, _query):
        return list(self.roles)


class ProjectCollection:
    def __init__(self, project, modified_count=1):
        self.project = project
        self.modified_count = modified_count
        self.last_update = None
        self.delete_called = False

    def find_one(self, query):
        if query.get("_id") == self.project.get("_id"):
            return self.project
        return None

    def update_one(self, query, update):
        self.last_update = (query, update)
        return SimpleNamespace(modified_count=self.modified_count)

    def delete_one(self, _query):
        self.delete_called = True
        return SimpleNamespace(deleted_count=1)


class UserCollection:
    def __init__(self, user):
        self.user = user

    def find_one(self, query):
        if query.get("_id") == self.user.get("_id"):
            return self.user
        return None


def test_roles_returns_project_defaults_when_catalog_is_empty(monkeypatch):
    fake_mongo = SimpleNamespace(db=SimpleNamespace(roles=RolesCollection()))
    monkeypatch.setattr(users_routes, "mongo", fake_mongo)

    response = app.test_client().get("/roles")

    assert response.status_code == 200
    assert response.get_json() == [
        {"label": "Líder", "value": "lider"},
        {"label": "Miembro", "value": "miembro"},
    ]


@pytest.mark.parametrize("project_id_key", ["projectId", "project_id", "proyecto_id"])
def test_assign_member_accepts_all_project_id_aliases(monkeypatch, project_id_key):
    project_id = ObjectId()
    department_id = ObjectId()
    user_id = ObjectId()
    project = {
        "_id": project_id,
        "departamento_id": department_id,
        "miembros": [],
        "status": {"actual": 1, "completado": []},
    }
    target_user = {
        "_id": user_id,
        "nombre": "Usuario real",
        "email": "usuario@example.test",
        "password": "hash-secreto",
        "rol": "usuario",
        "departamento_id": department_id,
    }
    projects = ProjectCollection(project)
    fake_mongo = SimpleNamespace(
        db=SimpleNamespace(
            proyectos=projects,
            usuarios=UserCollection(target_user),
        )
    )
    monkeypatch.setattr(projects_routes, "mongo", fake_mongo)
    monkeypatch.setattr(projects_routes, "agregar_log", lambda *_args, **_kwargs: None)

    payload = {
        project_id_key: str(project_id),
        "user": {"_id": {"$oid": str(user_id)}, "nombre": "Nombre alterado"},
        "role": {"value": "miembro", "label": "Miembro"},
    }
    actor = {"sub": str(ObjectId()), "nombre": "Administrador", "role": "super_admin"}

    with app.test_request_context(json=payload):
        response, status = projects_routes.asignar_usuario_proyecto.__wrapped__.__wrapped__(actor)

    assert status == 200
    assert response.get_json()["message"] == "Usuario asignado al proyecto con éxito"
    pushed_member = projects.last_update[1]["$push"]["miembros"]
    assert pushed_member["usuario"]["nombre"] == "Usuario real"
    assert "password" not in pushed_member["usuario"]
    assert pushed_member["role"] == {"value": "miembro", "label": "Miembro"}


def test_assign_member_reports_missing_project_id_explicitly():
    payload = {
        "user": {"_id": {"$oid": str(ObjectId())}},
        "role": {"value": "miembro", "label": "Miembro"},
    }
    actor = {"sub": str(ObjectId()), "nombre": "Administrador", "role": "super_admin"}

    with app.test_request_context(json=payload):
        response, status = projects_routes.asignar_usuario_proyecto.__wrapped__.__wrapped__(actor)

    assert status == 400
    assert "projectId es requerido" in response.get_json()["message"]


def test_assign_member_rejects_incomplete_role_without_internal_error():
    payload = {
        "projectId": str(ObjectId()),
        "user": {"_id": {"$oid": str(ObjectId())}},
        "role": {},
    }
    actor = {"sub": str(ObjectId()), "nombre": "Administrador", "role": "super_admin"}

    with app.test_request_context(json=payload):
        response, status = projects_routes.asignar_usuario_proyecto.__wrapped__.__wrapped__(actor)

    assert status == 400
    assert response.get_json()["message"] == "role debe incluir value y label válidos"


@pytest.mark.parametrize("stored_user_id_factory", [lambda value: value, lambda value: {"$oid": str(value)}])
def test_remove_member_uses_the_stored_user_id_shape(monkeypatch, stored_user_id_factory):
    project_id = ObjectId()
    user_id = ObjectId()
    stored_user_id = stored_user_id_factory(user_id)
    project = {
        "_id": project_id,
        "miembros": [
            {
                "usuario": {"_id": stored_user_id, "nombre": "Miembro"},
                "role": {"value": "miembro", "label": "Miembro"},
            }
        ],
    }
    projects = ProjectCollection(project)
    monkeypatch.setattr(
        projects_routes,
        "mongo",
        SimpleNamespace(db=SimpleNamespace(proyectos=projects)),
    )
    monkeypatch.setattr(projects_routes, "agregar_log", lambda *_args, **_kwargs: None)
    actor = {"sub": str(ObjectId()), "nombre": "Administrador", "role": "super_admin"}

    with app.test_request_context(json={"projectId": str(project_id), "userId": str(user_id)}):
        response, status = projects_routes.eliminar_usuario_proyecto.__wrapped__.__wrapped__(actor)

    assert status == 200
    assert response.get_json()["message"] == "Usuario eliminado del proyecto con éxito"
    assert projects.last_update[1]["$pull"]["miembros"]["usuario._id"] == stored_user_id


def test_remove_member_reports_when_database_did_not_modify_project(monkeypatch):
    project_id = ObjectId()
    user_id = ObjectId()
    project = {
        "_id": project_id,
        "miembros": [{"usuario": {"_id": user_id, "nombre": "Miembro"}}],
    }
    projects = ProjectCollection(project, modified_count=0)
    monkeypatch.setattr(
        projects_routes,
        "mongo",
        SimpleNamespace(db=SimpleNamespace(proyectos=projects)),
    )
    monkeypatch.setattr(projects_routes, "agregar_log", lambda *_args, **_kwargs: None)
    actor = {"sub": str(ObjectId()), "nombre": "Administrador", "role": "super_admin"}

    with app.test_request_context(json={"projectId": str(project_id), "userId": str(user_id)}):
        response, status = projects_routes.eliminar_usuario_proyecto.__wrapped__.__wrapped__(actor)

    assert status == 409
    assert response.get_json()["message"] == "No se pudo eliminar el usuario del proyecto"


def test_department_user_cannot_delete_project_they_do_not_own(monkeypatch):
    project_id = ObjectId()
    department_id = ObjectId()
    projects = ProjectCollection(
        {
            "_id": project_id,
            "owner": ObjectId(),
            "departamento_id": department_id,
        }
    )
    monkeypatch.setattr(
        projects_routes,
        "mongo",
        SimpleNamespace(db=SimpleNamespace(proyectos=projects)),
    )
    actor = {
        "sub": str(ObjectId()),
        "nombre": "Usuario",
        "role": "usuario",
        "departmentId": str(department_id),
    }

    with app.test_request_context(json={"projectId": str(project_id)}):
        response, status = projects_routes.eliminar_proyecto.__wrapped__.__wrapped__(actor)

    assert status == 403
    assert "propietario" in response.get_json()["message"]
    assert projects.delete_called is False
