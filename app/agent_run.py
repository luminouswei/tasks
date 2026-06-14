from dataclasses import dataclass, field
from typing import Any, Literal

# status 描述"这次 run 在工程链路里走到哪一步",不是业务结果。
# 3 值:
# - "completed"        业务执行成功 + trace 落库成功(写入 DB)
# - "failed"           业务执行失败 + trace 落库成功(写入 DB;error.code 描述原因)
# - "trace_persist_failed"  业务无论成败,trace 落库失败;
#                           **只放 API response / log,DB 里不保证有对应行**
#
# DB 里只存 completed / failed 两个值,trace_persist_failed 是 dispatcher
# 在 insert_run 抛异常时临时盖在 in-memory AgentRun 对象上的标志,用于翻译响应。
# 客户端看 DB 行永远不会看到 trace_persist_failed。
RunStatus = Literal["completed", "failed", "trace_persist_failed"]


@dataclass
class AgentErrorPayload:
    code: str
    message: str


@dataclass
class AgentWarning:
    """非致命的降级事件。run 业务上成功了(没 error),但有 caveat 需要客户端知道。

    跟 error 区分:
    - error:   run 失败,error.code 解释原因
    - warning: run 成功(没 error),warning.code 解释降级/caveat

    告警/分桶时不要混,WHERE error.code IS NOT NULL 不能把带 warning 的 run 算成失败。
    """
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
    # warnings: 非致命降级事件列表。常见来源:
    # - TOOL_RESULT_SERIALIZATION_FALLBACK: tool_result 不可 JSON 序列化,已走 fallback
    warnings: list[AgentWarning] = field(default_factory=list)
