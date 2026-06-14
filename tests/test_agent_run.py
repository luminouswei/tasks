from datetime import datetime

from app.agent_run import AgentErrorPayload, AgentRun, AgentWarning, TimelineEvent


def _parse_iso(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


def test_agent_run_completed_construction():
    """成功 run:status=completed,tool_result 填值,error 为 None,warnings 为 []。"""
    run = AgentRun(
        run_id="a" * 32,
        input="12 * 8",
        selected_tool="calculator",
        tool_args={"message": "12 * 8"},
        tool_result=96,
        status="completed",
        error=None,
        started_at="2026-06-10T08:00:00+00:00",
        finished_at="2026-06-10T08:00:00.005+00:00",
    )

    assert run.status == "completed"
    assert run.tool_result == 96
    assert run.error is None
    assert run.warnings == []  # 默认值


def test_agent_run_failed_tool_input_error_construction():
    """工具输入错(用户责任):status=failed,error.code=TOOL_INPUT_ERROR。"""
    run = AgentRun(
        run_id="b" * 32,
        input="1/0",
        selected_tool="calculator",
        tool_args={"message": "1/0"},
        tool_result=None,
        status="failed",
        error=AgentErrorPayload(
            code="TOOL_INPUT_ERROR",
            message="division by zero",
        ),
        started_at="2026-06-10T08:00:00+00:00",
        finished_at="2026-06-10T08:00:00.001+00:00",
    )

    assert run.status == "failed"
    assert run.tool_result is None
    assert run.error is not None
    assert run.error.code == "TOOL_INPUT_ERROR"
    assert run.error.message == "division by zero"


def test_agent_run_failed_tool_execution_error_construction():
    """工具执行异常(工具责任):status=failed,error.code=TOOL_EXECUTION_ERROR。"""
    run = AgentRun(
        run_id="c" * 32,
        input="x",
        selected_tool="weather",
        tool_args={"message": "x"},
        tool_result=None,
        status="failed",
        error=AgentErrorPayload(
            code="TOOL_EXECUTION_ERROR",
            message="weather API timed out",
        ),
        started_at="2026-06-10T08:00:00+00:00",
        finished_at="2026-06-10T08:00:00+00:00",
    )

    assert run.status == "failed"
    assert run.error.code == "TOOL_EXECUTION_ERROR"


def test_agent_run_failed_tool_not_found_construction():
    """工具不存在:status=failed,error.code=TOOL_NOT_FOUND。"""
    run = AgentRun(
        run_id="d" * 32,
        input="hi",
        selected_tool="weather",
        tool_args={"message": "hi"},
        tool_result=None,
        status="failed",
        error=AgentErrorPayload(
            code="TOOL_NOT_FOUND",
            message="tool 'weather' is not registered",
        ),
        started_at="2026-06-10T08:00:00+00:00",
        finished_at="2026-06-10T08:00:00+00:00",
    )

    assert run.status == "failed"
    assert run.error.code == "TOOL_NOT_FOUND"


def test_agent_run_trace_persist_failed_construction():
    """trace 落库失败:status=trace_persist_failed,error.code=TRACE_PERSIST_FAILED。

    这是 in-memory 状态,DB 里不会落;但作为 dataclass 字段值仍然合法。
    """
    run = AgentRun(
        run_id="e" * 32,
        input="hi",
        selected_tool="echo",
        tool_args={"message": "hi"},
        tool_result="hi",
        status="trace_persist_failed",
        error=AgentErrorPayload(
            code="TRACE_PERSIST_FAILED",
            message="RuntimeError: disk full",
        ),
        started_at="2026-06-10T08:00:00+00:00",
        finished_at="2026-06-10T08:00:00+00:00",
    )

    assert run.status == "trace_persist_failed"
    assert run.error.code == "TRACE_PERSIST_FAILED"


def test_agent_run_with_warnings_construction():
    """成功 run 可以带 warning(序列化降级等 caveat 不算 error)。"""
    run = AgentRun(
        run_id="f" * 32,
        input="x",
        selected_tool="custom",
        tool_args={"message": "x"},
        tool_result={"_serialization": "fallback", "type": "set", "repr": "{1, 2, 3}"},
        status="completed",
        error=None,
        warnings=[AgentWarning(
            code="TOOL_RESULT_SERIALIZATION_FALLBACK",
            message="tool result was not JSON-serializable; stored a safe repr",
        )],
        started_at="2026-06-10T08:00:00+00:00",
        finished_at="2026-06-10T08:00:00+00:00",
    )

    assert run.status == "completed"
    assert run.error is None
    assert len(run.warnings) == 1
    assert run.warnings[0].code == "TOOL_RESULT_SERIALIZATION_FALLBACK"


def test_agent_run_started_at_before_finished_at():
    """started_at 解析后必须早于 finished_at。"""
    run = AgentRun(
        run_id="g" * 32,
        input="x",
        selected_tool="echo",
        tool_args={"message": "x"},
        tool_result="x",
        status="completed",
        error=None,
        started_at="2026-06-10T08:00:00+00:00",
        finished_at="2026-06-10T08:00:00.100+00:00",
    )

    assert _parse_iso(run.started_at) < _parse_iso(run.finished_at)


def test_agent_run_tool_args_structure():
    """tool_args 必须是 dict,只含 message 一个键,且值等于 input。"""
    run = AgentRun(
        run_id="h" * 32,
        input="12 * 8",
        selected_tool="calculator",
        tool_args={"message": "12 * 8"},
        tool_result=96,
        status="completed",
        error=None,
        started_at="2026-06-10T08:00:00+00:00",
        finished_at="2026-06-10T08:00:00+00:00",
    )

    assert run.tool_args == {"message": "12 * 8"}
    assert run.tool_args["message"] == run.input


# ---------- TimelineEvent + AgentRun.timeline ----------


def test_timeline_event_now_factory_uses_utc_iso():
    """TimelineEvent.now() 用 UTC ISO 8601 时间戳(客户端解析不抛)。"""
    ev = TimelineEvent.now("test_event")
    assert ev.name == "test_event"
    # 能被 datetime.fromisoformat 解析就是合法 ISO 8601
    datetime.fromisoformat(ev.at)
    assert ev.detail is None  # 不传 detail 默认 None


def test_timeline_event_now_with_detail():
    """TimelineEvent.now(name, detail) 把 detail 一起带过去(给运维/告警用)。"""
    ev = TimelineEvent.now("tool_failed", detail="TOOL_EXECUTION_ERROR")
    assert ev.name == "tool_failed"
    assert ev.detail == "TOOL_EXECUTION_ERROR"


def test_agent_run_timeline_defaults_to_empty():
    """AgentRun 不传 timeline 时是空列表(向后兼容,旧代码构造不破)。"""
    run = AgentRun(
        run_id="i" * 32,
        input="x",
        selected_tool="echo",
        tool_args={},
        tool_result="x",
        status="completed",
        error=None,
        started_at="2026-06-10T08:00:00+00:00",
        finished_at="2026-06-10T08:00:00.001+00:00",
    )
    assert run.timeline == []


def test_agent_run_with_timeline_construction():
    """AgentRun 可以带 timeline 构造(诊断视图直接用)。"""
    timeline = [
        TimelineEvent(name="request_received", at="2026-06-10T08:00:00+00:00"),
        TimelineEvent(name="response_returned", at="2026-06-10T08:00:00.001+00:00"),
    ]
    run = AgentRun(
        run_id="j" * 32,
        input="x",
        selected_tool="echo",
        tool_args={},
        tool_result="x",
        status="completed",
        error=None,
        started_at="2026-06-10T08:00:00+00:00",
        finished_at="2026-06-10T08:00:00.001+00:00",
        timeline=timeline,
    )
    assert len(run.timeline) == 2
    assert run.timeline[0].name == "request_received"
    assert run.timeline[1].name == "response_returned"
