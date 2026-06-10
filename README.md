# Agent Tool Endpoint

最小可用的 AI agent 工具调用后端:每次请求产生一条 `AgentRun`
记录(包含 `status`、开始/结束时间戳、工具入参、错误信封),
落 SQLite,支持列表与单条查询。

## 安装

```bash
pip install -e ".[dev]"
```

## 启动

```bash
uvicorn app.main:app --reload
```

服务监听 `http://127.0.0.1:8000`,交互式文档在 `/docs`。

## 测试

```bash
pytest -q
```

## API

所有响应**统一** 9 字段 `AgentRun` 形态。成功调用 `tool_result`
有值、`error` 为 `null`;失败调用 `tool_result` 为 `null`、`error`
是 `{code, message}` 对象。

### POST /agent/run

用消息 + 工具名调用一个已注册的工具。

```bash
curl -X POST http://127.0.0.1:8000/agent/run \
  -H "Content-Type: application/json" \
  -d '{"message": "12*8", "tool": "calculator"}'
```

成功(200):

```json
{
  "run_id": "9f1c...",
  "input": "12*8",
  "selected_tool": "calculator",
  "tool_args": {"message": "12*8"},
  "status": "success",
  "tool_result": 96,
  "error": null,
  "started_at": "2026-06-10T08:00:00.000Z",
  "finished_at": "2026-06-10T08:00:00.005Z"
}
```

dispatch 阶段失败(4xx/5xx,工具执行 / 路由错误)—— 例:工具未注册(404):

```json
{
  "run_id": "a3b1...",
  "input": "hi",
  "selected_tool": "weather",
  "tool_args": {"message": "hi"},
  "status": "tool_not_found",
  "tool_result": null,
  "error": {"code": "TOOL_NOT_FOUND", "message": "tool 'weather' is not registered"},
  "started_at": "...",
  "finished_at": "..."
}
```

validation 阶段失败(422,请求体校验未通过,**不**生成 run):

```json
{"error": {"code": "INVALID_REQUEST", "message": "body.message: Field required"}}
```

| 阶段 | HTTP | error.code | run_id | status |
|---|---|---|---|---|
| 参数缺失/不合规(validation) | 422 | `INVALID_REQUEST` | 无 | — |
| tool 未注册(dispatch) | 404 | `TOOL_NOT_FOUND` | 有 | `tool_not_found` |
| 工具执行抛业务异常(dispatch) | 400 | `TOOL_EXECUTION_ERROR` | 有 | `tool_error` |
| 未预期异常(dispatch) | 500 | `INTERNAL_ERROR` | 有 | `internal_error` |

> validation 阶段错误不触发工具调用、不写 run,响应也不带 `run_id`。其它三个 dispatch 阶段**全部**会落库成 run 记录,失败也能用 `run_id` 查到完整 trace。

### POST /agent/runs/{run_id}/replay

用历史 run 的入参(`tool_args["message"]`,fallback `input` + `selected_tool`)
触发一次**新的**工具调用,产生**新** `run_id`,**原 run 一字不动**。

```bash
curl -X POST http://127.0.0.1:8000/agent/runs/<run_id>/replay
```

返回形态跟 `POST /agent/run` 完全一致(成功 200,失败 4xx/5xx + error 信封),
失败状态码映射也相同(`tool_not_found` → 404 / `tool_error` → 400 /
`internal_error` → 500)。

历史 run 不存在 → 404 `RUN_NOT_FOUND`。

常用场景:
- 复现一个生产失败的调用
- 修了工具 bug 之后批量重跑所有 `tool_error` 的 run,看哪些变 success
- 确认一个 `tool_not_found` 不是瞬时错误(重跑还是 tool_not_found = 真的没注册)

> Replay 是"读历史 + 重新调度",**不修改**历史 run。这是"日志"和"动作"的边界:历史 run 是日志,replay 是动作。

### GET /agent/runs/{run_id}

按 id 查一条 run。

```bash
curl http://127.0.0.1:8000/agent/runs/<run_id>
```

命中(200)返回完整 9 字段 `AgentRun`。未命中(404):

```json
{"error": {"code": "RUN_NOT_FOUND", "message": "run 'xxx' was not found"}}
```

### GET /agent/runs

列出所有 run,按 `started_at DESC` 排序,`run_id` 作为同毫秒 tiebreaker。

```bash
# 默认:limit=50, offset=0
curl http://127.0.0.1:8000/agent/runs

# 翻页
curl 'http://127.0.0.1:8000/agent/runs?limit=20&offset=40'
```

Query 参数:
- `limit` — 1..200,默认 50
- `offset` — >=0,默认 0

越界值返回 422 `INVALID_REQUEST`(FastAPI `Query` 约束自动挡)。

成功(200):

```json
{
  "runs": [<AgentRun>, <AgentRun>, ...],
  "limit": 50,
  "offset": 0
}
```

### Debug recipe:一次失败调用的复盘

```bash
# 1. 触发一次调用(可能成功也可能失败),拿到 run_id
curl -X POST http://127.0.0.1:8000/agent/run \
  -H "Content-Type: application/json" \
  -d '{"message": "1/0", "tool": "calculator"}'

# 2. 用 run_id 查完整 trace
curl http://127.0.0.1:8000/agent/runs/<run_id>

# 3. 看最近 20 条调用
curl 'http://127.0.0.1:8000/agent/runs?limit=20'
```

每条 run 都记录了:`input` / `selected_tool` / `tool_args` /
`tool_result`(成功)或 `error`(失败) / `started_at` /
`finished_at` —— 失败调用也能复盘。

## 内置工具

- `echo` — 原样返回输入的 message
- `calculator` — 手写递归下降解析器,支持 `+ - * /`、括号、一元负号(无 `eval`)

## 新增一个工具

1. 创建 `app/tools/<name>.py`:

```python
from app.registry import register_tool

@register_tool(name="weather", description="Lookup weather")
def run(message: str) -> str:
    return f"sunny in {message}"
```

2. 在 `app/tools/__init__.py` 加一行 import:

```python
from . import calculator, echo, weather
```

不需要改 `main.py` / `dispatcher.py` / `agent_run_store.py`。

## 配置

- `AGENT_RUN_DB_PATH`(env):SQLite 文件路径,默认 `./agent_runs.db`
