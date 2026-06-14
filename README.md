# Agent Tool Endpoint

最小可用的 AI agent 工具调用后端:每次请求产生一条 `AgentRun`
记录(包含 `status`、开始/结束时间戳、工具入参、错误信封、
降级警告 `warnings`、执行时间线 `timeline`),落 SQLite,支持列表、
单条查询、以及**结构化运行诊断**(`GET /agent/runs/{id}/diagnostics`),
失败时直接看到失败类型 + 关键事件时间线 + 下一步建议。

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

**响应形态**:
- 成功 (200):10 字段 `AgentRun`,**不**带 `diagnostics`(成功不需要调试视图)
- 业务失败 (4xx/5xx):10 字段 `AgentRun` + **额外**带 `diagnostics` 字段(失败就该有调试视图,
  客户端拿一次响应就能定位失败阶段、看 suggested_action,不必再 round-trip 一次 GET)
- trace 落库失败 (500):同上,且这是**唯一**能让客户端看到
  `failure_type=persistence_error` 的路径(GET diagnostics 查不到,见下)

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
  "finished_at": "...",
  "diagnostics": {
    "run_id": "a3b1...",
    "status": "failed",
    "failure_type": "validation_error",
    "failure_message": "tool 'weather' is not registered",
    "timeline": [
      {"name": "request_received", "at": "...", "detail": "tool=weather"},
      {"name": "validation_failed", "at": "...", "detail": "TOOL_NOT_FOUND"},
      {"name": "trace_persisted", "at": "...", "detail": null},
      {"name": "response_returned", "at": "...", "detail": null}
    ],
    "diagnostic_summary": "Run failed at validation via weather: tool 'weather' is not registered.",
    "suggested_action": "check tool name and tool input parameters"
  }
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
  "finished_at": "...",
  "diagnostics": {
    "run_id": "b2c1...",
    "status": "failed",
    "failure_type": "validation_error",
    "failure_message": "division by zero",
    "timeline": [
      {"name": "request_received", "at": "...", "detail": "tool=calculator"},
      {"name": "validation_passed", "at": "...", "detail": null},
      {"name": "tool_dispatch_started", "at": "...", "detail": "calculator"},
      {"name": "tool_failed", "at": "...", "detail": "TOOL_INPUT_ERROR"},
      {"name": "trace_persisted", "at": "...", "detail": null},
      {"name": "response_returned", "at": "...", "detail": null}
    ],
    "diagnostic_summary": "Run failed at validation via calculator: division by zero.",
    "suggested_action": "check tool name and tool input parameters"
  }
}
```

validation 阶段失败(422,请求体校验未通过,**不**生成 run):

```json
{"error": {"code": "INVALID_REQUEST", "message": "body.message: Field required"}}
```

trace 持久化失败(500,业务已执行完但 trace 没落库)—— 这种 run **DB 里查不到**,
响应里的 `run_id` 是 in-memory 临时分配的,只用于后台排查。
**响应里额外带 `diagnostics` 字段** —— 这是唯一能让客户端看到
`failure_type=persistence_error` 完整结构的路径(DB 没这一行,
GET `/agent/runs/{id}/diagnostics` 返 404):

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
  "finished_at": "...",
  "diagnostics": {
    "run_id": "c3d1...",
    "status": "trace_persist_failed",
    "failure_type": "persistence_error",
    "failure_message": "RuntimeError: disk full",
    "timeline": [
      {"name": "request_received", "at": "...", "detail": "tool=echo"},
      {"name": "validation_passed", "at": "...", "detail": null},
      {"name": "tool_dispatch_started", "at": "...", "detail": "echo"},
      {"name": "tool_executed", "at": "...", "detail": null},
      {"name": "trace_serialized", "at": "...", "detail": null},
      {"name": "trace_persist_failed", "at": "...", "detail": "RuntimeError"},
      {"name": "response_returned", "at": "...", "detail": null}
    ],
    "diagnostic_summary": "Tool execution completed but trace persistence failed; trace may be lost.",
    "suggested_action": "inspect persistence backend and retry trace insert"
  }
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

### GET /agent/runs/{run_id}/diagnostics

按 id 查一次 run 的**结构化诊断视图**(给运维 / 开发者用)。
跟 `GET /agent/runs/{run_id}`(10 字段 trace)分开:这个端点把 status /
error / warnings 翻译成"这次 run 失败在哪里 / 下一步该看什么"。

```bash
curl http://127.0.0.1:8000/agent/runs/<run_id>/diagnostics
```

未命中(404):

```json
{"error": {"code": "RUN_NOT_FOUND", "message": "run 'xxx' was not found"}}
```

成功(200),7 字段稳定形态:

| 字段 | 含义 |
|---|---|
| `run_id` | 32 字符 hex,跟 `GET /agent/runs/{id}` 一致 |
| `status` | 跟 `AgentRun.status` 一致(`completed` / `failed` / `trace_persist_failed`) |
| `failure_type` | 5 闭集:`validation_error` / `tool_error` / `serialization_error` / `persistence_error` / `unknown`,成功且无降级时为 `null` |
| `failure_message` | 失败原因(已脱敏),成功为 `null` |
| `timeline` | 按时间顺序的关键事件点,每条 `{name, at, detail}` |
| `diagnostic_summary` | 一句话描述这次 run |
| `suggested_action` | failure_type 对应的下一步建议,成功为 `null` |

#### `failure_type` 的 5 个值

| 值 | 触发场景(error.code / warning.code) |
|---|---|
| `validation_error` | `TOOL_INPUT_ERROR`, `TOOL_NOT_FOUND` — 用户责任 |
| `tool_error` | `TOOL_EXECUTION_ERROR` — 工具责任 |
| `serialization_error` | warning 里有 `TOOL_RESULT_SERIALIZATION_FALLBACK`(run 仍 success,只是 tool_result 走了 fallback) |
| `persistence_error` | `TRACE_PERSIST_FAILED` — trace 落库失败 |
| `unknown` | `INTERNAL_ERROR` 或任何没匹配上的 code |

#### timeline 事件名(7 类,按执行顺序)

```
request_received
validation_passed | validation_failed
tool_dispatch_started
tool_executed | tool_failed
trace_serialized | trace_serialization_fallback
trace_persisted | trace_persist_failed
response_returned
```

失败分支也记(`validation_failed` / `tool_failed` / `trace_persist_failed`),
客户端从 timeline 第一条到最后一条能直接定位"卡在哪个阶段"。

#### 成功示例

```json
{
  "run_id": "9f1c...",
  "status": "completed",
  "failure_type": null,
  "failure_message": null,
  "timeline": [
    {"name": "request_received", "at": "2026-06-14T00:00:00.000+00:00", "detail": "tool=calculator"},
    {"name": "validation_passed", "at": "2026-06-14T00:00:00.000+00:00", "detail": null},
    {"name": "tool_dispatch_started", "at": "2026-06-14T00:00:00.000+00:00", "detail": "calculator"},
    {"name": "tool_executed", "at": "2026-06-14T00:00:00.005+00:00", "detail": null},
    {"name": "trace_serialized", "at": "2026-06-14T00:00:00.005+00:00", "detail": null},
    {"name": "trace_persisted", "at": "2026-06-14T00:00:00.005+00:00", "detail": null},
    {"name": "response_returned", "at": "2026-06-14T00:00:00.005+00:00", "detail": null}
  ],
  "diagnostic_summary": "Run completed in 5ms via calculator.",
  "suggested_action": null
}
```

#### 失败示例(工具抛 `ToolExecutionError`)

```json
{
  "run_id": "a3b1...",
  "status": "failed",
  "failure_type": "tool_error",
  "failure_message": "weather API timed out",
  "timeline": [
    {"name": "request_received", "at": "2026-06-14T00:00:00.000+00:00", "detail": "tool=weather"},
    {"name": "validation_passed", "at": "2026-06-14T00:00:00.000+00:00", "detail": null},
    {"name": "tool_dispatch_started", "at": "2026-06-14T00:00:00.000+00:00", "detail": "weather"},
    {"name": "tool_failed", "at": "2026-06-14T00:00:00.500+00:00", "detail": "TOOL_EXECUTION_ERROR"},
    {"name": "trace_persisted", "at": "2026-06-14T00:00:00.500+00:00", "detail": null},
    {"name": "response_returned", "at": "2026-06-14T00:00:00.500+00:00", "detail": null}
  ],
  "diagnostic_summary": "Run failed during tool execution via weather: weather API timed out.",
  "suggested_action": "inspect tool health and recent tool logs"
}
```

#### 降级示例(不可 JSON 序列化的 tool_result)

成功 + `TOOL_RESULT_SERIALIZATION_FALLBACK` warning → `failure_type="serialization_error"`,
`status` 仍 `completed`,timeline 末段把 `trace_serialized` 换成 `trace_serialization_fallback`,
tool_result 是结构化 fallback 形态:

```json
{
  "run_id": "...",
  "status": "completed",
  "failure_type": "serialization_error",
  "failure_message": null,
  "timeline": [
    {"name": "request_received", "at": "...", "detail": "tool=custom"},
    {"name": "validation_passed", "at": "...", "detail": null},
    {"name": "tool_dispatch_started", "at": "...", "detail": "custom"},
    {"name": "tool_executed", "at": "...", "detail": null},
    {"name": "trace_serialization_fallback", "at": "...", "detail": "set"},
    {"name": "trace_persisted", "at": "...", "detail": null},
    {"name": "response_returned", "at": "...", "detail": null}
  ],
  "diagnostic_summary": "Run completed via custom with serialization fallback; tool result is a safe repr.",
  "suggested_action": "verify tool returns JSON-serializable values"
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

# 3. 查结构化诊断视图 — failure_type + timeline + suggested_action
curl http://127.0.0.1:8000/agent/runs/<run_id>/diagnostics

# 4. 看最近 20 条调用
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
