# Agent Tool Endpoint

最小可用的 AI agent 工具调用后端:每次请求产生一条 `AgentRun`
记录(包含 `status`、开始/结束时间戳、工具入参、错误信封、
降级警告 `warnings`),落 SQLite,支持列表与单条查询。

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

所有响应**统一** 10 字段 `AgentRun` 形态:

| 字段 | 含义 | 取值 |
|---|---|---|
| `run_id` | 32 字符 hex | — |
| `input` | 用户原始输入 | 任意字符串 |
| `selected_tool` | 工具名 | 字符串 |
| `tool_args` | 实际传给工具的参数 | dict |
| `status` | **工程生命周期状态**(见下表) | `completed` / `failed` / `trace_persist_failed` |
| `tool_result` | 工具返回的业务结果 | 任意 JSON 序列化的值,失败时为 `null` |
| `error` | 失败原因 | `{code, message}` 或 `null` |
| `warnings` | 非致命降级告警 | `[{code, message}, ...]`,空时 `[]` |
| `started_at` | ISO 8601 | 字符串 |
| `finished_at` | ISO 8601 | 字符串 |

### 3 个正交字段的语义

| 字段 | 回答的问题 | 出现时机 |
|---|---|---|
| `status` | 这次 run 在工程链路里走到哪一步? | 总是(除了 validation 失败) |
| `error` | run 为什么失败? | run 失败时(配合 `status="failed"`) |
| `warnings` | run 成功了,但有 caveat 吗? | 序列化降级等场景(配合 `status="completed"`) |

`error` 和 `warnings` **不要混**:
- 告警/分桶时 `WHERE error.code IS NOT NULL` 只挑真失败的 run
- `warnings` 单独处理(`WHERE json_array_length(warnings) > 0`)

### `status` 的 3 个值

| 值 | 含义 | 落 DB? |
|---|---|---|
| `completed` | 业务执行成功 + trace 落库成功 | ✅ |
| `failed` | 业务执行失败 + trace 落库成功(细节看 `error.code`) | ✅ |
| `trace_persist_failed` | 业务无论成败,trace 落库失败 | ❌ **只在响应/日志** |

### 7 个场景的完整映射

| 场景 | `status` | `error.code` | `warnings` | HTTP | DB? |
|---|---|---|---|---|---|
| 工具跑通,trace 存了 | `completed` | `null` | `[]` | 200 | ✅ |
| 工具不存在 | `failed` | `TOOL_NOT_FOUND` | `[]` | 404 | ✅ |
| 工具参数/输入错误(用户责任) | `failed` | `TOOL_INPUT_ERROR` | `[]` | 400 | ✅ |
| 工具内部执行异常(工具责任) | `failed` | `TOOL_EXECUTION_ERROR` | `[]` | 500 | ✅ |
| 系统内部非预期异常 | `failed` | `INTERNAL_ERROR` | `[]` | 500 | 尽量 |
| 请求本身不合法(validation) | **无 status**(不创建 run) | `INVALID_REQUEST` | — | 422 | ❌ |
| 业务跑完,但 trace 落库失败 | `trace_persist_failed` | `TRACE_PERSIST_FAILED` | `[]` | 500 | ❌ |
| 工具跑通但 result 不可 JSON 序列化 | `completed` | `null` | `[{code: "TOOL_RESULT_SERIALIZATION_FALLBACK", ...}]` | 200 | ✅(降级) |

### 责任边界决定告警通道

- `TOOL_INPUT_ERROR` (HTTP 400) — 用户错。客户端改请求重发即可,**不需要** PagerDuty。
- `TOOL_EXECUTION_ERROR` (HTTP 500) — 工具崩。**需要**告警和修复。
- `INTERNAL_ERROR` (HTTP 500) — 系统崩。**需要**告警和修复。
- `TRACE_PERSIST_FAILED` (HTTP 500) — 业务结果拿到了但 trace 没记。**需要**告警 + 重试落库,但**不**应该当成"业务失败"重发请求。

### 序列化降级

当 `tool_result` 或 `tool_args` 包含无法被 `json.dumps` 直接序列化的值
(`set` / `bytes` / `Decimal` / 自定义对象),trace **不会**丢,只是把那个字段
整体替换为:

```json
{
  "_serialization": "fallback",
  "type": "<原值类型名>",
  "repr": "<repr(...) 截断到 500 字符>"
}
```

