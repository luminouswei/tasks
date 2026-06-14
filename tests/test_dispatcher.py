import re
from datetime import datetime

import pytest

from app.agent_run import AgentWarning
from app.agent_run_store import get_run, init_db
from app import registry
from app.dispatcher import dispatch
from app.errors import ToolExecutionError, ToolInputError
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


# ---------- 1. 4 类业务结果都映射到 status=completed/failed + error.code ----------


def test_dispatch_success(temp_agent_run_db):
    def fake_run(message: str):
        return {"echo": message}
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    # 业务成功 → status=completed
    assert result.run.status == "completed"
    assert result.run.tool_result == {"echo": "hi"}
    assert result.run.error is None
    assert result.run.warnings == []
    assert result.run.selected_tool == "fake"
    assert result.run.input == "hi"
    assert re.fullmatch(r"[0-9a-f]{32}", result.run.run_id)


def test_dispatch_tool_execution_error(temp_agent_run_db):
    """工具内部崩(工具责任) → status=failed + TOOL_EXECUTION_ERROR(HTTP 500)。"""
    def fake_run(message: str):
        raise ToolExecutionError("internal tool bug")
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    assert result.run.status == "failed"
    assert result.run.tool_result is None
    assert result.run.error is not None
    assert result.run.error.code == "TOOL_EXECUTION_ERROR"
    assert result.run.error.message == "internal tool bug"


def test_dispatch_tool_input_error(temp_agent_run_db):
    """用户输入错(用户责任) → status=failed + TOOL_INPUT_ERROR(HTTP 400)。

    跟 TOOL_EXECUTION_ERROR(HTTP 500)区分:用户错不需要告警工具,改请求重发即可。
    """
    def fake_run(message: str):
        raise ToolInputError("bad argument")
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    assert result.run.status == "failed"
    assert result.run.tool_result is None
    assert result.run.error is not None
    assert result.run.error.code == "TOOL_INPUT_ERROR"
    assert result.run.error.message == "bad argument"


def test_dispatch_internal_error(temp_agent_run_db):
    """未预期异常 → status=failed + INTERNAL_ERROR,error.message 脱敏,不暴露原始异常信息。"""
    def fake_run(message: str):
        raise ValueError("secret details")
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    assert result.run.status == "failed"
    assert result.run.tool_result is None
    assert result.run.error is not None
    assert result.run.error.code == "INTERNAL_ERROR"
    assert result.run.error.message == "internal server error"
    # 原始异常信息不能进 trace
    assert "secret" not in result.run.error.message


def test_dispatch_tool_not_found(temp_agent_run_db):
    result = dispatch("missing", "hi")

    # 工具不存在也是 failed,error.code 区分具体原因
    assert result.run.status == "failed"
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


# ---------- 3. 4 类业务结果都要落库 ----------


def test_dispatch_success_persists_to_store(temp_agent_run_db):
    def fake_run(message: str):
        return {"echo": message}
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    loaded = get_run(result.run.run_id, temp_agent_run_db)
    assert loaded is not None
    assert loaded["status"] == "completed"
    assert loaded["tool_result"] == {"echo": "hi"}
    assert loaded["error"] is None
    assert loaded["warnings"] == []
    assert loaded["tool_args"] == {"message": "hi"}
    assert loaded["input"] == "hi"
    assert loaded["selected_tool"] == "fake"


def test_dispatch_tool_error_persists_to_store(temp_agent_run_db):
    def fake_run(message: str):
        raise ToolExecutionError("internal tool bug")
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    loaded = get_run(result.run.run_id, temp_agent_run_db)
    assert loaded is not None
    assert loaded["status"] == "failed"
    assert loaded["tool_result"] is None
    assert loaded["error"]["code"] == "TOOL_EXECUTION_ERROR"
    assert loaded["error"]["message"] == "internal tool bug"


