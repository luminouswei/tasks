"""结构化运行诊断:把 AgentRun 翻译成可直接给人看的"这次 run 怎么样"。

设计原则:
- **纯函数 / 不耦合业务执行**。dispatcher 只往 AgentRun.timeline 追加事件,
  不知道 failure_type / summary 怎么写;本模块只读 AgentRun,产出 Diagnostics。
  改判定规则(比如新增一种 failure_type)只动这一个文件,业务执行链路不变。
- **failure_type 是闭集**。5 个可枚举值 + None(成功且无降级),见 FailureType。
  任意 error.code 都能被分类;不在映射表里的走 "unknown",绝不会无中生有。
- **失败信息脱敏**。failure_message 优先用 AgentRun.error.message(已脱敏),
  unknown 类型只用 "internal server error",不暴露原始异常类名或堆栈。
  diagnostics 端点是给运维 / 开发者看的 debug 入口,不是给最终用户看的;
  但**仍然不**把"RuntimeError: <class_name>"这种字符串原样返给客户端。
"""
from dataclasses import dataclass, field
from typing import Literal

from app.agent_run import AgentRun, AgentWarning

# 5 闭集 + None。review 时会审"failure_type 是否稳定、可枚举",
# 所以这里用 Literal 显式枚举,任何拼写错误都会被静态分析抓住。
FailureType = Literal[
    "validation_error",   # 用户责任:tool_not_found / tool_input_error
    "tool_error",         # 工具责任:tool_execution_error
    "serialization_error",  # 工具跑通但 result 不可 JSON 序列化(降级,run 仍 success)
    "persistence_error",  # trace 落库失败
    "unknown",            # 系统非预期 / 任何没匹配上的 code
]

# error.code -> failure_type 的查表。
# 跟 agent_run.py / errors.py 里的常量一一对应,缺失的 code 在 classify_failure_type 里
# 显式落 "unknown",保证映射是 total function。
_ERROR_CODE_TO_FAILURE_TYPE: dict[str, FailureType] = {
    "TOOL_NOT_FOUND": "validation_error",
    "TOOL_INPUT_ERROR": "validation_error",
    "TOOL_EXECUTION_ERROR": "tool_error",
    "INTERNAL_ERROR": "unknown",
    "TRACE_PERSIST_FAILED": "persistence_error",
}

# warning.code -> failure_type。
# warning 表示"业务上成功但有 caveat",所以 serialization_error 只在有 warning 时出现。
_WARNING_CODE_TO_FAILURE_TYPE: dict[str, FailureType] = {
    "TOOL_RESULT_SERIALIZATION_FALLBACK": "serialization_error",
}

# failure_type -> 运维/开发者下一步建议。
# 跟 diagnostic_summary 区分:summary 描述这次发生了什么,suggested_action 描述接下来该做什么。
SUGGESTED_ACTIONS: dict[FailureType, str] = {
    "validation_error": "check tool name and tool input parameters",
    "tool_error": "inspect tool health and recent tool logs",
    "serialization_error": "verify tool returns JSON-serializable values",
    "persistence_error": "inspect persistence backend and retry trace insert",
    "unknown": "review server logs and stack trace",
}


def classify_failure_type(
    error_code: str | None,
    warnings: list[AgentWarning],
) -> FailureType | None:
    """根据 error_code 和 warnings 给这次 run 分类。

    优先级:error.code > warning.code > None(成功且无降级)。
    - 有 error -> 失败类型(error 优先,即使 run 业务上"成功"但 status=trace_persist_failed)
    - 无 error 但有 TOOL_RESULT_SERIALIZATION_FALLBACK warning -> serialization_error
    - 无 error 无 warning -> None(干净成功)
    """
    if error_code is not None:
        return _ERROR_CODE_TO_FAILURE_TYPE.get(error_code, "unknown")
    for w in warnings:
        if w.code in _WARNING_CODE_TO_FAILURE_TYPE:
            return _WARNING_CODE_TO_FAILURE_TYPE[w.code]
    return None


