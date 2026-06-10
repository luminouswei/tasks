# Agent Tool Endpoint

Minimal FastAPI backend for running registered tools and storing traces in SQLite.

## Setup

```bash
pip install -e ".[dev]"
```

## Run

```bash
uvicorn app.main:app --reload
```

Server listens on `http://127.0.0.1:8000`. Interactive docs at `/docs`.

## Test

```bash
pytest -q
```

## API

### POST /agent/run

Run a registered tool with a message.

```bash
curl -X POST http://127.0.0.1:8000/agent/run \
  -H "Content-Type: application/json" \
  -d '{"message": "12*8", "tool": "calculator"}'
```

Success (200):
```json
{"trace_id": "...", "tool": "calculator", "result": 96.0, "latency_ms": 3}
```

Dispatch-stage error (4xx/5xx from tool execution / routing):
```json
{"trace_id": "...", "error": {"code": "TOOL_NOT_FOUND", "message": "tool 'weather' is not registered"}}
```

Validation-stage error (422 from request body validation, no trace generated):
```json
{"error": {"code": "INVALID_REQUEST", "message": "body.message: Field required"}}
```

| 阶段 | HTTP | error.code | trace_id |
|---|---|---|---|
| 参数缺失/不合规(validation) | 422 | `INVALID_REQUEST` | 无 |
| tool 未注册(dispatch) | 404 | `TOOL_NOT_FOUND` | 有 |
| 工具执行抛业务异常(dispatch) | 400 | `TOOL_EXECUTION_ERROR` | 有 |
| 未预期异常(dispatch) | 500 | `INTERNAL_ERROR` | 有 |

> validation 阶段错误不触发工具调用、不写 trace,响应也不带 `trace_id`。只有进入 dispatch 阶段的请求才会生成 trace。

### GET /agent/traces/{trace_id}

Fetch a recorded trace by id.

```bash
curl http://127.0.0.1:8000/agent/traces/<trace_id>
```

Success (200) includes `input`, `result` (on success) or `error` (on failure), `latency_ms`, `created_at`. Not found returns 404 `TRACE_NOT_FOUND`.

## Built-in Tools

- `echo` — returns the input message
- `calculator` — evaluates `+ - * /` expressions with parentheses and unary minus (recursive-descent parser, no `eval`)

## Adding a New Tool

1. Create `app/tools/<name>.py`:

```python
from app.registry import register_tool

@register_tool(name="weather", description="Lookup weather")
def run(message: str) -> str:
    return f"sunny in {message}"
```

2. Add one import line to `app/tools/__init__.py`:

```python
from . import calculator, echo, weather
```

No changes to `main.py` / `dispatcher.py` / `trace_store.py` are needed.

## Configuration

- `TRACE_DB_PATH` (env): SQLite file location, default `./traces.db`
