from bson import ObjectId

from api.index import app
from api.routes import projects as projects_module


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query):
        for doc in self.docs:
            match = True
            for key, expected in query.items():
                value = doc.get(key)
                if isinstance(value, ObjectId) and isinstance(expected, ObjectId):
                    if value != expected:
                        match = False
                        break
                elif value != expected:
                    match = False
                    break
            if match:
                return doc
        return None

    def find(self, query):
        result = []
        for doc in self.docs:
            match = True
            for key, expected in query.items():
                value = doc.get(key)
                if isinstance(value, ObjectId) and isinstance(expected, ObjectId):
                    if value != expected:
                        match = False
                        break
                elif value != expected:
                    match = False
                    break
            if match:
                result.append(doc)
        return result


class FakeDB:
    def __init__(self, project_id, dept_id):
        self.proyectos = FakeCollection([
            {
                "_id": project_id,
                "nombre": "Proyecto Prueba",
                "descripcion": "Descripcion de prueba",
                "objetivo_general": "Objetivo de prueba",
                "objetivos_especificos": ["Obj 1", "Obj 2"],
                "departamento_id": dept_id,
            }
        ])
        self.departamentos = FakeCollection([
            {"_id": dept_id, "nombre": "Departamento QA"}
        ])
        self.documentos = FakeCollection([
            {
                "project_id": project_id,
                "cuenta_contable": "4.01.03",
                "descripcion": "Repuesto",
                "monto": 150000,
            }
        ])


class FakeMongo:
    def __init__(self, project_id, dept_id):
        self.db = FakeDB(project_id, dept_id)


def test_descargar_acta_inicio_pdf_happy_path(monkeypatch):
    project_id = ObjectId()
    dept_id = ObjectId()

    monkeypatch.setattr(projects_module, "mongo", FakeMongo(project_id, dept_id))
    monkeypatch.setattr(projects_module, "generar_acta_inicio_pdf", lambda *args, **kwargs: b"%PDF-FAKE-ACTA")

    client = app.test_client()
    response = client.get(f"/proyecto/{project_id}/acta_inicio.pdf")

    assert response.status_code == 200
    assert response.headers.get("Content-Type", "").startswith("application/pdf")
    assert response.data.startswith(b"%PDF")


def test_descargar_informe_actividad_pdf_happy_path(monkeypatch):
    project_id = ObjectId()
    dept_id = ObjectId()

    monkeypatch.setattr(projects_module, "mongo", FakeMongo(project_id, dept_id))
    monkeypatch.setattr(projects_module, "generar_informe_actividad_pdf", lambda *args, **kwargs: b"%PDF-FAKE-INFORME")

    client = app.test_client()
    response = client.get(
        f"/proyecto/{project_id}/informe_actividad.pdf?nombre_actividad=Visita+tecnica&ubicacion=UCV"
    )

    assert response.status_code == 200
    assert response.headers.get("Content-Type", "").startswith("application/pdf")
    assert response.data.startswith(b"%PDF")
