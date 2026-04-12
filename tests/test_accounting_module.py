from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

from bson import ObjectId
from pymongo import UpdateOne

from api import create_app
from api.routes import accounting as accounting_routes
from api.routes import documents as documents_routes
from api.routes import projects as project_routes
from api.services import accounting_service
from api.services import project_funding_service
from api.services.accounting_service import AccountCatalogService, AccountScopeService, SeedService
from api.services.project_funding_service import ProjectFundingService


class BulkResult:
    def __init__(self, upserted_count=0, modified_count=0):
        self.upserted_count = upserted_count
        self.modified_count = modified_count


class InMemoryCollection:
    def __init__(self):
        self.rows = []

    def create_index(self, *args, **kwargs):
        return "idx"

    def _resolve_field(self, row, field):
        current = row
        for part in field.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _assign_field(self, row, field, value):
        if "." not in field:
            row[field] = value
            return
        current = row
        parts = field.split(".")
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value

    def _match_condition(self, current, expected):
        if isinstance(expected, dict):
            if "$in" in expected:
                return current in expected["$in"]
            if "$regex" in expected:
                import re
                pattern = expected["$regex"]
                flags = re.I if expected.get("$options") == "i" else 0
                return re.search(pattern, str(current or ""), flags) is not None
        return current == expected

    def _match(self, row, query):
        clauses = []
        if "$or" in query:
            clauses.append(any(self._match(row, branch) for branch in query["$or"]))
        if "$and" in query:
            clauses.append(all(self._match(row, branch) for branch in query["$and"]))

        for key, value in query.items():
            if key in {"$or", "$and"}:
                continue
            clauses.append(self._match_condition(self._resolve_field(row, key), value))

        return all(clauses) if clauses else True

    def bulk_write(self, ops, ordered=False):
        upserted = 0
        modified = 0
        for op in ops:
            assert isinstance(op, UpdateOne)
            filt = op._filter
            update = op._doc
            target = next((r for r in self.rows if self._match(r, filt)), None)
            if target is None and op._upsert:
                doc = dict(filt)
                doc.update(update.get("$setOnInsert", {}))
                doc.update(update.get("$set", {}))
                self.rows.append(doc)
                upserted += 1
            elif target is not None:
                target.update(update.get("$set", {}))
                modified += 1
        return BulkResult(upserted_count=upserted, modified_count=modified)

    def delete_many(self, query):
        self.rows = [r for r in self.rows if not self._match(r, query)]

    def find_one(self, query, projection=None):
        for row in self.rows:
            if self._match(row, query):
                if projection:
                    if all(v in (0, False) for v in projection.values()):
                        excluded = {k for k, v in projection.items() if v in (0, False)}
                        return {k: v for k, v in row.items() if k not in excluded}
                    return {k: row.get(k) for k, v in projection.items() if v}
                return dict(row)
        return None

    def find(self, query, projection=None):
        out = [r for r in self.rows if self._match(r, query)]
        if projection:
            if all(v in (0, False) for v in projection.values()):
                excluded = {k for k, v in projection.items() if v in (0, False)}
                out = [{k: v for k, v in r.items() if k not in excluded} for r in out]
            else:
                out = [{k: r.get(k) for k, v in projection.items() if v} for r in out]

        class _Cursor(list):
            def sort(self, *args, **kwargs):
                return self

            def limit(self, *_args, **_kwargs):
                return self

            def skip(self, count):
                return _Cursor(self[count:])

        return _Cursor(out)

    def update_one(self, query, update, upsert=False, session=None):
        row = next((r for r in self.rows if self._match(r, query)), None)
        if row is None:
            if not upsert:
                return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)
            row = dict(query)
            row.update(update.get("$setOnInsert", {}))
            self.rows.append(row)
            matched_count = 0
            modified_count = 0
            upserted_id = ObjectId()
        else:
            matched_count = 1
            modified_count = 1
            upserted_id = None
        for k, v in update.get("$set", {}).items():
            self._assign_field(row, k, v)
        for k, v in update.get("$inc", {}).items():
            current = self._resolve_field(row, k) or 0
            self._assign_field(row, k, current + v)
        return SimpleNamespace(
            matched_count=matched_count,
            modified_count=modified_count,
            upserted_id=upserted_id,
        )

    def count_documents(self, query):
        return len([r for r in self.rows if self._match(r, query)])

    def insert_one(self, doc, session=None):
        self.rows.append(dict(doc))
        return SimpleNamespace(inserted_id=ObjectId())

    def insert_many(self, docs, session=None):
        for doc in docs:
            self.rows.append(dict(doc))
        return SimpleNamespace(inserted_ids=[ObjectId() for _ in docs])

    def aggregate(self, pipeline):
        return []


class InMemoryDB:
    def __init__(self):
        self.master_accounts = InMemoryCollection()
        self.master_units = InMemoryCollection()
        self.master_funding_sources = InMemoryCollection()
        self.master_budget_categories = InMemoryCollection()
        self.account_scope_state = InMemoryCollection()
        self.ledger_movements = InMemoryCollection()
        self.proyectos = InMemoryCollection()
        self.documentos = InMemoryCollection()
        self.departamentos = InMemoryCollection()
        self.logs = InMemoryCollection()
        self.acciones = InMemoryCollection()