同时往 `warnings` 里加一条 `TOOL_RESULT_SERIALIZATION_FALLBACK`,客户端可以靠
这个 marker 主动发现降级(不必再去 `tool_result._serialization` 字段里挖)。

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
  "status": "completed",
  "tool_result": 96,
  "error": null,
  "warnings": [],
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
  "status": "failed",
  "tool_result": null,
  "error": {"code": "TOOL_NOT_FOUND", "message": "tool 'weather' is not registered"},
  "warnings": [],
  "started_at": "...",
  "finished_at": "..."
}
```

工具参数错(用户责任,400)—— 例:`calculator` `1/0`:

```json
{
  "run_id": "b2c1...",
  "input": "1/0",
  "selected_tool": "calculator",
  "tool_args": {"message": "1/0"},
  "status": "failed",
  "tool_result": null,
  "error": {"code": "TOOL_INPUT_ERROR", "message": "division by zero"},
  "warnings": [],
  "started_at": "...",
  "finished_at": "..."
}
```

validation 阶段失败(422,请求体校验未通过,**不**生成 run):

```json
{"error": {"code": "INVALID_REQUEST", "message": "body.message: Field required"}}
```

trace 持久化失败(500,业务已执行完但 trace 没落库)—— 这种 run **DB 里查不到**,
响应里的 `run_id` 是 in-memory 临时分配的,只用于后台排查:

```json
{
  "run_id": "c3d1...",
  "input": "hi",
  "selected_tool": "echo",
  "tool_args": {"message": "hi"},
  "status": "trace_persist_failed",
  "tool_result": "hi",
  "error": {"code": "TRACE_PERSIST_FAILED", "message": "RuntimeError: disk full"},
  "warnings": [],
  "started_at": "...",
  "finished_at": "..."
}
```

> **关键契约**:`TRACE_PERSIST_FAILED` 时 `tool_result` **保留业务结果**(业务执行过了,工具被调一次,结果不能丢)。客户端靠 `error.code` 区分"业务失败"和"业务成功但 trace 没记",**绝对不要**把这种响应当成业务失败重发请求(否则工具会被调两次)。

工具跑通但 result 不可 JSON 序列化(200,带 warning 降级):

```json
{
  "run_id": "d4e1...",
  "input": "x",
  "selected_tool": "custom",
  "tool_args": {"message": "x"},
  "status": "completed",
  "tool_result": {"_serialization": "fallback", "type": "set", "repr": "{1, 2, 3}"},
  "error": null,
  "warnings": [
    {"code": "TOOL_RESULT_SERIALIZATION_FALLBACK", "message": "tool result was not JSON-serializable; stored a safe repr"}
  ],
  "started_at": "...",
  "finished_at": "..."
}
```

### POST /agent/runs/{run_id}/replay

用历史 run 的入参(`tool_args["message"]`,fallback `input` + `selected_tool`)
触发一次**新的**工具调用,产生**新** `run_id`,**原 run 一字不动**。

```bash
curl -X POST http://127.0.0.1:8000/agent/runs/<run_id>/replay
```

返回形态跟 `POST /agent/run` 完全一致(成功 200,失败 4xx/5xx + error 信封),
失败状态码映射也相同(`TOOL_NOT_FOUND` → 404 / `TOOL_INPUT_ERROR` → 400 /
`TOOL_EXECUTION_ERROR` → 500 / `INTERNAL_ERROR` → 500)。

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

命中(200)返回完整 10 字段 `AgentRun`。未命中(404):

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
`tool_result`(成功)或 `error`(失败) / `warnings`(降级告警) /
`started_at` / `finished_at` —— 失败调用也能复盘。

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

## Schema 演进 & 迁移

`init_db` 自动用 `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ... ADD COLUMN` 兼容老 DB。
但老 DB 里的旧 `status` 值(`success` / `tool_not_found` / `tool_error` / `internal_error`)
**新代码读出来不会自动映射**(代码不认这些字面量了),会原样落进响应,
客户端就会看到混合的新旧值。

如果之前用过这个项目,推荐直接清库重新开始:

```bash
rm -f agent_runs.db
uvicorn app.main:app --reload  # 启动时自动建新 schema
```

生产环境要做正式 migration 的话,至少需要:
1. 把 `status` 列的旧值映射到新值:`success → completed`,其它 → `failed`
2. 业务细节(原 `status='tool_error'` 的)需要从 `error.code` 重新判别(现在 `error_code` 已经在表里,可以直接读)

## 设计文档

`status` / `error` / `warnings` 的分层设计来自项目内部的 `.claude/memory/trace-status-semantics.md`,
跟代码一起演进,记录了"为什么不把 `_unserializable` 塞 `error.code`"这类决策的依据。
