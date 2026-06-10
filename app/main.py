import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import app.tools  # noqa: F401  # 触发工具自注册
from app.agent_run_store import get_run, init_db, list_runs
from app.dispatcher import dispatch
from app.errors import CODE_TO_STATUS
from app.models import RunRequest

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


# ---------- 响应构造辅助 ----------


def _run_response_body(run: dict[str, Any]) -> dict[str, Any]:
    """把 store 返回的 dict(已经是 AgentRun 形态)统一组装成 HTTP 响应需要的 9 字段。

    成功 / 失败 / list item / GET single 都走这里,保证 API 端响应形态完全一致。
    """
    return {
        "run_id": run["run_id"],
        "input": run["input"],
        "selected_tool": run["selected_tool"],
        "tool_args": run["tool_args"],
        "status": run["status"],
        "tool_result": run["tool_result"],
        "error": run["error"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
    }


# ---------- 路由 ----------


@app.post("/agent/run")
def run_agent(req: RunRequest):
    result = dispatch(req.tool, req.message)
    run = result.run

    if run.error is None:
        return _run_response_body({
            "run_id": run.run_id,
            "input": run.input,
            "selected_tool": run.selected_tool,
            "tool_args": run.tool_args,
            "status": run.status,
            "tool_result": run.tool_result,
            "error": None,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        })

    status_code = CODE_TO_STATUS.get(run.error.code, 500)

    return JSONResponse(
        status_code=status_code,
        content=_run_response_body({
            "run_id": run.run_id,
            "input": run.input,
            "selected_tool": run.selected_tool,
            "tool_args": run.tool_args,
            "status": run.status,
            "tool_result": None,
            "error": {
                "code": run.error.code,
                "message": run.error.message,
            },
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        }),
    )


@app.get("/agent/runs")
def list_agent_runs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    runs = list_runs(limit=limit, offset=offset)
    return {
        "runs": [_run_response_body(r) for r in runs],
        "limit": limit,
        "offset": offset,
    }


@app.get("/agent/runs/{run_id}")
def get_agent_run(run_id: str):
    run = get_run(run_id)

    if run is not None:
        return _run_response_body(run)

    return JSONResponse(
        status_code=404,
        content={
            "error": {
                "code": "RUN_NOT_FOUND",
                "message": f"run '{run_id}' was not found",
            }
        },
    )