class MongoStub:
    def __init__(self):
        self.db = InMemoryDB()

        class _Client:
            def start_session(self):
                raise RuntimeError("no sessions in test")

        self.cx = _Client()


def _flatten_tree(nodes):
    out = []
    for node in nodes:
        out.append(node)
        out.extend(_flatten_tree(node.get("children", [])))
    return out


def test_seed_idempotente(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(accounting_service, "mongo", mongo_stub)

    service = SeedService(base_dir="/Users/MacBook/Develop/deu-sisgead/deu-sisgead-be")
    monkeypatch.setattr(service, "_ensure_local_data_files", lambda: None)
    monkeypatch.setattr(service, "_load_accounts", lambda: [{"code": "100", "description": "A", "group": "INGRESO", "is_header": False, "level": 1, "parent_code": None}])
    monkeypatch.setattr(service, "_load_units", lambda: [{"code": "460", "description": "U", "level": 1, "parent_code": None}])
    monkeypatch.setattr(service, "_load_funding_sources", lambda: [{"code": "11", "description": "F"}])
    monkeypatch.setattr(service, "_load_budget_categories", lambda: [{"code": "AC", "description": "C"}])

    first = service.seed(year=2025)
    second = service.seed(year=2025)

    assert first["counts"]["accounts"] >= 1
    assert second["counts"]["accounts"] >= 1
    assert len(mongo_stub.db.master_accounts.rows) == 1


def test_crear_movimiento_actualiza_balance(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(accounting_service, "mongo", mongo_stub)

    mongo_stub.db.master_accounts.rows.append(
        {
            "year": 2025,
            "code": "401010100000",
            "description": "Cuenta detalle",
            "group": "EGRESO",
            "is_header": False,
            "level": 4,
            "parent_code": "401010000000",
        }
    )

    result = AccountScopeService.create_movement(
        year=2025,
        scope_type="department",
        scope_id="dep-1",
        account_code="401010100000",
        movement_type="debit",
        amount=100,
        description="Prueba",
        reference={"kind": "manual", "id": "1"},
        created_by="user-1",
        allow_negative=True,
    )

    assert result["state"]["balance"] == 100
    assert result["state"]["movementsCount"] == 1
    assert len(mongo_stub.db.ledger_movements.rows) == 1


def test_rbac_department_basico():
    user_admin = {"role": "super_admin"}
    user_dep_ok = {"role": "admin_departamento", "departamento_id": "dep-1"}
    user_dep_bad = {"role": "admin_departamento", "departamento_id": "dep-2"}

    assert accounting_routes._can_access_department(user_admin, "dep-1") is True
    assert accounting_routes._can_access_department(user_dep_ok, "dep-1") is True
    assert accounting_routes._can_access_department(user_dep_bad, "dep-1") is False


def test_mostrar_proyectos_incluye_metadata_departamento(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(project_routes, "mongo", mongo_stub)
    monkeypatch.setattr(project_funding_service, "mongo", mongo_stub)
    monkeypatch.setattr(accounting_service, "mongo", mongo_stub)

    department_id = ObjectId()
    project_id = ObjectId()

    mongo_stub.db.departamentos.rows.append(
        {
            "_id": department_id,
            "codigo": "DEP-01",
            "nombre": "Planificacion",
        }
    )
    mongo_stub.db.proyectos.rows.append(
        {
            "_id": project_id,
            "nombre": "Proyecto de prueba",
            "descripcion": "Descripcion",
            "fecha_inicio": "2026-01-01",
            "fecha_fin": "2026-12-31",
            "departamento_id": department_id,
            "balance": 0,
            "balance_inicial": 0,
            "status": {"actual": 1, "completado": []},
        }
    )

    app = create_app()
    with app.test_request_context("/mostrar_proyectos?page=0&limit=10"):
        response = project_routes.mostrar_proyectos.__wrapped__.__wrapped__({"role": "super_admin"})

    payload = response.get_json()
    assert payload["count"] == 1
    assert len(payload["request_list"]) == 1

    project = payload["request_list"][0]
    assert project["departmentId"] == str(department_id)
    assert project["departmentName"] == "Planificacion"
    assert project["departmentCode"] == "DEP-01"
    assert project["departamento"]["_id"] == str(department_id)
    assert project["departamento"]["nombre"] == "Planificacion"


def test_mostrar_proyectos_usa_anio_actual_para_balance(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(project_routes, "mongo", mongo_stub)
    monkeypatch.setattr(project_funding_service, "mongo", mongo_stub)
    monkeypatch.setattr(accounting_service, "mongo", mongo_stub)

    current_year = datetime.now(timezone.utc).year
    project_id = ObjectId()

    mongo_stub.db.master_accounts.rows.append(
        {
            "year": current_year,
            "code": "401010100000",
            "description": "Cuenta detalle",
            "group": "EGRESO",
            "is_header": False,
            "level": 4,
            "parent_code": "401010000000",
        }
    )
    mongo_stub.db.account_scope_state.rows.append(
        {
            "year": current_year,
            "scopeType": "project",
            "scopeId": str(project_id),
            "accountCode": "401010100000",
            "balance": 250.0,
            "movementsCount": 1,
            "lastMovementAt": datetime.now(timezone.utc),
        }
    )
    mongo_stub.db.proyectos.rows.append(
        {
            "_id": project_id,
            "nombre": "Proyecto con saldo",
            "descripcion": "Descripcion",
            "fecha_inicio": "2026-01-01",
            "fecha_fin": "2026-12-31",
            "balance": 0,
            "balance_inicial": 0,
            "status": {"actual": 1, "completado": []},
        }
    )

    app = create_app()
    with app.test_request_context("/mostrar_proyectos?page=0&limit=10"):
        response = project_routes.mostrar_proyectos.__wrapped__.__wrapped__({"role": "super_admin"})

    payload = response.get_json()
    assert payload["count"] == 1
    project = payload["request_list"][0]
    assert project["fundingYear"] == current_year
    assert project["balance"] == 250.0
    assert project["fundingSummary"]["totals"]["currentAvailable"] == 250.0


def test_admin_accounts_income_type_is_required_and_persisted(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(accounting_routes, "mongo", mongo_stub)
    monkeypatch.setattr(accounting_service, "mongo", mongo_stub)

    app = create_app()

    with app.test_request_context(
        "/api/admin/accounts",
        method="POST",
        json={
            "year": 2025,
            "code": "401010100000",
            "description": "Cuenta detalle",
            "group": "EGRESO",
            "is_header": False,
            "level": 4,
            "parent_code": "401010000000",
        },
    ):
        response, status_code = accounting_routes.admin_create_account.__wrapped__.__wrapped__({"role": "super_admin"})

    assert status_code == 400
    assert response.get_json()["message"] == "incomeType es requerido"

    with app.test_request_context(
        "/api/admin/accounts",
        method="POST",
        json={
            "year": 2025,
            "code": "401010100000",
            "description": "Cuenta detalle",
            "group": "EGRESO",
            "is_header": False,
            "level": 4,
            "parent_code": "401010000000",
            "incomeType": "ordinary",
        },
    ):
        response, status_code = accounting_routes.admin_create_account.__wrapped__.__wrapped__({"role": "super_admin"})

    assert status_code == 201
    created = mongo_stub.db.master_accounts.find_one({"year": 2025, "code": "401010100000"})
    assert created["incomeType"] == "ordinary"

    with app.test_request_context(
        "/api/admin/accounts/401010100000?year=2025",
        method="PUT",
        json={"incomeType": "own"},
    ):
        response, status_code = accounting_routes.admin_update_account.__wrapped__.__wrapped__(
            {"role": "super_admin"},
            "401010100000",
        )

    assert status_code == 200

    with app.test_request_context("/api/admin/accounts?year=2025&page=0&limit=20"):
        response, status_code = accounting_routes.admin_list_accounts.__wrapped__.__wrapped__({"role": "super_admin"})

    assert status_code == 200
    payload = response.get_json()
    assert payload["request_list"][0]["incomeType"] == "own"


def test_account_catalog_outputs_income_type_and_legacy_null(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(accounting_service, "mongo", mongo_stub)

    mongo_stub.db.master_accounts.rows.extend(
        [
            {
                "year": 2025,
                "code": "100000000000",
                "description": "Raiz ordinaria",
                "group": "INGRESO",
                "is_header": True,
                "level": 1,
                "parent_code": None,
                "incomeType": "ordinary",
            },
            {
                "year": 2025,
                "code": "100100000000",
                "description": "Cuenta legado",
                "group": "INGRESO",
                "is_header": False,
                "level": 2,
                "parent_code": "100000000000",
            },
        ]
    )
    mongo_stub.db.account_scope_state.rows.append(
        {
            "year": 2025,
            "scopeType": "project",
            "scopeId": "proj-1",
            "accountCode": "100100000000",
            "balance": 20.0,
            "movementsCount": 1,
            "lastMovementAt": None,
        }
    )

    search_rows = AccountCatalogService.search(year=2025, group="INGRESO")
    by_code = {row["code"]: row for row in search_rows}
    assert by_code["100000000000"]["incomeType"] == "ordinary"
    assert by_code["100100000000"]["incomeType"] is None

    tree = AccountCatalogService.tree(year=2025, group="INGRESO")
    flat_tree = _flatten_tree(tree)
    tree_by_code = {row["code"]: row for row in flat_tree}
    assert tree_by_code["100000000000"]["incomeType"] == "ordinary"
    assert tree_by_code["100100000000"]["incomeType"] is None

    scoped = AccountScopeService.get_scope_accounts(
        year=2025,
        scope_type="project",
        scope_id="proj-1",
        assigned_only=True,
    )
    scoped_flat = _flatten_tree(scoped["tree"])
    scoped_by_code = {row["code"]: row for row in scoped_flat}
    assert scoped_by_code["100100000000"]["incomeType"] is None


def test_mostrar_documentos_resultados_expone_aliases_y_filtro(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(documents_routes, "mongo", mongo_stub)
    monkeypatch.setattr(documents_routes, "can_access_project", lambda *_args, **_kwargs: True)

    project_id = ObjectId()
    finished_doc_id = ObjectId()

    mongo_stub.db.proyectos.rows.append({"_id": project_id, "departamento_id": ObjectId()})
    mongo_stub.db.documentos.rows.extend(
        [
            {
                "_id": finished_doc_id,
                "project_id": project_id,
                "descripcion": "Actividad finalizada",
                "status": "finished",
                "description": "Resultado final",
                "logros": "Logro 1",
                "lineas_accion": "Seguir trabajando",
                "archivos_aprobado": [{"nombre": "resultado.jpg", "ruta": "/tmp/resultado.jpg"}],
            },
            {
                "_id": ObjectId(),
                "project_id": project_id,
                "descripcion": "Actividad con cierre administrativo",
                "status": "in_progress",
            },
            {
                "_id": ObjectId(),
                "project_id": project_id,
                "descripcion": "Actividad nueva",
                "status": "new",
            },
        ]
    )

    app = create_app()
    with app.test_request_context(f"/proyecto/{project_id}/documentos?page=0&limit=10&status=finished"):
        response = documents_routes.mostrar_documentos_proyecto.__wrapped__.__wrapped__({"role": "super_admin"}, str(project_id))

    payload = response.get_json()
    assert payload["count"] == 1
    assert len(payload["request_list"]) == 1
    result = payload["request_list"][0]
    assert result["resultDescription"] == "Resultado final"
    assert result["resultados"] == "Resultado final"
    assert result["logros"] == "Logro 1"
    assert result["lineasAccion"] == "Seguir trabajando"
    assert result["resultAttachments"][0]["download_url"].endswith(f"/documentos/{finished_doc_id}/resultados/0")


def test_cerrar_presupuesto_registra_cierre_administrativo_y_restringe_permisos(monkeypatch, tmp_path):
    mongo_stub = MongoStub()
    consumed = {}
    monkeypatch.setattr(documents_routes, "mongo", mongo_stub)
    monkeypatch.setattr(documents_routes, "can_access_project", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        documents_routes.ProjectFundingService,
        "consume_project_account",
        lambda *args, **kwargs: consumed.update(kwargs),
    )
    monkeypatch.chdir(tmp_path)

    project_id = ObjectId()
    doc_id = ObjectId()
    mongo_stub.db.proyectos.rows.append(
        {"_id": project_id, "departamento_id": ObjectId(), "nombre": "Proyecto", "fundingYear": 2026}
    )
    mongo_stub.db.documentos.rows.append({"_id": doc_id, "project_id": project_id, "descripcion": "Actividad"})

    app = create_app()

    with app.test_request_context(
        "/documento_cerrar",
        method="POST",
        data={
            "projectId": str(project_id),
            "docId": str(doc_id),
            "monto": "1",
            "year": "2026",
            "referencia": "REF-01",
            "transferAmount": "1",
            "banco": "Banco prueba",
            "accountCode": "401010100000",
        },
        content_type="multipart/form-data",
    ):
        response, status_code = documents_routes.cerrar_presupuesto.__wrapped__.__wrapped__(
            {"role": "super_admin", "nombre": "Admin"}
        )

    assert status_code == 201
    stored = mongo_stub.db.documentos.find_one({"_id": doc_id})
    assert stored["status"] == "in_progress"
    assert stored["monto_aprobado"] == 100
    assert stored["accountCode"] == "401010100000"
    assert stored["referencia"] == "REF-01"
    assert stored["administrative_closed_at"] is not None
    assert consumed["year"] == 2026

    with app.test_request_context(
        "/documento_cerrar",
        method="POST",
        data={
            "projectId": str(project_id),
            "docId": str(doc_id),
            "monto": "1",
            "year": "2026",
            "accountCode": "401010100000",
        },
        content_type="multipart/form-data",
    ):
        response, status_code = documents_routes.cerrar_presupuesto.__wrapped__.__wrapped__(
            {"role": "usuario", "nombre": "Usuario"}
        )

    assert status_code == 403
    assert "cierre administrativo" in response.get_json()["message"]


def test_finalizar_actividad_acepta_imagenes_y_rechaza_archivos_invalidos(monkeypatch, tmp_path):
    mongo_stub = MongoStub()
    monkeypatch.setattr(documents_routes, "mongo", mongo_stub)
    monkeypatch.setattr(documents_routes, "can_access_project", lambda *_args, **_kwargs: True)
    monkeypatch.chdir(tmp_path)

    project_id = ObjectId()
    doc_id = ObjectId()
    mongo_stub.db.proyectos.rows.append({"_id": project_id, "departamento_id": ObjectId(), "nombre": "Proyecto"})
    mongo_stub.db.documentos.rows.append(
        {
            "_id": doc_id,
            "project_id": project_id,
            "descripcion": "Actividad",
            "status": "in_progress",
        }
    )

    app = create_app()

    with app.test_request_context(
        "/documento_finalizar",
        method="POST",
        data={
            "projectId": str(project_id),
            "docId": str(doc_id),
            "resultados": "Resultado con imagen",
            "logros": "Logro principal",
            "limitaciones": "Limitacion principal",
            "lecciones": "Leccion principal",
            "lineasAccion": "Siguiente paso",
            "files": (BytesIO(b"fake-image"), "evidencia.jpg"),
        },
        content_type="multipart/form-data",
    ):
        response, status_code = documents_routes.finalizar_actividad.__wrapped__.__wrapped__(
            {"role": "usuario", "nombre": "Admin"}
        )

    assert status_code == 201
    stored = mongo_stub.db.documentos.find_one({"_id": doc_id})
    assert stored["status"] == "finished"
    assert stored["resultados"] == "Resultado con imagen"
    assert stored["description"] == "Resultado con imagen"
    assert stored["logros"] == "Logro principal"
    assert stored["lineas_accion"] == "Siguiente paso"
    assert stored["archivos_aprobado"][0]["nombre"] == "evidencia.jpg"

    new_doc_id = ObjectId()
    mongo_stub.db.documentos.rows.append(
        {
            "_id": new_doc_id,
            "project_id": project_id,
            "descripcion": "Actividad nueva",
            "status": "new",
        }
    )

    with app.test_request_context(
        "/documento_finalizar",
        method="POST",
        data={
            "projectId": str(project_id),
            "docId": str(new_doc_id),
            "resultados": "Resultado invalido",
        },
        content_type="multipart/form-data",
    ):
        response, status_code = documents_routes.finalizar_actividad.__wrapped__.__wrapped__(
            {"role": "usuario", "nombre": "Admin"}
        )

    assert status_code == 400
    assert "cierre administrativo" in response.get_json()["error"]

    other_doc_id = ObjectId()
    mongo_stub.db.documentos.rows.append(
        {
            "_id": other_doc_id,
            "project_id": project_id,
            "descripcion": "Actividad 2",
            "status": "in_progress",
        }
    )

    with app.test_request_context(
        "/documento_finalizar",
        method="POST",
        data={
            "projectId": str(project_id),
            "docId": str(other_doc_id),
            "resultados": "Resultado con PDF",
            "files": (BytesIO(b"%PDF"), "evidencia.pdf"),
        },
        content_type="multipart/form-data",
    ):
        response, status_code = documents_routes.finalizar_actividad.__wrapped__.__wrapped__(
            {"role": "usuario", "nombre": "Admin"}
        )

    assert status_code == 400
    assert response.get_json()["error"] == "Solo se permiten imágenes PNG, GIF, JPEG o JPG en el cierre de actividad"


def test_transfer_between_accounts_actualiza_ambas(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(accounting_service, "mongo", mongo_stub)

    for code in ("401010100000", "401010200000"):
        mongo_stub.db.master_accounts.rows.append(
            {
                "year": 2025,
                "code": code,
                "description": "Cuenta detalle",
                "group": "EGRESO",
                "is_header": False,
                "level": 4,
                "parent_code": "401010000000",
            }
        )

    # saldo inicial en origen
    mongo_stub.db.account_scope_state.rows.append(
        {
            "year": 2025,
            "scopeType": "department",
            "scopeId": "dep-1",
            "accountCode": "401010100000",
            "balance": 500.0,
            "movementsCount": 1,
        }
    )

    result = AccountScopeService.transfer_between_accounts(
        year=2025,
        scope_type="department",
        scope_id="dep-1",
        from_account_code="401010100000",
        to_account_code="401010200000",
        amount=200.0,
        description="Transferencia interna",
        reference={"kind": "manual", "id": "T-1"},
        created_by="user-1",
        allow_negative=False,
    )

    assert result["sourceState"]["balance"] == 300.0
    assert result["targetState"]["balance"] == 200.0
    assert len(mongo_stub.db.ledger_movements.rows) == 2


def test_get_scope_accounts_assigned_only_include_zero_filters(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(accounting_service, "mongo", mongo_stub)

    mongo_stub.db.master_accounts.rows.extend(
        [
            {
                "year": 2025,
                "code": "100000000000",
                "description": "Raiz",
                "group": "EGRESO",
                "is_header": True,
                "level": 1,
                "parent_code": None,
            },
            {
                "year": 2025,
                "code": "100100000000",
                "description": "Sub raiz",
                "group": "EGRESO",
                "is_header": True,
                "level": 2,
                "parent_code": "100000000000",
            },
            {
                "year": 2025,
                "code": "100100100000",
                "description": "Detalle A",
                "group": "EGRESO",
                "is_header": False,
                "level": 3,
                "parent_code": "100100000000",
            },
            {
                "year": 2025,
                "code": "100100200000",
                "description": "Detalle B",
                "group": "EGRESO",
                "is_header": False,
                "level": 3,
                "parent_code": "100100000000",
            },
        ]
    )

    # Dos cuentas asignadas al scope: una con saldo y otra en 0.
    mongo_stub.db.account_scope_state.rows.extend(
        [
            {
                "year": 2025,
                "scopeType": "project",
                "scopeId": "proj-1",
                "accountCode": "100100100000",
                "balance": 300.0,
                "movementsCount": 2,
                "lastMovementAt": None,
            },
            {
                "year": 2025,
                "scopeType": "project",
                "scopeId": "proj-1",
                "accountCode": "100100200000",
                "balance": 0.0,
                "movementsCount": 0,
                "lastMovementAt": None,
            },
        ]
    )

    # assignedOnly + includeZero=false: solo no-cero + ancestros.
    payload_no_zero = AccountScopeService.get_scope_accounts(
        year=2025,
        scope_type="project",
        scope_id="proj-1",
        assigned_only=True,
        include_zero=False,
    )

    def _flatten(nodes):
        out = []
        for node in nodes:
            out.append(node)
            out.extend(_flatten(node.get("children", [])))
        return out

    flat_no_zero = _flatten(payload_no_zero["tree"])
    codes_no_zero = {row["code"] for row in flat_no_zero}

    assert "100100100000" in codes_no_zero
    assert "100100200000" not in codes_no_zero
    # Ancestros preservados para mantener jerarquía.
    assert "100100000000" in codes_no_zero
    assert "100000000000" in codes_no_zero
    assert payload_no_zero["meta"]["assignedOnly"] is True
    assert payload_no_zero["meta"]["includeZero"] is False
    assert payload_no_zero["meta"]["totalAssigned"] == 2

    # assignedOnly + includeZero=true: incluye asignadas en 0.
    payload_with_zero = AccountScopeService.get_scope_accounts(
        year=2025,
        scope_type="project",
        scope_id="proj-1",
        assigned_only=True,
        include_zero=True,
    )
    flat_with_zero = _flatten(payload_with_zero["tree"])
    codes_with_zero = {row["code"] for row in flat_with_zero}
    assert "100100100000" in codes_with_zero
    assert "100100200000" in codes_with_zero


def test_project_funding_summary_legacy_uses_snapshots(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(project_funding_service, "mongo", mongo_stub)

    project = {
        "_id": ObjectId(),
        "nombre": "Proyecto Legacy",
        "balance": 125000,
        "balance_inicial": 150000,
        "status": {"actual": 1, "completado": []},
    }
    mongo_stub.db.proyectos.rows.append(project)

    summary = ProjectFundingService.build_summary(project, year=2025, user={"role": "super_admin"})

    assert summary["model"]["status"] == "legacy"
    assert summary["model"]["migrationRequired"] is True
    assert summary["totals"]["currentAvailable"] == 1250.0
    assert summary["totals"]["initialAssigned"] == 1500.0


def test_project_funding_summary_uses_current_available_when_initial_missing(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(project_funding_service, "mongo", mongo_stub)

    project = {
        "_id": ObjectId(),
        "nombre": "Proyecto Active",
        "balance": 0,
        "balance_inicial": 0,
        "status": {"actual": 1, "completado": []},
        "fundingModel": {
            "version": 2,
            "status": "active",
            "configuredAt": None,
            "migratedAt": None,
            "migratedBy": None,
            "initialAssignedAmount": 0,
            "legacyCurrentBalanceSnapshot": None,
            "legacyInitialBalanceSnapshot": None,
            "migrationNote": None,
        },
    }
    mongo_stub.db.proyectos.rows.append(project)
    mongo_stub.db.master_accounts.rows.append(
        {
            "year": 2026,
            "code": "403109900000",
            "description": "Cuenta detalle",
            "group": "EGRESO",
            "is_header": False,
            "level": 4,
            "parent_code": "403100000000",
        }
    )
    mongo_stub.db.account_scope_state.rows.append(
        {
            "year": 2026,
            "scopeType": "project",
            "scopeId": str(project["_id"]),
            "accountCode": "403109900000",
            "balance": 300.0,
            "movementsCount": 1,
            "lastMovementAt": None,
        }
    )

    summary = ProjectFundingService.build_summary(project, year=2026, user={"role": "super_admin"})

    assert summary["totals"]["currentAvailable"] == 300.0
    assert summary["totals"]["initialAssigned"] == 300.0


def test_project_funding_summary_uses_historical_funding_when_initial_missing(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(project_funding_service, "mongo", mongo_stub)

    project_id = ObjectId()
    project = {
        "_id": project_id,
        "nombre": "Proyecto Active",
        "balance": 0,
        "balance_inicial": 0,
        "status": {"actual": 1, "completado": []},
        "fundingModel": {
            "version": 2,
            "status": "active",
            "configuredAt": None,
            "migratedAt": None,
            "migratedBy": None,
            "initialAssignedAmount": 0,
            "legacyCurrentBalanceSnapshot": None,
            "legacyInitialBalanceSnapshot": None,
            "migrationNote": None,
        },
    }
    mongo_stub.db.proyectos.rows.append(project)
    mongo_stub.db.master_accounts.rows.append(
        {
            "year": 2026,
            "code": "403109900000",
            "description": "Cuenta detalle",
            "group": "EGRESO",
            "is_header": False,
            "level": 4,
            "parent_code": "403100000000",
        }
    )
    mongo_stub.db.account_scope_state.rows.append(
        {
            "year": 2026,
            "scopeType": "project",
            "scopeId": str(project_id),
            "accountCode": "403109900000",
            "balance": 200.0,
            "movementsCount": 2,
            "lastMovementAt": None,
        }
    )
    mongo_stub.db.ledger_movements.rows.extend(
        [
            {
                "_id": ObjectId(),
                "year": 2026,
                "scopeType": "project",
                "scopeId": str(project_id),
                "accountCode": "403109900000",
                "type": "debit",
                "amount": 300.0,
                "reference": {"kind": "transfer", "fundingType": "funding"},
            },
            {
                "_id": ObjectId(),
                "year": 2026,
                "scopeType": "project",
                "scopeId": str(project_id),
                "accountCode": "403109900000",
                "type": "credit",
                "amount": 100.0,
                "reference": {"kind": "project_expense"},
            },
        ]
    )

    summary = ProjectFundingService.build_summary(project, year=2026, user={"role": "super_admin"})

    assert summary["totals"]["currentAvailable"] == 200.0
    assert summary["totals"]["initialAssigned"] == 300.0


def test_allocate_funds_updates_project_and_states(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(accounting_service, "mongo", mongo_stub)
    monkeypatch.setattr(project_funding_service, "mongo", mongo_stub)
    monkeypatch.setattr(project_funding_service, "agregar_log", lambda *args, **kwargs: None)

    for code in ("401010100000", "401010200000"):
        mongo_stub.db.master_accounts.rows.append(
            {
                "year": 2025,
                "code": code,
                "description": "Cuenta detalle",
                "group": "EGRESO",
                "is_header": False,
                "level": 4,
                "parent_code": "401010000000",
            }
        )

    project_id = ObjectId()
    department_id = ObjectId()
    project = {
        "_id": project_id,
        "nombre": "Proyecto Funding",
        "departamento_id": department_id,
        "balance": 0,
        "balance_inicial": 0,
        "status": {"actual": 1, "completado": []},
        "fundingModel": {
            "version": 2,
            "status": "active",
            "configuredAt": None,
            "migratedAt": None,
            "migratedBy": None,
            "initialAssignedAmount": 0,
            "legacyCurrentBalanceSnapshot": None,
            "legacyInitialBalanceSnapshot": None,
            "migrationNote": None,
        },
    }
    mongo_stub.db.proyectos.rows.append(project)
    mongo_stub.db.account_scope_state.rows.append(
        {
            "year": 2025,
            "scopeType": "department",
            "scopeId": str(department_id),
            "accountCode": "401010100000",
            "balance": 500.0,
            "movementsCount": 1,
        }
    )

    result = ProjectFundingService.allocate_funds(
        project,
        year=2025,
        source_scope_type="department",
        source_scope_id=str(department_id),
        allocations=[
            {
                "fromAccountCode": "401010100000",
                "toAccountCode": "401010200000",
                "amount": 250.0,
                "description": "Asignacion inicial",
            }
        ],
        user={"sub": "user-1", "nombre": "Admin", "role": "admin_departamento", "departmentId": str(department_id)},
        allow_negative=False,
    )

    assert result["fundingSummary"]["totals"]["currentAvailable"] == 250.0
    assert result["fundingSummary"]["totals"]["initialAssigned"] == 250.0
    updated_project = mongo_stub.db.proyectos.find_one({"_id": project_id})
    assert updated_project["fundingModel"]["initialAssignedAmount"] == 25000
    assert 1 in updated_project["status"]["completado"]


def test_migration_requires_exact_total_and_activates_project(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(accounting_service, "mongo", mongo_stub)
    monkeypatch.setattr(project_funding_service, "mongo", mongo_stub)
    monkeypatch.setattr(project_funding_service, "agregar_log", lambda *args, **kwargs: None)

    for code in ("401010100000", "401010200000"):
        mongo_stub.db.master_accounts.rows.append(
            {
                "year": 2025,
                "code": code,
                "description": "Cuenta detalle",
                "group": "EGRESO",
                "is_header": False,
                "level": 4,
                "parent_code": "401010000000",
            }
        )

    project_id = ObjectId()
    department_id = ObjectId()
    project = {
        "_id": project_id,
        "nombre": "Proyecto Migracion",
        "departamento_id": department_id,
        "balance": 100000,
        "balance_inicial": 120000,
        "status": {"actual": 1, "completado": []},
        "fundingModel": {
            "version": 2,
            "status": "legacy",
            "configuredAt": None,
            "migratedAt": None,
            "migratedBy": None,
            "initialAssignedAmount": 0,
            "legacyCurrentBalanceSnapshot": 100000,
            "legacyInitialBalanceSnapshot": 120000,
            "migrationNote": None,
        },
    }
    mongo_stub.db.proyectos.rows.append(project)
    mongo_stub.db.account_scope_state.rows.append(
        {
            "year": 2025,
            "scopeType": "department",
            "scopeId": str(department_id),
            "accountCode": "401010100000",
            "balance": 1200.0,
            "movementsCount": 1,
        }
    )

    result = ProjectFundingService.allocate_funds(
        project,
        year=2025,
        source_scope_type="department",
        source_scope_id=str(department_id),
        allocations=[
            {
                "fromAccountCode": "401010100000",
                "toAccountCode": "401010200000",
                "amount": 1000.0,
                "description": "Migracion saldo legacy",
            }
        ],
        user={"sub": "user-1", "nombre": "Admin", "role": "admin_departamento", "departmentId": str(department_id)},
        allow_negative=False,
        migration=True,
        note="Migracion manual",
    )

    updated_project = mongo_stub.db.proyectos.find_one({"_id": project_id})
    assert updated_project["fundingModel"]["status"] == "active"
    assert updated_project["fundingModel"]["initialAssignedAmount"] == 120000
    assert result["fundingSummary"]["totals"]["currentAvailable"] == 1000.0


def test_build_timeline_merges_ledger_and_legacy(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(project_funding_service, "mongo", mongo_stub)

    project_id = ObjectId()
    project = {
        "_id": project_id,
        "nombre": "Proyecto Timeline",
        "balance": 50000,
        "balance_inicial": 50000,
        "status": {"actual": 1, "completado": []},
        "fundingModel": {
            "version": 2,
            "status": "legacy",
            "configuredAt": None,
            "migratedAt": None,
            "migratedBy": None,
            "initialAssignedAmount": 0,
            "legacyCurrentBalanceSnapshot": 50000,
            "legacyInitialBalanceSnapshot": 50000,
            "migrationNote": None,
        },
    }
    mongo_stub.db.proyectos.rows.append(project)
    mongo_stub.db.ledger_movements.rows.append(
        {
            "_id": ObjectId(),
            "year": 2025,
            "scopeType": "project",
            "scopeId": str(project_id),
            "accountCode": "401010200000",
            "type": "debit",
            "amount": 100.0,
            "description": "Asignacion",
            "reference": {"kind": "transfer", "fundingType": "funding", "title": "Asignación de fondos", "actorName": "Admin"},
            "createdAt": datetime(2025, 1, 10, tzinfo=timezone.utc),
        }
    )
    mongo_stub.db.acciones.rows.append(
        {
            "_id": ObjectId(),
            "project_id": project_id,
            "user": "Legacy User",
            "type": "Fondeo",
            "amount": 50000,
            "total_amount": 50000,
            "created_at": datetime(2025, 1, 1),
        }
    )

    timeline = ProjectFundingService.build_timeline(project, year=2025)

    assert {item["source"] for item in timeline} == {"ledger", "legacy_action"}
    assert any(item["type"] == "funding" for item in timeline)


def test_descargar_movimientos_exporta_timeline_json(monkeypatch):
    mongo_stub = MongoStub()
    monkeypatch.setattr(project_routes, "mongo", mongo_stub)
    monkeypatch.setattr(project_routes, "ProjectFundingService", ProjectFundingService)
    monkeypatch.setattr(project_funding_service, "mongo", mongo_stub)

    project_id = ObjectId()
    project = {
        "_id": project_id,
        "nombre": "Proyecto Export",
        "balance": 0,
        "balance_inicial": 0,
        "status": {"actual": 1, "completado": []},
    }
    mongo_stub.db.proyectos.rows.append(project)

    monkeypatch.setattr(
        project_routes.ProjectFundingService,
        "build_timeline",
        lambda *_args, **_kwargs: [
            {
                "id": "mov-1",
                "occurredAt": datetime(2025, 1, 1, tzinfo=timezone.utc),
                "type": "funding",
                "source": "ledger",
                "title": "Asignación de fondos",
                "description": "Linea 1",
                "amount": 100.0,
                "projectBalanceAfter": 100.0,
                "accountCode": "401010200000",
                "actorName": "Admin",
                "reference": {},
            }
        ],
    )

    app = create_app()
    with app.test_request_context(f"/proyecto/{project_id}/movimientos/descargar?formato=json"):
        response = project_routes.descargar_movimientos.__wrapped__.__wrapped__(
            {"role": "super_admin"},
            str(project_id),
        )

    response.direct_passthrough = False
    payload = response.get_data(as_text=True)
    assert "Asignación de fondos" in payload
    assert "projectBalanceAfter" in payload
    assert 'filename=timeline_movimientos.json' in response.headers.get("Content-Disposition", "")
