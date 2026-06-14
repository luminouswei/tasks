import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import app.tools  # noqa: F401  # 触发工具自注册
from app.agent_run import AgentRun
from app.agent_run_store import get_run, init_db, list_runs
from app.dispatcher import DispatchResult, dispatch
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


def _agent_run_to_response_dict(run: AgentRun) -> dict[str, Any]:
    """AgentRun -> API 响应 dict(9 字段一致形态)。

    成功时 tool_result 填值、error 为 None;业务失败时 tool_result 强制 None、
    error 为 {code, message};trace 持久化失败时用 TRACE_PERSIST_FAILED
    覆盖 error 字段(优先级高于业务 error,告诉调用方"业务结果拿到了但没记下来")。
    POST 和 replay 走这条路径,GET 走 _run_response_body。
    """
    body: dict[str, Any] = {
        "run_id": run.run_id,
        "input": run.input,
        "selected_tool": run.selected_tool,
        "tool_args": run.tool_args,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }
    if run.trace_persistence == "failed":
        body["error"] = {
            "code": "TRACE_PERSIST_FAILED",
            "message": run.trace_error or "trace persistence failed",
        }
        body["tool_result"] = None
    elif run.error is not None:
        body["error"] = {
            "code": run.error.code,
            "message": run.error.message,
        }
        body["tool_result"] = None
    else:
        body["error"] = None
        body["tool_result"] = run.tool_result
    return body


def _dispatch_to_response(result: DispatchResult) -> dict[str, Any] | JSONResponse:
    """把 DispatchResult 翻译成 HTTP 响应。

    - trace 持久化失败 -> 500(优先级最高,业务结果虽然拿到了但需要可观测性告警)
    - 业务成功 -> 200 dict
    - 业务失败 -> 按 CODE_TO_STATUS 返 4xx/5xx JSONResponse

    POST /agent/run 和 POST /agent/runs/{id}/replay 共用,保证两条路径响应形态完全一致。
    """
    run = result.run

    if run.trace_persistence == "failed":
        return JSONResponse(
            status_code=500,
            content=_agent_run_to_response_dict(run),
        )

    if run.error is None:
        return _agent_run_to_response_dict(run)

    status_code = CODE_TO_STATUS.get(run.error.code, 500)
    return JSONResponse(
        status_code=status_code,
        content=_agent_run_to_response_dict(run),
    )


# ---------- 路由 ----------


@app.post("/agent/run")
def run_agent(req: RunRequest):
    return _dispatch_to_response(dispatch(req.tool, req.message))


def _not_found_response(run_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "error": {
                "code": "RUN_NOT_FOUND",
                "message": f"run '{run_id}' was not found",
            }
        },
    )


@app.post("/agent/runs/{run_id}/replay")
def replay_run(run_id: str):
    original = get_run(run_id)
    if original is None:
        return _not_found_response(run_id)

    # 优先用 tool_args["message"],fallback input
    # tool_args 是 dict,可能为空;为空时直接用 input
    tool_args = original["tool_args"] or {}
    message = tool_args.get("message", original["input"])
    selected_tool = original["selected_tool"]

    # 重新 dispatch,产生新 run(新 run_id,新时间戳);原 run 一字不动
    return _dispatch_to_response(dispatch(selected_tool, message))


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

    return _not_found_response(run_id)
