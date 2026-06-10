import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.trace_store import init_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "traces.db"
    init_db(db_path)

    monkeypatch.setattr("app.trace_store._db_path", db_path)

    with TestClient(app) as test_client:
        yield test_client