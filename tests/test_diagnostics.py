"""app/diagnostics.py 的纯函数测试。

build_diagnostics 是纯函数,只读 AgentRun,产 Diagnostics。改判定规则只动
app/diagnostics.py,这些测试就是契约。"""
from app.agent_run import AgentErrorPayload, AgentRun, AgentWarning, TimelineEvent
from app.diagnostics import (
    Diagnostics,
    build_diagnostics,
    build_diagnostic_summary,
    classify_failure_type,
    SUGGESTED_ACTIONS,
)


# ---------- helpers ----------

def _make_run(
    *,
    run_id: str = "a" * 32,
    input: str = "x",
    selected_tool: str = "echo",
    tool_result=None,
    status: str = "completed",
    error: AgentErrorPayload | None = None,
    started_at: str = "2026-06-14T00:00:00+00:00",
    finished_at: str = "2026-06-14T00:00:00.005+00:00",
    warnings: list[AgentWarning] | None = None,
    timeline: list[TimelineEvent] | None = None,
) -> AgentRun:
    return AgentRun(
        run_id=run_id,
        input=input,
        selected_tool=selected_tool,
        tool_args={"message": input},
        tool_result=tool_result,
        status=status,
        error=error,
        started_at=started_at,
        finished_at=finished_at,
        warnings=warnings or [],
        timeline=timeline or [],
    )


# ---------- classify_failure_type 查表全 5 闭集 + 边界 ----------

def test_classify_validation_error_tool_not_found():
    """TOOL_NOT_FOUND → validation_error(用户责任:工具名错)。"""
    assert classify_failure_type("TOOL_NOT_FOUND", []) == "validation_error"


def test_classify_validation_error_tool_input_error():
    """TOOL_INPUT_ERROR → validation_error(用户责任:参数错)。"""
    assert classify_failure_type("TOOL_INPUT_ERROR", []) == "validation_error"


def test_classify_tool_error():
    """TOOL_EXECUTION_ERROR → tool_error(工具责任)。"""
    assert classify_failure_type("TOOL_EXECUTION_ERROR", []) == "tool_error"


def test_classify_persistence_error():
    """TRACE_PERSIST_FAILED → persistence_error(落库失败,业务可能已成功)。"""
    assert classify_failure_type("TRACE_PERSIST_FAILED", []) == "persistence_error"


def test_classify_unknown_internal_error():
    """INTERNAL_ERROR → unknown(系统崩,信息已脱敏)。"""
    assert classify_failure_type("INTERNAL_ERROR", []) == "unknown"


def test_classify_unknown_unmapped_code():
    """未来新加的 error.code 没在查表里 → 兜底 unknown,不会抛。"""
    assert classify_failure_type("FUTURE_CODE_9999", []) == "unknown"


def test_classify_none_clean_success():
    """无 error 无 warning → None,clean success。"""
    assert classify_failure_type(None, []) is None


def test_classify_serialization_error_from_warning():
    """无 error 但有 TOOL_RESULT_SERIALIZATION_FALLBACK warning → serialization_error。

    注意:此时 run 业务上 status=completed,但 diagnostics 把"曾走过降级"标出来,
    运维看 failure_type 立刻知道这条 run 的 tool_result 是 fallback 形态。
    """
    warnings = [AgentWarning(
        code="TOOL_RESULT_SERIALIZATION_FALLBACK",
        message="tool result was not JSON-serializable; stored a safe repr",
    )]
    assert classify_failure_type(None, warnings) == "serialization_error"


def test_classify_error_takes_priority_over_warning():
    """error 优先于 warning:即使有 serialization warning,error.code 决定分类。"""
    warnings = [AgentWarning(
        code="TOOL_RESULT_SERIALIZATION_FALLBACK",
        message="...",
    )]
    assert classify_failure_type("TOOL_EXECUTION_ERROR", warnings) == "tool_error"


# ---------- build_diagnostic_summary 5 个 failure_type + 成功 ----------

