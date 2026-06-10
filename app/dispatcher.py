import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.errors import ToolExecutionError
from app.registry import get_tool
from app.trace_store import insert_trace

logger = logging.getLogger(__name__)


@dataclass
class ErrorPayload:
    code: str
    message: str


@dataclass
class DispatchResult:
    trace_id: str
    tool: str
    result: Any
    error: ErrorPayload | None
    latency_ms: int


def dispatch(tool_name: str, message: str) -> DispatchResult:
    trace_id = uuid4().hex
    start = perf_counter()

    result: Any = None
    error: ErrorPayload | None = None

    try:
        spec = get_tool(tool_name)
    except KeyError:
        error = ErrorPayload(
            code="TOOL_NOT_FOUND",
            message=f"tool '{tool_name}' is not registered",
        )
    else:
        try:
            result = spec.run(message)
        except ToolExecutionError as exc:
            error = ErrorPayload(
                code="TOOL_EXECUTION_ERROR",
                message=exc.message,
            )
        except Exception:
            logger.exception("tool execution failed")
            error = ErrorPayload(
                code="INTERNAL_ERROR",
                message="internal server error",
            )

    latency_ms = int((perf_counter() - start) * 1000)

    try:
        insert_trace(
            {
                "trace_id": trace_id,
                "tool": tool_name,
                "input": message,
                "output": result if error is None else None,
                "error_code": error.code if error is not None else None,
                "error_msg": error.message if error is not None else None,
                "latency_ms": latency_ms,
            }
        )
    except Exception:
        logger.exception("trace insert failed")

    return DispatchResult(
        trace_id=trace_id,
        tool=tool_name,
        result=result,
        error=error,
        latency_ms=latency_ms,
    )