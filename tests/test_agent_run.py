from datetime import datetime

from app.agent_run import AgentErrorPayload, AgentRun


def _parse_iso(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


def test_agent_run_success_construction():
    """成功 run:status=success,tool_result 填值,error 为 None。"""
    run = AgentRun(
        run_id="a" * 32,
        input="12 * 8",
        selected_tool="calculator",
        tool_args={"message": "12 * 8"},
        tool_result=96,
        status="success",
        error=None,
        started_at="2026-06-10T08:00:00+00:00",
        finished_at="2026-06-10T08:00:00.005+00:00",
    )

    assert run.status == "success"
    assert run.tool_result == 96
    assert run.error is None


def test_agent_run_tool_error_construction():
    """工具执行异常 run:status=tool_error,tool_result=None,error 有 code。"""
    run = AgentRun(
        run_id="b" * 32,
        input="1/0",
        selected_tool="calculator",
        tool_args={"message": "1/0"},
        tool_result=None,
        status="tool_error",
        error=AgentErrorPayload(
            code="TOOL_EXECUTION_ERROR",
            message="division by zero",
        ),
        started_at="2026-06-10T08:00:00+00:00",
        finished_at="2026-06-10T08:00:00.001+00:00",
    )

    assert run.status == "tool_error"
    assert run.tool_result is None
    assert run.error is not None
    assert run.error.code == "TOOL_EXECUTION_ERROR"
    assert run.error.message == "division by zero"


def test_agent_run_tool_not_found_construction():
    """工具不存在 run:status=tool_not_found,error.code=TOOL_NOT_FOUND。"""
    run = AgentRun(
        run_id="c" * 32,
        input="hi",
        selected_tool="weather",
        tool_args={"message": "hi"},
        tool_result=None,
        status="tool_not_found",
        error=AgentErrorPayload(
            code="TOOL_NOT_FOUND",
            message="tool 'weather' is not registered",
        ),
        started_at="2026-06-10T08:00:00+00:00",
        finished_at="2026-06-10T08:00:00+00:00",
    )

    assert run.status == "tool_not_found"
    assert run.error.code == "TOOL_NOT_FOUND"


def test_agent_run_started_at_before_finished_at():
    """started_at 解析后必须早于 finished_at。"""
    run = AgentRun(
        run_id="d" * 32,
        input="x",
        selected_tool="echo",
        tool_args={"message": "x"},
        tool_result="x",
        status="success",
        error=None,
        started_at="2026-06-10T08:00:00+00:00",
        finished_at="2026-06-10T08:00:00.100+00:00",
    )

    assert _parse_iso(run.started_at) < _parse_iso(run.finished_at)


def test_agent_run_tool_args_structure():
    """tool_args 必须是 dict,只含 message 一个键,且值等于 input。"""
    run = AgentRun(
        run_id="e" * 32,
        input="12 * 8",
        selected_tool="calculator",
        tool_args={"message": "12 * 8"},
        tool_result=96,
        status="success",
        error=None,
        started_at="2026-06-10T08:00:00+00:00",
        finished_at="2026-06-10T08:00:00+00:00",
    )

    assert run.tool_args == {"message": "12 * 8"}
    assert run.tool_args["message"] == run.input
