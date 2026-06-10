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
