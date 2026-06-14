from dataclasses import dataclass, field
from datetime import datetime, timezone
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


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串。timeline event 的时间戳都走这里,保证格式一致。"""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TimelineEvent:
    """agent run 生命周期中的一个关键节点,按执行顺序追加到 AgentRun.timeline。

    名字(name)是固定枚举,见 app/diagnostics.py 里的命名约定;
    时间戳(at)用 ISO 8601 UTC;detail 是可选的轻量上下文(短字符串,不存堆栈/大对象,
    避免 timeline 把 trace 体积撑爆,也不泄露敏感内容)。
    """
    name: str
    at: str
    detail: str | None = None

    @classmethod
    def now(cls, name: str, detail: str | None = None) -> "TimelineEvent":
        """快速构造:用当前 UTC 时间戳。dispatcher 7 个事件点都走这个。"""
        return cls(name=name, at=_now_iso(), detail=detail)


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
    # timeline: 这次 run 按时间顺序的关键事件点(诊断 / 可观测用)。
    # 失败分支也记录(比如 tool_not_found 也会记 validation_failed),保证
    # 客户端从 timeline 能直接定位失败发生在哪个阶段,不必去日志里翻。
    # 7 类固定事件名见 app/diagnostics.py 顶部的 EVENT_NAMES 注释。
    timeline: list[TimelineEvent] = field(default_factory=list)
