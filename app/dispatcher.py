import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from app.agent_run import AgentErrorPayload, AgentRun, AgentWarning, RunStatus
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
    else:
        try:
            tool_result = spec.run(message)
            status = "completed"
        except ToolInputError as exc:
            # 用户责任:非法参数。error.code 走 TOOL_INPUT_ERROR,HTTP 400。
            status = "failed"
            error = AgentErrorPayload(
                code="TOOL_INPUT_ERROR",
                message=exc.message,
            )
        except ToolExecutionError as exc:
            # 工具责任:工具内部崩。error.code 走 TOOL_EXECUTION_ERROR,HTTP 500。
            status = "failed"
            error = AgentErrorPayload(
                code="TOOL_EXECUTION_ERROR",
                message=exc.message,
            )
        except Exception:
            # 未预期异常:系统责任(非工具也非用户)。HTTP 500,error.message 脱敏。
            logger.exception("tool execution failed")
            status = "failed"
            error = AgentErrorPayload(
                code="INTERNAL_ERROR",
                message="internal server error",
            )

    finished_at = datetime.now(timezone.utc).isoformat()

    # 检测 tool_result 是否走序列化降级(只针对成功 run;失败 run tool_result=None)
    if status == "completed" and tool_result is not None:
        _, did_fallback = serialize_tool_result_with_fallback(tool_result)
        if did_fallback:
            warnings.append(_SERIALIZATION_FALLBACK_WARNING)

    # 业务执行已经完成(成功 / 业务失败 / 内部错误,3 种都算"执行过了")。
    # trace 落库是观测动作,不应该反过来影响业务结果的可得性:
    # 失败时把异常摘要带回去,主调用方可以决定告警/重试,但仍然能拿到业务结果。
    # **DB 里** 只写 completed / failed(2 值);"trace_persist_failed" 是
    # dispatcher 抛异常时临时盖在 in-memory run 上的标志,只走响应/日志。
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
            "started_at": started_at,
            "finished_at": finished_at,
        })
    except Exception as exc:
        logger.exception("trace insert failed run_id=%s", run_id)
        persisted = False
        trace_error = f"{type(exc).__name__}: {exc}"

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
    )

    return DispatchResult(run=run)
