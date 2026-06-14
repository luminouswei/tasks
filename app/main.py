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
from app.diagnostics import build_diagnostics
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
    """把 store 返回的 dict(已经是 AgentRun 形态)统一组装成 HTTP 响应需要的 10 字段。

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
        "warnings": run.get("warnings", []),
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
    }


def _agent_run_to_response_dict(run: AgentRun) -> dict[str, Any]:
    """AgentRun -> API 响应 dict(10 字段一致形态)。

    字段契约:
    - run_id / input / selected_tool / tool_args / status /
      started_at / finished_at: 总是返回
    - tool_result: 业务执行过了就填值(成功 / 失败都填,看具体 dispatcher 怎么处理)
      - 业务成功 → 工具返回值
      - 业务失败 → dispatcher 已置 None(工具没拿到结果)
      - trace 落库失败 → 业务结果保留(trace 失败不影响业务结果的可得性)
    - error: 业务失败 / trace 失败时填 {code, message},业务成功时 null
    - warnings: 非空时填 list[{code, message}],空时 [] (跟 "无 warning" 区分开)

    POST 和 replay 走这条路径,GET 走 _run_response_body(也走它,保持 10 字段一致)。
    """
    body: dict[str, Any] = {
        "run_id": run.run_id,
        "input": run.input,
        "selected_tool": run.selected_tool,
        "tool_args": run.tool_args,
        "status": run.status,
        "tool_result": run.tool_result,
        "error": None,
        "warnings": [{"code": w.code, "message": w.message} for w in run.warnings],
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }
    if run.error is not None:
        body["error"] = {
            "code": run.error.code,
            "message": run.error.message,
        }
        # 业务失败时 tool_result 在 dispatcher 里就设了 None(工具没拿到结果),
        # 这里不再覆盖。
        # **trace 落库失败时不覆盖 tool_result**——业务执行过了,业务结果
        # 应当保留给调用方(他们已经付了工具调用的代价),只是观测落库失败。
        # 客户端需要靠 error.code == "TRACE_PERSIST_FAILED" 区分"业务失败"
        # 和"业务成功但 trace 没记",前者不该重试(已执行),后者**绝对不该**
        # 当成业务失败重发请求(否则工具会被调两次)。
    return body


def _dispatch_to_response(result: DispatchResult) -> dict[str, Any] | JSONResponse:
    """把 DispatchResult 翻译成 HTTP 响应。

    - status == "trace_persist_failed" -> 500(trace 落库失败,但业务结果已拿到)
    - 业务成功(status == "completed") -> 200
    - 业务失败(status == "failed" + error 有值) -> 按 CODE_TO_STATUS 返 4xx/5xx

    POST /agent/run 和 POST /agent/runs/{id}/replay 共用,保证两条路径响应形态完全一致。
    """
    run = result.run

    if run.status == "trace_persist_failed":
        return JSONResponse(
            status_code=CODE_TO_STATUS.get("TRACE_PERSIST_FAILED", 500),
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


# ---------- 诊断视图 ----------

# 诊断视图的 7 字段响应契约。所有 GET /agent/runs/{id}/diagnostics 命中都返这 7 个 key;
# 字段值不随成功 / 失败 / 降级变化,客户端解析逻辑可以稳定。
DIAGNOSTICS_RESPONSE_KEYS = (
    "run_id", "status", "failure_type", "failure_message",
    "timeline", "diagnostic_summary", "suggested_action",
)


def _diagnostics_to_response(diagnostics) -> dict[str, Any]:
    """Diagnostics dataclass -> API 响应 dict(7 字段稳定形态)。"""
    return {
        "run_id": diagnostics.run_id,
        "status": diagnostics.status,
        "failure_type": diagnostics.failure_type,
        "failure_message": diagnostics.failure_message,
        "timeline": diagnostics.timeline,
        "diagnostic_summary": diagnostics.diagnostic_summary,
        "suggested_action": diagnostics.suggested_action,
    }


@app.get("/agent/runs/{run_id}/diagnostics")
def get_agent_run_diagnostics(run_id: str):
    """返回这次 run 的结构化诊断视图,给运维 / 开发者用。

    - 200: 7 字段稳定形态(failure_type, timeline, summary, suggested_action 等)
    - 404: RUN_NOT_FOUND(找不到的 run_id)

    diagnostics 是**只读派生视图**:从 AgentRun(status / error / warnings / timeline)
    翻译而来,业务执行链路不受影响。改判定规则只动 app/diagnostics.py。
    """
    run = get_run(run_id)

    if run is None:
        return _not_found_response(run_id)

    # store 返回的是 dict(已经是 AgentRun 字段形态);直接喂给 build_diagnostics,
    # 避免再走 dataclass 构造一次。AgentRun.error 在 dict 里是 dict,在
    # build_diagnostics 里我们访问 run.error 是为了从 dataclass 取,但因为
    # 字段命名一致(diagnostics 实际上只用 error.code / error.message /
    # warnings / timeline / status),直接构造一个等价的 AgentRun dataclass
    # 给 build_diagnostics 用最稳。
    from app.agent_run import AgentErrorPayload

    error = None
    if run.get("error"):
        error = AgentErrorPayload(
            code=run["error"]["code"],
            message=run["error"]["message"],
        )

    from app.agent_run import AgentWarning
    warnings = [
        AgentWarning(code=w["code"], message=w["message"])
        for w in run.get("warnings", [])
    ]

    # timeline 在 store 里已经是 dict 列表(name/at/detail),
    # build_diagnostics 内部会再 copy 一次,语义无副作用。
    from app.agent_run import TimelineEvent
    timeline = [
        TimelineEvent(name=ev["name"], at=ev["at"], detail=ev.get("detail"))
        for ev in run.get("timeline", [])
    ]

    run_obj = AgentRun(
        run_id=run["run_id"],
        input=run["input"],
        selected_tool=run["selected_tool"],
        tool_args=run["tool_args"],
        tool_result=run["tool_result"],
        status=run["status"],
        error=error,
        started_at=run["started_at"],
        finished_at=run["finished_at"],
        warnings=warnings,
        timeline=timeline,
    )

    diagnostics = build_diagnostics(run_obj)
    return _diagnostics_to_response(diagnostics)
