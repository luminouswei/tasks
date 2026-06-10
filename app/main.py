import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import app.tools  # noqa: F401  # 触发工具自注册
from app.dispatcher import dispatch
from app.errors import CODE_TO_STATUS
from app.models import RunRequest
from app.trace_store import get_trace, init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(lifespan=lifespan)


def _format_validation_error(exc: RequestValidationError) -> str:
    messages: list[str] = []

    for error in exc.errors():
        loc = ".".join(str(part) for part in error.get("loc", []))
        msg = error.get("msg", "invalid value")

        if loc:
            messages.append(f"{loc}: {msg}")
        else:
            messages.append(msg)

    return "; ".join(messages) or "invalid request"


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "INVALID_REQUEST",
                "message": _format_validation_error(exc),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    logger.exception("unhandled exception")

    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "internal server error",
            }
        },
    )


@app.post("/agent/run")
def run_agent(req: RunRequest):
    result = dispatch(req.tool, req.message)

    if result.error is None:
        return {
            "trace_id": result.trace_id,
            "tool": result.tool,
            "result": result.result,
            "latency_ms": result.latency_ms,
        }

    status_code = CODE_TO_STATUS.get(result.error.code, 500)

    return JSONResponse(
        status_code=status_code,
        content={
            "trace_id": result.trace_id,
            "error": {
                "code": result.error.code,
                "message": result.error.message,
            },
        },
    )

@app.get("/agent/traces/{trace_id}")
def get_agent_trace(trace_id: str):
    trace = get_trace(trace_id)

    if trace is not None:
        return trace

    return JSONResponse(
        status_code=404,
        content={
            "error": {
                "code": "TRACE_NOT_FOUND",
                "message": f"trace '{trace_id}' was not found",
            }
        },
    )