def test_dispatch_tool_input_error_persists_to_store(temp_agent_run_db):
    """用户输入错 → DB 落 status=failed + error.code=TOOL_INPUT_ERROR。"""
    def fake_run(message: str):
        raise ToolInputError("bad input")
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    loaded = get_run(result.run.run_id, temp_agent_run_db)
    assert loaded is not None
    assert loaded["status"] == "failed"
    assert loaded["tool_result"] is None
    assert loaded["error"]["code"] == "TOOL_INPUT_ERROR"
    assert loaded["error"]["message"] == "bad input"


def test_dispatch_internal_error_persists_to_store(temp_agent_run_db):
    """internal_error 也要落库,error.message 仍是脱敏后的固定字符串。"""
    def fake_run(message: str):
        raise ValueError("secret details")
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    loaded = get_run(result.run.run_id, temp_agent_run_db)
    assert loaded is not None
    assert loaded["status"] == "failed"
    assert loaded["tool_result"] is None
    assert loaded["error"]["code"] == "INTERNAL_ERROR"
    assert loaded["error"]["message"] == "internal server error"
    assert "secret" not in loaded["error"]["message"]


def test_dispatch_tool_not_found_persists_to_store(temp_agent_run_db):
    result = dispatch("missing", "hi")

    loaded = get_run(result.run.run_id, temp_agent_run_db)
    assert loaded is not None
    assert loaded["status"] == "failed"
    assert loaded["error"]["code"] == "TOOL_NOT_FOUND"
    assert loaded["tool_result"] is None


# ---------- 4. 不可序列化 tool_result 仍能落库 + 加 warning ----------


def test_dispatch_unserializable_tool_result_still_persists(temp_agent_run_db):
    """工具返回 set 等不可 JSON 序列化的值:
    1. trace 仍要落库
    2. tool_result 是结构化 fallback(带 _serialization 标记)
    3. warnings 列表里有 TOOL_RESULT_SERIALIZATION_FALLBACK caveat
    """
    def fake_run(message: str):
        return {1, 2, 3}  # set
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    # 业务结果(status=completed)不受影响
    assert result.run.status == "completed"
    # AgentRun 内存里的 tool_result 仍然是原始 set(主调用方拿得到)
    assert result.run.tool_result == {1, 2, 3}
    # warnings 列表里有 caveat
    assert len(result.run.warnings) == 1
    assert result.run.warnings[0].code == "TOOL_RESULT_SERIALIZATION_FALLBACK"

    # trace 仍然落库
    loaded = get_run(result.run.run_id, temp_agent_run_db)
    assert loaded is not None
    assert loaded["status"] == "completed"
    # 落库后是结构化 fallback dict,不是 None
    assert loaded["tool_result"] is not None
    assert loaded["tool_result"]["_serialization"] == "fallback"
    assert loaded["tool_result"]["type"] == "set"
    # warnings 也落库
    assert len(loaded["warnings"]) == 1
    assert loaded["warnings"][0]["code"] == "TOOL_RESULT_SERIALIZATION_FALLBACK"


def test_dispatch_serializable_tool_result_has_no_warnings(temp_agent_run_db):
    """正常返回值的 run 不会有 warning。"""
    def fake_run(message: str):
        return {"echo": message}
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    assert result.run.warnings == []
    loaded = get_run(result.run.run_id, temp_agent_run_db)
    assert loaded["warnings"] == []


# ---------- 5. 持久化失败时,业务结果保留 + status 盖成 trace_persist_failed ----------


def test_dispatch_surfaces_persistence_failure(monkeypatch, tmp_path):
    """insert_run 抛异常(模拟 DB 锁 / 磁盘满 / 权限错):
    - status 盖成 trace_persist_failed
    - error.code = TRACE_PERSIST_FAILED
    - 业务结果保留(主调用方拿得到)
    """
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

    # 业务结果仍然在响应里
    assert result.run.tool_result == {"ok": True}
    # status 盖成 trace_persist_failed(只 in-memory,DB 没这行)
    assert result.run.status == "trace_persist_failed"
    # error.code 显式带出失败原因
    assert result.run.error is not None
    assert result.run.error.code == "TRACE_PERSIST_FAILED"
    assert "RuntimeError" in result.run.error.message
    assert "disk full" in result.run.error.message
