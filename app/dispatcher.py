import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from app.agent_run import AgentErrorPayload, AgentRun, RunStatus
from app.agent_run_store import insert_run
from app.errors import ToolExecutionError
from app.registry import get_tool

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    run: AgentRun


def dispatch(tool_name: str, message: str) -> DispatchResult:
    run_id = uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()

    tool_args = {"message": message}

    tool_result: Any = None
    error: AgentErrorPayload | None = None
    status: RunStatus

    try:
        spec = get_tool(tool_name)
    except KeyError:
        status = "tool_not_found"
        error = AgentErrorPayload(
            code="TOOL_NOT_FOUND",
            message=f"tool '{tool_name}' is not registered",
        )
    else:
        try:
            tool_result = spec.run(message)
            status = "success"
        except ToolExecutionError as exc:
            status = "tool_error"
            error = AgentErrorPayload(
                code="TOOL_EXECUTION_ERROR",
                message=exc.message,
            )
        except Exception:
            logger.exception("tool execution failed")
            status = "internal_error"
            error = AgentErrorPayload(
                code="INTERNAL_ERROR",
                message="internal server error",
            )

    finished_at = datetime.now(timezone.utc).isoformat()

    # 业务执行已经完成(成功 / 业务失败 / 内部错误,3 种都算"执行过了")。
    # trace 落库是观测动作,不应该反过来影响业务结果的可得性:
    # 失败时把异常摘要带回去,主调用方可以决定告警/重试,但仍然能拿到业务结果。
    trace_persistence: Literal["ok", "failed"] = "ok"
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
            "started_at": started_at,
            "finished_at": finished_at,
        })
    except Exception as exc:
        # logger.exception 已经把 traceback 落日志,这里只把简短摘要带进 run。
        # 主路径(本仓库的 main.py)会把它翻译成 500 + TRACE_PERSIST_FAILED。
        logger.exception("trace insert failed run_id=%s", run_id)
        trace_persistence = "failed"
        trace_error = f"{type(exc).__name__}: {exc}"

    run = AgentRun(
        run_id=run_id,
        input=message,
        selected_tool=tool_name,
        tool_args=tool_args,
        tool_result=tool_result,
        status=status,
        error=error,
        started_at=started_at,
        finished_at=finished_at,
        trace_persistence=trace_persistence,
        trace_error=trace_error,
    )

    return DispatchResult(run=run)
