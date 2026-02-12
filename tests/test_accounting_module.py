from types import SimpleNamespace

from bson import ObjectId
from pymongo import UpdateOne

from api.routes import accounting as accounting_routes
from api.services import accounting_service
from api.services.accounting_service import AccountScopeService, SeedService


class BulkResult:
    def __init__(self, upserted_count=0, modified_count=0):
        self.upserted_count = upserted_count
        self.modified_count = modified_count


class InMemoryCollection:
    def __init__(self):
        self.rows = []

    def create_index(self, *args, **kwargs):
        return "idx"

    def _match(self, row, query):
        return all(row.get(k) == v for k, v in query.items())

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

        return _Cursor(out)

    def update_one(self, query, update, upsert=False, session=None):
        row = next((r for r in self.rows if self._match(r, query)), None)
        if row is None:
            if not upsert:
                return
            row = dict(query)
            row.update(update.get("$setOnInsert", {}))
            self.rows.append(row)
        for k, v in update.get("$set", {}).items():
            row[k] = v
        for k, v in update.get("$inc", {}).items():
            row[k] = row.get(k, 0) + v

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
        self.departamentos = InMemoryCollection()


class MongoStub:
    def __init__(self):
        self.db = InMemoryDB()

        class _Client:
            def start_session(self):
                raise RuntimeError("no sessions in test")

        self.cx = _Client()


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
