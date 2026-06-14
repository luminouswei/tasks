import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from app.agent_run import AgentErrorPayload, AgentRun, AgentWarning, RunStatus, TimelineEvent
from app.agent_run_store import (
    insert_run,
    serialize_tool_result_with_fallback,
)
from app.errors import ToolExecutionError, ToolInputError
from app.registry import get_tool

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    run: AgentRun


_SERIALIZATION_FALLBACK_WARNING = AgentWarning(
    code="TOOL_RESULT_SERIALIZATION_FALLBACK",
    message="tool result was not JSON-serializable; stored a safe repr",
)


def dispatch(tool_name: str, message: str) -> DispatchResult:
    run_id = uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()

    # timeline 在 dispatch 入口就开始记录,客户端从 timeline 第一条
    # ("request_received")就能看到这次调用是什么时候进来的。
    timeline: list[TimelineEvent] = [
        TimelineEvent.now("request_received", detail=f"tool={tool_name}")
    ]

    tool_args = {"message": message}

    tool_result: Any = None
    error: AgentErrorPayload | None = None
    status: RunStatus = "completed"
    warnings: list[AgentWarning] = []

    try:
        spec = get_tool(tool_name)
    except KeyError:
        status = "failed"
        error = AgentErrorPayload(
            code="TOOL_NOT_FOUND",
            message=f"tool '{tool_name}' is not registered",
        )
        # get_tool 抛 KeyError 也记到 timeline(失败路径也保留),
        # 客户端一眼能看出"卡在路由"而不是"工具跑挂了"。
        timeline.append(
            TimelineEvent.now("validation_failed", detail="TOOL_NOT_FOUND")
        )
    else:
        timeline.append(TimelineEvent.now("validation_passed"))
        try:
            timeline.append(
                TimelineEvent.now("tool_dispatch_started", detail=tool_name)
            )
            tool_result = spec.run(message)
            status = "completed"
            timeline.append(TimelineEvent.now("tool_executed"))
        except ToolInputError as exc:
            # 用户责任:非法参数。error.code 走 TOOL_INPUT_ERROR,HTTP 400。
            status = "failed"
            error = AgentErrorPayload(
                code="TOOL_INPUT_ERROR",
                message=exc.message,
            )
            timeline.append(
                TimelineEvent.now("tool_failed", detail="TOOL_INPUT_ERROR")
            )
        except ToolExecutionError as exc:
            # 工具责任:工具内部崩。error.code 走 TOOL_EXECUTION_ERROR,HTTP 500。
            status = "failed"
            error = AgentErrorPayload(
                code="TOOL_EXECUTION_ERROR",
                message=exc.message,
            )
            timeline.append(
                TimelineEvent.now("tool_failed", detail="TOOL_EXECUTION_ERROR")
            )
        except Exception:
            # 未预期异常:系统责任(非工具也非用户)。HTTP 500,error.message 脱敏。
            logger.exception("tool execution failed")
            status = "failed"
            error = AgentErrorPayload(
                code="INTERNAL_ERROR",
                message="internal server error",
            )
            timeline.append(
                TimelineEvent.now("tool_failed", detail="INTERNAL_ERROR")
            )

    finished_at = datetime.now(timezone.utc).isoformat()

    # 检测 tool_result 是否走序列化降级(只针对成功 run;失败 run tool_result=None)
    if status == "completed" and tool_result is not None:
        _, did_fallback = serialize_tool_result_with_fallback(tool_result)
        if did_fallback:
            warnings.append(_SERIALIZATION_FALLBACK_WARNING)
            timeline.append(
                TimelineEvent.now(
                    "trace_serialization_fallback",
                    detail=type(tool_result).__name__,
                )
            )
        else:
            timeline.append(TimelineEvent.now("trace_serialized"))

    # 业务执行已经完成(成功 / 业务失败 / 内部错误,3 种都算"执行过了")。
    # trace 落库是观测动作,不应该反过来影响业务结果的可得性:
    # 失败时把异常摘要带回去,主调用方可以决定告警/重试,但仍然能拿到业务结果。
    # **DB 里** 只写 completed / failed(2 值);"trace_persist_failed" 是
    # dispatcher 抛异常时临时盖在 in-memory run 上的标志,只走响应/日志。
    #
    # **关键**:timeline 在 insert_run 之前**完整拼好**(包含 trace_persisted +
    # response_returned),作为整体写进 DB。这样 GET /agent/runs/{id}/diagnostics
    # 拿到的 timeline 跟在 dispatch 内存里看到的完全一致,不会出现"DB 里的
    # timeline 比响应里的少 2 条"这种漂移。
    # timestamp 比真实动作略早几微秒,可以接受(diagnostics 不要求高精度)。
    # 如果 insert 失败,就把最后 2 条改成 trace_persist_failed + response_returned,
    # in-memory timeline 仍然保持完整(in-memory 只在响应/log 用,不入 DB)。
    timeline.append(TimelineEvent.now("trace_persisted"))
    timeline.append(TimelineEvent.now("response_returned"))

    persisted = True
    trace_error: str | None = None

    try:
        insert_run({
            "run_id": run_id,
            "input": message,
            "selected_tool": tool_name,
            "tool_args": tool_args,
            "tool_result": tool_result,
            "status": status,
            "error_code": error.code if error is not None else None,
            "error_message": error.message if error is not None else None,
            "warnings": warnings,
            "timeline": timeline,
            "started_at": started_at,
            "finished_at": finished_at,
        })
    except Exception as exc:
        logger.exception("trace insert failed run_id=%s", run_id)
        persisted = False
        trace_error = f"{type(exc).__name__}: {exc}"
        # DB 没这一行。把 in-memory timeline 最后 2 条改成失败标记——
        # 客户端拿到的响应 / 日志 timeline 仍然完整,只是尾部事件反映了真实结果。
        # detail 用异常类名(operator-facing),不暴露堆栈或 DB 路径。
        timeline[-2] = TimelineEvent.now(
            "trace_persist_failed", detail=type(exc).__name__
        )
        # timeline[-1] 已经是 response_returned,保留

    # 落库失败时,业务 status 保留(已执行完),同时盖上 trace_persist_failed
    # 让 main.py 翻译成 500 + TRACE_PERSIST_FAILED。warnings 保留但不入 DB。
    final_status: RunStatus = "trace_persist_failed" if not persisted else status
    final_error: AgentErrorPayload | None = error
    if not persisted:
        final_error = AgentErrorPayload(
            code="TRACE_PERSIST_FAILED",
            message=trace_error or "trace persistence failed",
        )

    run = AgentRun(
        run_id=run_id,
        input=message,
        selected_tool=tool_name,
        tool_args=tool_args,
        tool_result=tool_result,
        status=final_status,
        error=final_error,
        started_at=started_at,
        finished_at=finished_at,
        warnings=warnings,
        timeline=timeline,
    )

    return DispatchResult(run=run)
