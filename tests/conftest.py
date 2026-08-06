# tests/conftest.py
import pytest
from bson import ObjectId

from api.index import app
from api.routes import documents as documents_routes
from pymongo import MongoClient


@pytest.fixture(autouse=True)
def mock_backblaze_upload(monkeypatch):
    def fake_upload(_file_buffer, full_path):
        return {
            "fileName": full_path,
            "download_url": f"https://files.example.test/{full_path}",
            "fileId": str(ObjectId()),
        }

    monkeypatch.setattr(documents_routes, "upload_file", fake_upload)

@pytest.fixture
def test_db():
    uri = app.config["MONGO_URI"]
    client = MongoClient(uri)
    db = client.get_default_database()
    yield db
    # Limpiar al final del test
    client.drop_database(db.name)

@pytest.fixture
def client():
    app.config.update({
        "TESTING": True,
    })
    return app.test_client()