def _safe_error_message(error_code: str | None, error_message: str | None) -> str | None:
    """诊断视图的 failure_message:复用 error.message 的脱敏规则。

    - INTERNAL_ERROR: 已经统一脱敏成 "internal server error",**不**再放原始异常。
    - 其它 code:直接用 error.message(diagnostics 端点是 debug 用的,给运维看可以详细一点)。
    - 没有 error:None(成功 run 不该有 failure_message)。
    """
    if error_code is None or error_message is None:
        return None
    return error_message


def _format_duration_ms(started_at: str, finished_at: str) -> int | None:
    """算耗时(ms)。解析失败返 None,不抛异常影响主流程。"""
    from datetime import datetime
    try:
        s = datetime.fromisoformat(started_at)
        f = datetime.fromisoformat(finished_at)
    except ValueError:
        return None
    delta_ms = (f - s).total_seconds() * 1000
    return max(0, int(delta_ms))


def build_diagnostic_summary(
    run: AgentRun,
    failure_type: FailureType | None,
) -> str:
    """给运维/开发者读的一段话,说明这次 run 怎么样了。

    失败信息来自 error.message(已脱敏),不动原始 exception 类名。
    成功时附带耗时和工具名,方便快速判断"这次跑得快不快"。
    """
    duration_ms = _format_duration_ms(run.started_at, run.finished_at)
    duration_part = f" in {duration_ms}ms" if duration_ms is not None else ""
    tool_part = f" via {run.selected_tool}" if run.selected_tool else ""

    if failure_type is None:
        return f"Run completed{duration_part}{tool_part}."

    error_msg = run.error.message if run.error is not None else None

    if failure_type == "validation_error":
        return f"Run failed at validation{tool_part}: {error_msg or 'invalid request'}."

    if failure_type == "tool_error":
        return f"Run failed during tool execution{tool_part}: {error_msg or 'tool execution failed'}."

    if failure_type == "serialization_error":
        return (
            f"Run completed{tool_part} with serialization fallback; "
            "tool result is a safe repr."
        )

    if failure_type == "persistence_error":
        return (
            "Tool execution completed but trace persistence failed; "
            "trace may be lost."
        )

    # failure_type == "unknown" / 兜底
    # 显式不暴露原始 exception 类名,只用脱敏后的 message
    return f"Run failed with internal error: {error_msg or 'internal server error'}."


@dataclass
class Diagnostics:
    """一次 run 的结构化诊断视图。

    字段契约:
    - run_id / status: 跟 AgentRun 一致
    - failure_type: 5 闭集之一,或者 None(成功且无降级)
    - failure_message: 给运维看的失败原因,已脱敏;成功为 None
    - timeline: 按时间顺序的关键事件点,每条 {name, at, detail}
    - diagnostic_summary: 给人读的一句话
    - suggested_action: failure_type 对应的下一步建议;成功为 None

    这是**只读**视图,跟 AgentRun 字段不重复储存(Diagnostics 在请求时由
    build_diagnostics 现场生成),改动判定规则只动本文件。
    """
    run_id: str
    status: str
    failure_type: FailureType | None
    failure_message: str | None
    timeline: list[dict] = field(default_factory=list)
    diagnostic_summary: str = ""
    suggested_action: str | None = None


def build_diagnostics(run: AgentRun) -> Diagnostics:
    """把 AgentRun 翻译成 Diagnostics 视图。

    纯函数:不读 DB / 不发请求 / 不改 run;同样的 run 两次调用结果一致。
    """
    error_code = run.error.code if run.error is not None else None

    failure_type = classify_failure_type(error_code, run.warnings)
    summary = build_diagnostic_summary(run, failure_type)
    failure_message = _safe_error_message(
        error_code,
        run.error.message if run.error is not None else None,
    )
    suggested_action = (
        SUGGESTED_ACTIONS.get(failure_type) if failure_type is not None else None
    )

    # timeline 转成 dict 列表(JSON 友好);dataclass 不直接序列化,需要中间 dict
    timeline_dicts = [
        {"name": ev.name, "at": ev.at, "detail": ev.detail}
        for ev in run.timeline
    ]

    return Diagnostics(
        run_id=run.run_id,
        status=run.status,
        failure_type=failure_type,
        failure_message=failure_message,
        timeline=timeline_dicts,
        diagnostic_summary=summary,
        suggested_action=suggested_action,
    )
