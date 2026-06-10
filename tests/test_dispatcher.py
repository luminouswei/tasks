import re

import pytest

from app.dispatcher import dispatch
from app.errors import ToolExecutionError
from app.registry import ToolSpec
from app import registry
from app.trace_store import get_trace, init_db


@pytest.fixture
def temp_trace_db(tmp_path, monkeypatch):
    db_path = tmp_path / "traces.db"
    init_db(db_path)

    monkeypatch.setattr("app.trace_store._db_path", db_path)

    return db_path


@pytest.fixture(autouse=True)
def clear_registry():
    old_registry = registry._registry.copy()
    registry._registry.clear()

    yield

    registry._registry.clear()
    registry._registry.update(old_registry)


def test_dispatch_success(temp_trace_db):
    def fake_run(message: str):
        return {"echo": message}

    registry._registry["fake"] = ToolSpec(
        name="fake",
        description="fake tool",
        run=fake_run,
    )

    result = dispatch("fake", "hi")

    assert result.tool == "fake"
    assert result.result == {"echo": "hi"}
    assert result.error is None
    assert result.latency_ms >= 0
    assert re.fullmatch(r"[0-9a-f]{32}", result.trace_id)

    trace = get_trace(result.trace_id, temp_trace_db)
    assert trace is not None
    assert trace["result"] == {"echo": "hi"}


def test_dispatch_tool_execution_error(temp_trace_db):
    def fake_run(message: str):
        raise ToolExecutionError("bad input")

    registry._registry["fake"] = ToolSpec(
        name="fake",
        description="fake tool",
        run=fake_run,
    )

    result = dispatch("fake", "hi")

    assert result.result is None
    assert result.error is not None
    assert result.error.code == "TOOL_EXECUTION_ERROR"
    assert result.error.message == "bad input"

    trace = get_trace(result.trace_id, temp_trace_db)
    assert trace is not None
    assert trace["error"] == {
        "code": "TOOL_EXECUTION_ERROR",
        "message": "bad input",
    }


def test_dispatch_internal_error(temp_trace_db):
    def fake_run(message: str):
        raise ValueError("secret details")

    registry._registry["fake"] = ToolSpec(
        name="fake",
        description="fake tool",
        run=fake_run,
    )

    result = dispatch("fake", "hi")

    assert result.result is None
    assert result.error is not None
    assert result.error.code == "INTERNAL_ERROR"
    assert result.error.message == "internal server error"

    trace = get_trace(result.trace_id, temp_trace_db)
    assert trace is not None
    assert trace["error"] == {
        "code": "INTERNAL_ERROR",
        "message": "internal server error",
    }


def test_dispatch_tool_not_found(temp_trace_db):
    result = dispatch("missing", "hi")

    assert result.result is None
    assert result.error is not None
    assert result.error.code == "TOOL_NOT_FOUND"
    assert "missing" in result.error.message

    trace = get_trace(result.trace_id, temp_trace_db)
    assert trace is not None
    assert trace["error"]["code"] == "TOOL_NOT_FOUND"