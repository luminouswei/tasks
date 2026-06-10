import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
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
    )

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
    except Exception:
        logger.exception("trace insert failed")

    return DispatchResult(run=run)
