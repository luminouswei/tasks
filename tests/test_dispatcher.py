import re
from datetime import datetime

import pytest

from app.agent_run_store import get_run, init_db
from app import registry
from app.dispatcher import dispatch
from app.errors import ToolExecutionError
from app.registry import ToolSpec


@pytest.fixture
def temp_agent_run_db(tmp_path, monkeypatch):
    db_path = tmp_path / "agent_runs.db"
    init_db(db_path)
    monkeypatch.setattr("app.agent_run_store._db_path", db_path)
    return db_path


@pytest.fixture(autouse=True)
def clear_registry():
    old_registry = registry._registry.copy()
    registry._registry.clear()
    yield
    registry._registry.clear()
    registry._registry.update(old_registry)


def _register_fake(run):
    """把一个本地函数塞进 _registry,名字固定为 'fake'。"""
    registry._registry["fake"] = ToolSpec(
        name="fake",
        description="fake tool",
        run=run,
    )


# ---------- 1. run 形态:4 个 status 各自的字段断言 ----------


def test_dispatch_success(temp_agent_run_db):
    def fake_run(message: str):
        return {"echo": message}
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    assert result.run.status == "success"
    assert result.run.tool_result == {"echo": "hi"}
    assert result.run.error is None
    assert result.run.selected_tool == "fake"
    assert result.run.input == "hi"
    assert re.fullmatch(r"[0-9a-f]{32}", result.run.run_id)


def test_dispatch_tool_execution_error(temp_agent_run_db):
    def fake_run(message: str):
        raise ToolExecutionError("bad input")
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    assert result.run.status == "tool_error"
    assert result.run.tool_result is None
    assert result.run.error is not None
    assert result.run.error.code == "TOOL_EXECUTION_ERROR"
    assert result.run.error.message == "bad input"


def test_dispatch_internal_error(temp_agent_run_db):
    """未预期异常 → 状态 internal_error,error.message 脱敏,不暴露原始异常信息。"""
    def fake_run(message: str):
        raise ValueError("secret details")
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    assert result.run.status == "internal_error"
    assert result.run.tool_result is None
    assert result.run.error is not None
    assert result.run.error.code == "INTERNAL_ERROR"
    assert result.run.error.message == "internal server error"
    # 原始异常信息不能进 trace
    assert "secret" not in result.run.error.message


def test_dispatch_tool_not_found(temp_agent_run_db):
    result = dispatch("missing", "hi")

    assert result.run.status == "tool_not_found"
    assert result.run.tool_result is None
    assert result.run.error is not None
    assert result.run.error.code == "TOOL_NOT_FOUND"
    assert "missing" in result.run.error.message


# ---------- 2. tool_args / started_at / finished_at 形态 ----------


def test_dispatch_tool_args_and_timestamps(temp_agent_run_db):
    def fake_run(message: str):
        return message
    _register_fake(fake_run)

    result = dispatch("fake", "12 * 8")

    assert result.run.tool_args == {"message": "12 * 8"}
    started = datetime.fromisoformat(result.run.started_at)
    finished = datetime.fromisoformat(result.run.finished_at)
    assert started < finished


# ---------- 3. 4 个 status 都要落库(覆盖 success / tool_error / internal_error / tool_not_found) ----------


def test_dispatch_success_persists_to_store(temp_agent_run_db):
    def fake_run(message: str):
        return {"echo": message}
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    loaded = get_run(result.run.run_id, temp_agent_run_db)
    assert loaded is not None
    assert loaded["status"] == "success"
    assert loaded["tool_result"] == {"echo": "hi"}
    assert loaded["error"] is None
    assert loaded["tool_args"] == {"message": "hi"}
    assert loaded["input"] == "hi"
    assert loaded["selected_tool"] == "fake"


def test_dispatch_tool_error_persists_to_store(temp_agent_run_db):
    def fake_run(message: str):
        raise ToolExecutionError("bad input")
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    loaded = get_run(result.run.run_id, temp_agent_run_db)
    assert loaded is not None
    assert loaded["status"] == "tool_error"
    assert loaded["tool_result"] is None
    assert loaded["error"]["code"] == "TOOL_EXECUTION_ERROR"
    assert loaded["error"]["message"] == "bad input"


def test_dispatch_internal_error_persists_to_store(temp_agent_run_db):
    """internal_error 也要落库,error.message 仍是脱敏后的固定字符串。"""
    def fake_run(message: str):
        raise ValueError("secret details")
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    loaded = get_run(result.run.run_id, temp_agent_run_db)
    assert loaded is not None
    assert loaded["status"] == "internal_error"
    assert loaded["tool_result"] is None
    assert loaded["error"]["code"] == "INTERNAL_ERROR"
    assert loaded["error"]["message"] == "internal server error"
    assert "secret" not in loaded["error"]["message"]


def test_dispatch_tool_not_found_persists_to_store(temp_agent_run_db):
    result = dispatch("missing", "hi")

    loaded = get_run(result.run.run_id, temp_agent_run_db)
    assert loaded is not None
    assert loaded["status"] == "tool_not_found"
    assert loaded["error"]["code"] == "TOOL_NOT_FOUND"
    assert loaded["tool_result"] is None