def test_summary_success():
    """成功 run:含耗时和工具名。"""
    run = _make_run(
        started_at="2026-06-14T00:00:00+00:00",
        finished_at="2026-06-14T00:00:00.005+00:00",
        selected_tool="calculator",
    )
    summary = build_diagnostic_summary(run, None)
    assert "completed" in summary
    assert "5ms" in summary
    assert "calculator" in summary


def test_summary_validation_error_includes_error_message():
    """validation_error summary 带出原始错误信息(已脱敏 / 用户责任)。"""
    run = _make_run(
        error=AgentErrorPayload(code="TOOL_INPUT_ERROR", message="division by zero"),
    )
    summary = build_diagnostic_summary(run, "validation_error")
    assert "validation" in summary
    assert "division by zero" in summary


def test_summary_tool_error():
    """tool_error summary 强调是工具责任。"""
    run = _make_run(
        error=AgentErrorPayload(code="TOOL_EXECUTION_ERROR", message="API timed out"),
    )
    summary = build_diagnostic_summary(run, "tool_error")
    assert "tool execution" in summary
    assert "API timed out" in summary


def test_summary_serialization_error():
    """serialization_error summary 说明是降级,不是真失败。"""
    run = _make_run(warnings=[AgentWarning(
        code="TOOL_RESULT_SERIALIZATION_FALLBACK",
        message="...",
    )])
    summary = build_diagnostic_summary(run, "serialization_error")
    assert "serialization" in summary.lower() or "fallback" in summary.lower()


def test_summary_persistence_error_mentions_trace_lost():
    """persistence_error summary 提示 trace 可能丢了(运维据此决定重试)。"""
    run = _make_run(
        status="trace_persist_failed",
        error=AgentErrorPayload(code="TRACE_PERSIST_FAILED", message="disk full"),
        tool_result="x",  # 业务结果保留
    )
    summary = build_diagnostic_summary(run, "persistence_error")
    assert "persistence" in summary or "trace" in summary
    # 不应该泄露具体异常类名到 summary(脱敏)
    assert "RuntimeError" not in summary
    assert "disk full" not in summary  # persistence summary 用了通用模板


def test_summary_unknown_uses_sanitized_message():
    """unknown summary 不暴露原始异常类名 / 堆栈。"""
    run = _make_run(
        error=AgentErrorPayload(code="INTERNAL_ERROR", message="internal server error"),
    )
    summary = build_diagnostic_summary(run, "unknown")
    # 用了 error.message 字段,该字段已经脱敏成 "internal server error"
    assert "internal server error" in summary
    # 不该凭空泄露的关键词
    assert "secret" not in summary
    assert "ValueError" not in summary


# ---------- suggested_action 查表完整覆盖 ----------

def test_suggested_actions_cover_all_failure_types():
    """SUGGESTED_ACTIONS 字典覆盖全部 5 个 failure_type,运维看到 failure_type 就能给建议。"""
    for ft in ("validation_error", "tool_error", "serialization_error", "persistence_error", "unknown"):
        assert ft in SUGGESTED_ACTIONS
        assert isinstance(SUGGESTED_ACTIONS[ft], str)
        assert SUGGESTED_ACTIONS[ft]  # 非空


# ---------- build_diagnostics 端到端 ----------

def test_build_diagnostics_success_returns_none_failure_type():
    """成功 run → failure_type=None,failure_message=None,suggested_action=None,
    summary 是 "completed ..." 句式。"""
    run = _make_run(tool_result="x")
    d = build_diagnostics(run)

    assert isinstance(d, Diagnostics)
    assert d.run_id == run.run_id
    assert d.status == "completed"
    assert d.failure_type is None
    assert d.failure_message is None
    assert d.suggested_action is None
    assert "completed" in d.diagnostic_summary
    assert d.timeline == []  # 没 timeline 不报错


