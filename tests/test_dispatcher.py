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
    # timeline 仍然完整:trace_persisted 被替换成 trace_persist_failed,
    # response_returned 保留(响应确实出去了)
    event_names = [ev.name for ev in result.run.timeline]
    assert "trace_persist_failed" in event_names
    assert "response_returned" in event_names
    assert "trace_persisted" not in event_names
    # trace_persist_failed 的 detail 是异常类名(operator-facing)
    failed_event = next(
        ev for ev in result.run.timeline if ev.name == "trace_persist_failed"
    )
    assert failed_event.detail == "RuntimeError"


# ---------- 6. timeline 事件按执行顺序记录(含失败分支) ----------


def test_dispatch_success_timeline_has_7_events_in_order(temp_agent_run_db):
    """成功 run → 7 个事件按执行顺序:
    request_received, validation_passed, tool_dispatch_started,
    tool_executed, trace_serialized, trace_persisted, response_returned
    """
    def fake_run(message: str):
        return {"echo": message}
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    event_names = [ev.name for ev in result.run.timeline]
    assert event_names == [
        "request_received", "validation_passed", "tool_dispatch_started",
        "tool_executed", "trace_serialized", "trace_persisted",
        "response_returned",
    ]


def test_dispatch_timeline_events_have_iso_timestamps(temp_agent_run_db):
    """timeline 事件时间戳是 ISO 8601 字符串,客户端可以解析。"""
    from datetime import datetime
    def fake_run(message: str):
        return message
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    for ev in result.run.timeline:
        datetime.fromisoformat(ev.at)  # 解析不抛即合法


def test_dispatch_timeline_tool_not_found_uses_validation_failed(temp_agent_run_db):
    """tool 不存在 → timeline 走 validation_failed 分支,不会记 tool_dispatch_started。"""
    result = dispatch("missing", "hi")

    event_names = [ev.name for ev in result.run.timeline]
    assert "request_received" in event_names
    assert "validation_failed" in event_names
    assert "tool_dispatch_started" not in event_names
    # 但 trace_persisted + response_returned 仍然记(响应确实出去了,只是没真正持久化到 DB 的 run 是 failed 形态)
    assert "trace_persisted" in event_names
    assert "response_returned" in event_names
    # validation_failed 的 detail 是 error.code(便于查询/告警)
    failed_event = next(
        ev for ev in result.run.timeline if ev.name == "validation_failed"
    )
    assert failed_event.detail == "TOOL_NOT_FOUND"


def test_dispatch_timeline_tool_execution_error_uses_tool_failed(temp_agent_run_db):
    """tool 抛 ToolExecutionError → tool_failed 事件,detail=TOOL_EXECUTION_ERROR。"""
    def fake_run(message: str):
        raise ToolExecutionError("internal tool bug")
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    event_names = [ev.name for ev in result.run.timeline]
    assert "validation_passed" in event_names
    assert "tool_dispatch_started" in event_names
    assert "tool_failed" in event_names
    assert "tool_executed" not in event_names
    failed_event = next(
        ev for ev in result.run.timeline if ev.name == "tool_failed"
    )
    assert failed_event.detail == "TOOL_EXECUTION_ERROR"


def test_dispatch_timeline_unserializable_records_serialization_fallback(temp_agent_run_db):
    """不可 JSON 序列化的 tool_result → trace_serialization_fallback 事件,不是 trace_serialized。"""
    def fake_run(message: str):
        return {1, 2, 3}  # set
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    event_names = [ev.name for ev in result.run.timeline]
    assert "tool_executed" in event_names
    assert "trace_serialization_fallback" in event_names
    assert "trace_serialized" not in event_names
    fallback_event = next(
        ev for ev in result.run.timeline if ev.name == "trace_serialization_fallback"
    )
    # detail 是不可序列化值的类型名(运维一眼看到是哪种类型)
    assert fallback_event.detail == "set"


def test_dispatch_timeline_persists_to_db(temp_agent_run_db):
    """DB 里的 timeline 跟 in-memory AgentRun.timeline 一致(都是完整 7 事件)。

    关键:DB 不能丢 trace_persisted / response_returned 这两条(否则 GET diagnostics
    拿到的 timeline 就缺尾部,跟 dispatch 内存响应里的对不上,会出诡异 bug)。
    """
    def fake_run(message: str):
        return message
    _register_fake(fake_run)

    result = dispatch("fake", "hi")

    loaded = get_run(result.run.run_id, temp_agent_run_db)
    assert loaded is not None
    loaded_events = [ev["name"] for ev in loaded["timeline"]]
    mem_events = [ev.name for ev in result.run.timeline]
    assert loaded_events == mem_events
    # 完整 7 条
    assert len(loaded_events) == 7
    assert "trace_persisted" in loaded_events
    assert "response_returned" in loaded_events
