from datetime import datetime

from app.agent_run import AgentErrorPayload, AgentRun, AgentWarning


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
