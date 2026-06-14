from dataclasses import dataclass
from typing import Any, Literal

# 跟 app/errors.py 的几个 code 一一对应,作为 status 字段的字面量约束。
RunStatus = Literal[
    "success",
    "tool_not_found",
    "tool_error",
    "internal_error",
]


@dataclass
class AgentErrorPayload:
    code: str
    message: str


@dataclass
class AgentRun:
    run_id: str
    input: str
    selected_tool: str
    tool_args: dict[str, Any]
    tool_result: Any | None
    status: RunStatus
    error: AgentErrorPayload | None
    started_at: str
    finished_at: str
    # trace_persistence 描述"业务执行成功后,trace 是否成功落库"。
    # "ok" = insert_run 成功;"failed" = 抛了异常(dispatcher 仍把业务结果带回来,主调用方拿得到)。
    # 跟 status 字段正交:status 描述业务结果(成功/失败),trace_persistence 描述可观测性落库结果。
    trace_persistence: Literal["ok", "failed"] = "ok"
    trace_error: str | None = None
