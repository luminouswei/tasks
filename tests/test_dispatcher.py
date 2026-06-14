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


# ---------- 4. 不可序列化 tool_result 仍能落库 ----------


def test_dispatch_unserializable_tool_result_still_persists(temp_agent_run_db):
    """工具返回 set/自定义对象等不可 JSON 序列化的值,trace 仍要落库,
    并且读出来的 tool_result 是结构化 fallback(带 _unserializable 标记)。
    关键:不再静默退化为 None。
    """
    def fake_run(message: str):
        return {1, 2, 3}  # set
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    # 业务结果(status=success)不受影响
    assert result.run.status == "success"
    # AgentRun 内存里的 tool_result 仍然是原始 set(主调用方拿得到)
    assert result.run.tool_result == {1, 2, 3}

    # trace 仍然落库
    loaded = get_run(result.run.run_id, temp_agent_run_db)
    assert loaded is not None
    assert loaded["status"] == "success"
    # 落库后是结构化 fallback dict,不是 None
    assert loaded["tool_result"] is not None
    assert loaded["tool_result"]["_unserializable"] is True
    assert loaded["tool_result"]["type"] == "set"


# ---------- 5. 持久化失败时,调用方能拿到明确失败信息 ----------


def test_dispatch_surfaces_persistence_failure(monkeypatch, tmp_path):
    """insert_run 抛异常(模拟 DB 锁 / 磁盘满 / 权限错),
    dispatch 不应再静默吞错,要在 run 上带出 trace_persistence=failed + trace_error。"""
    db = tmp_path / "runs.db"
    init_db(db)
    monkeypatch.setattr("app.agent_run_store._db_path", db)

    def broken_insert_run(record, path=None):
        raise RuntimeError("disk full: simulated")
    monkeypatch.setattr("app.dispatcher.insert_run", broken_insert_run)

    def fake_run(message: str):
        return {"ok": True}
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    # 业务结果仍然返回,不让 trace 失败反过来影响业务可观察性
    assert result.run.status == "success"
    assert result.run.tool_result == {"ok": True}
    # 持久化失败被显式带出来
    assert result.run.trace_persistence == "failed"
    assert result.run.trace_error is not None
    assert "RuntimeError" in result.run.trace_error
    assert "disk full" in result.run.trace_error
    # 业务 error 字段不该被污染(这是 trace 失败,不是业务失败)
    assert result.run.error is None
