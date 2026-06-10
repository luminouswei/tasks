import pytest
from fastapi.testclient import TestClient

from app.agent_run_store import init_db
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "agent_runs.db"
    init_db(db_path)

    monkeypatch.setattr("app.agent_run_store._db_path", db_path)

    with TestClient(app) as test_client:
        yield test_client