def test_build_diagnostics_includes_timeline():
    """timeline 转成 dict 列表形态(JSON 友好)。"""
    timeline = [
        TimelineEvent(name="request_received", at="2026-06-14T00:00:00+00:00", detail="tool=echo"),
        TimelineEvent(name="response_returned", at="2026-06-14T00:00:00.001+00:00"),
    ]
    run = _make_run(tool_result="x", timeline=timeline)
    d = build_diagnostics(run)

    assert len(d.timeline) == 2
    assert d.timeline[0] == {
        "name": "request_received",
        "at": "2026-06-14T00:00:00+00:00",
        "detail": "tool=echo",
    }
    assert d.timeline[1]["detail"] is None  # 缺省 detail → None


def test_build_diagnostics_tool_execution_error():
    """TOOL_EXECUTION_ERROR → failure_type=tool_error,带出原始 message 和建议。"""
    run = _make_run(
        status="failed",
        error=AgentErrorPayload(code="TOOL_EXECUTION_ERROR", message="weather API timed out"),
    )
    d = build_diagnostics(run)

    assert d.failure_type == "tool_error"
    assert d.failure_message == "weather API timed out"
    assert d.suggested_action == "inspect tool health and recent tool logs"
    assert "tool execution" in d.diagnostic_summary


def test_build_diagnostics_tool_input_error():
    """TOOL_INPUT_ERROR → failure_type=validation_error。"""
    run = _make_run(
        status="failed",
        error=AgentErrorPayload(code="TOOL_INPUT_ERROR", message="division by zero"),
    )
    d = build_diagnostics(run)

    assert d.failure_type == "validation_error"
    assert d.failure_message == "division by zero"
    assert d.suggested_action == "check tool name and tool input parameters"


def test_build_diagnostics_tool_not_found():
    """TOOL_NOT_FOUND 也走 validation_error(工具名拼错 = 用户责任)。"""
    run = _make_run(
        status="failed",
        error=AgentErrorPayload(code="TOOL_NOT_FOUND", message="tool 'weather' is not registered"),
    )
    d = build_diagnostics(run)

    assert d.failure_type == "validation_error"
    assert "weather" in d.failure_message


def test_build_diagnostics_trace_persist_failed():
    """TRACE_PERSIST_FAILED → persistence_error,failure_message 保留 trace 异常摘要。"""
    run = _make_run(
        status="trace_persist_failed",
        error=AgentErrorPayload(code="TRACE_PERSIST_FAILED", message="RuntimeError: disk full"),
        tool_result="x",  # 业务结果保留
    )
    d = build_diagnostics(run)

    assert d.failure_type == "persistence_error"
    assert d.failure_message == "RuntimeError: disk full"
    assert d.suggested_action == "inspect persistence backend and retry trace insert"


def test_build_diagnostics_serialization_fallback_warning():
    """成功 run + serialization warning → failure_type=serialization_error,status 仍 completed。"""
    run = _make_run(
        tool_result={"_serialization": "fallback", "type": "set", "repr": "{1,2,3}"},
        status="completed",
        warnings=[AgentWarning(
            code="TOOL_RESULT_SERIALIZATION_FALLBACK",
            message="...",
        )],
    )
    d = build_diagnostics(run)

    assert d.failure_type == "serialization_error"
    assert d.status == "completed"  # 业务上是成功的
    assert d.suggested_action == "verify tool returns JSON-serializable values"


def test_build_diagnostics_is_pure():
    """build_diagnostics 纯函数:同样输入两次调用结果完全一致,不修改 run。"""
    run = _make_run(
        tool_result="x",
        timeline=[TimelineEvent(name="x", at="2026-06-14T00:00:00+00:00")],
    )
    d1 = build_diagnostics(run)
    d2 = build_diagnostics(run)

    assert d1.failure_type == d2.failure_type
    assert d1.timeline == d2.timeline
    assert d1.diagnostic_summary == d2.diagnostic_summary
    # run 自身没被修改
    assert len(run.timeline) == 1
