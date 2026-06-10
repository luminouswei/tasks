# 最小 AI Agent 工具调用后端接口 — 设计文档

- 日期: 2026-06-09
- 状态: 已通过 brainstorming,待用户审阅
- 目标分支: `task/agent-tool-endpoint`

## 1. 目标与范围

实现一个后端 HTTP 接口,接收用户一句话 + 工具名,在服务端调用对应的本地工具,返回工具结果与调用 trace。覆盖正常路径、错误路径、可观测性(trace 落库 + 查询)、可扩展的注册机制。

明确不在范围内:多轮对话、模型推理、外部网络工具、鉴权、并发性能优化。

## 2. 技术选型

| 维度 | 选择 | 理由 |
|---|---|---|
| 语言 / 框架 | Python 3.11 + FastAPI | 异步生态、类型注解、Pydantic 校验、后续接 AI 工具链顺 |
| Trace 存储 | SQLite (单文件) | 跨重启可查,标准库零依赖,够用 |
| Calculator 实现 | 手写递归下降解析器 | 任务要求"练手",且更可控 |
| 工具注册 | 装饰器自注册 | 加新工具无需改 dispatcher |
| 错误响应 | 分类型 HTTP 状态码 + `{error: {code, message}}` envelope | 主流,易消费 |
| 测试 | pytest + FastAPI TestClient | 同步够用,降低心智负担 |

## 3. 架构

```
HTTP 请求
   ↓
[ API 层 ]  app/api/routes.py (挂在 main.app 上)
   │   参数校验 (Pydantic) + 异常 → HTTP 响应
   ↓
[ 分发层 ]  app/dispatcher.py
   │   查注册表,调工具,捕获异常,产生 trace
   ↓
[ 工具层 ]  app/tools/*.py   (装饰器自注册到 registry)
   │   纯函数: 接收 message 字符串,返回 JSON-serializable 结果
   ↓
[ 存储层 ]  app/trace_store.py   (SQLite,同步访问)
   ↑   写 trace / 查 trace
```

核心原则:
- 工具只接收 `message: str`,返回 `Any`(必须 JSON-serializable)。不接触 HTTP / trace / 异常类。
- dispatcher 是唯一接触 registry + trace_store + 异常 → HTTP code 映射的地方。
- 工具执行失败不抛 5xx。dispatcher 捕获异常后,把错误写进 `DispatchResult.error`,HTTP 层负责按 error.code 映射状态码。trace 同步记录。
- trace 写库失败不阻断主请求,退化到日志。

## 4. 接口契约

### 4.1 POST `/agent/run`

请求体:
```json
{
  "message": "帮我计算 12 * 8",
  "tool": "calculator"
}
```

校验规则(Pydantic):
- `message`: 非空字符串,trim 后长度 ≤ 2000
- `tool`: 非空字符串,trim 后长度 ≤ 64,匹配 `^[a-z][a-z0-9_]*$`

成功响应(200):
```json
{
  "trace_id": "9f1c…",
  "tool": "calculator",
  "result": 96,
  "latency_ms": 3
}
```

`result` 是工具返回的原始 JSON 值(calculator 返回数字,echo 返回字符串)。

错误响应统一 envelope,HTTP 状态码表达错误类型:
```json
{ "error": { "code": "TOOL_NOT_FOUND", "message": "tool 'weather' is not registered" } }
```

| 场景 | HTTP | error.code |
|---|---|---|
| 参数缺失 / 类型错 / 不合规 | 422 | `INVALID_REQUEST` |
| 工具名未注册 | 404 | `TOOL_NOT_FOUND` |
| 工具执行抛业务异常(除零、表达式不合法) | 400 | `TOOL_EXECUTION_ERROR` |
| 未预期异常 | 500 | `INTERNAL_ERROR` |

### 4.2 GET `/agent/traces/{trace_id}` (stretch)

- 200 + 完整 trace(`tool`、`input`、`output` 或 `error` 二选一、`latency_ms`、`created_at`)
- 404 `TRACE_NOT_FOUND`

trace 返回结构:
```json
{
  "trace_id": "9f1c…",
  "tool": "calculator",
  "input": "12 * 8",
  "result": 96,
  "latency_ms": 3,
  "created_at": "2026-06-09T08:12:34.567Z"
}
```

失败时:
```json
{
  "trace_id": "…",
  "tool": "calculator",
  "input": "1/0",
  "error": { "code": "TOOL_EXECUTION_ERROR", "message": "division by zero" },
  "latency_ms": 1,
  "created_at": "2026-06-09T08:12:34.567Z"
}
```

## 5. 工具注册与分发

### 5.1 ToolSpec

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str                # e.g. "calculator"
    description: str
    run: Callable[[str], Any]
```

### 5.2 注册 — 装饰器

```python
# app/registry.py
_REGISTRY: dict[str, ToolSpec] = {}

def register_tool(name: str, *, description: str):
    def deco(fn):
        _REGISTRY[name] = ToolSpec(name, description, fn)
        return fn
    return deco

def get_tool(name: str) -> ToolSpec:
    spec = _REGISTRY.get(name)
    if spec is None:
        raise ToolNotFoundError(f"tool '{name}' is not registered")
    return spec

def registered_names() -> list[str]:
    return sorted(_REGISTRY.keys())
```

### 5.3 自注册触发

`app/tools/__init__.py` 显式 import 所有工具子模块(白名单导入,不让它靠隐式发现):

```python
from . import calculator, echo
```

加新工具就在这一行加。

### 5.4 dispatcher 流程

`app/dispatcher.py` 入口: `dispatch(tool_name: str, message: str) -> DispatchResult`

```python
@dataclass
class DispatchResult:
    trace_id: str
    tool: str
    result: Any            # 成功时填,失败时为 None
    error: ErrorPayload | None  # 失败时填,成功时为 None
    latency_ms: int

@dataclass
class ErrorPayload:
    code: str              # 与 HTTP 层 status 映射的 key
    message: str
```

流程:
1. `trace_id = uuid4().hex`
2. `start = time.perf_counter()`
3. 查 `_REGISTRY`,没有 → 返回 `DispatchResult(trace_id, tool, None, ErrorPayload("TOOL_NOT_FOUND", …), 0)`,写 trace,API 层映射到 404
4. 调 `spec.run(message)`,捕获:
   - `ToolExecutionError` → `result=None, error=ErrorPayload("TOOL_EXECUTION_ERROR", str(e))`,写 trace,API 层映射到 400
   - 其他 `Exception` → `result=None, error=ErrorPayload("INTERNAL_ERROR", "internal server error")`(原始堆栈写日志,不进 error.message),写 trace,API 层映射到 500
5. `latency_ms = int((perf_counter() - start) * 1000)`
6. 写 trace,返回 `DispatchResult`

API 层(`app/main.py`)的响应构造:若 `error is None` 则 200 + `result`;否则按 `error.code → http_status` 表返回 4xx/5xx + error envelope。该映射表与 `errors.AppError.http_status` 字段对齐,集中在一处定义。

## 6. 工具实现

### 6.1 echo

```python
# app/tools/echo.py
@register_tool(name="echo", description="回显输入")
def run(message: str) -> str:
    return message
```

### 6.2 calculator

```python
# app/tools/calculator.py
@register_tool(name="calculator", description="四则运算")
def run(message: str) -> float:
    return parse_and_eval(message)  # 走 app.parser
```

支持的语法:
- 数字(整数 / 小数)
- `+ - * /` 四则运算
- 一元负号(如 `-3 + 2`)
- 括号
- 空格任意

不支持:函数调用、变量、幂运算。所有非法输入(空串、含字母、连续运算符、括号不匹配、除零)抛 `ToolExecutionError`。

## 7. 解析器(app/parser.py)

手写递归下降,先 tokenize 再 parse 然后 evaluate。

**Token 类型:** `NUMBER`, `+`, `-`, `*`, `/`, `(`, `)`, `EOF`

**文法(优先级从低到高):**
```
expression  := term (('+' | '-') term)*
term        := factor (('*' | '/') factor)*
factor      := ('-' factor) | NUMBER | '(' expression ')'
```

求值时同步算,不需要单独 AST。结果用 float(整数仍以 `12.0` 返回,在响应里序列化为 `12.0`,测试用 `pytest.approx` 或 `== 96` 容忍)。

除零检测:在做 `/` 时检查右操作数为 0,抛 `ToolExecutionError("division by zero")`。

## 8. Trace 模型与存储

### 8.1 表结构

```sql
CREATE TABLE IF NOT EXISTS traces (
  trace_id     TEXT PRIMARY KEY,
  tool         TEXT NOT NULL,
  input        TEXT NOT NULL,
  output       TEXT,                  -- 成功时存 JSON 序列化结果
  error_code   TEXT,                  -- 失败时存 error.code
  error_msg    TEXT,                  -- 失败时存 error.message
  latency_ms   INTEGER NOT NULL,
  created_at   TEXT NOT NULL          -- ISO8601 UTC
);
CREATE INDEX IF NOT EXISTS idx_traces_tool_created ON traces(tool, created_at);
```

### 8.2 存储 API

`app/trace_store.py`:
- `init_db(path)` — 启动时建表
- `insert_trace(record)` — 同步写一条
- `get_trace(trace_id)` — 返回 dict 或 None
- 连接用 `sqlite3.connect(path, check_same_thread=False)`,每次调用用 `with` 拿游标
- JSON 序列化时 `output` 用 `json.dumps(ensure_ascii=False)`,读回时 `json.loads`
- 路径通过环境变量 `TRACE_DB_PATH` 配置,默认 `./traces.db`

trace 写入异常只记 `logger.exception("trace insert failed")`,不向用户暴露,不阻断主响应。

## 9. 错误处理

### 9.1 异常与 code→http_status 映射

```python
# app/errors.py
# 工具代码抛 ToolExecutionError;其他由 dispatcher 兜底。
# AppError 本身更多是"为 code→http_status 提供单一来源"。
class AppError(Exception):
    code: str = "INTERNAL_ERROR"
    http_status: int = 500
    def __init__(self, message: str): self.message = message; super().__init__(message)

class ToolExecutionError(AppError):
    """工具主动抛,表示用户输入导致了可预期的业务错误。"""
    code = "TOOL_EXECUTION_ERROR"; http_status = 400

# code → http_status 映射(集中):
CODE_TO_STATUS = {
    "TOOL_NOT_FOUND":         404,
    "TOOL_EXECUTION_ERROR":   400,
    "TRACE_NOT_FOUND":        404,
    "INVALID_REQUEST":        422,
    "INTERNAL_ERROR":         500,
}
```

工具代码只 import `ToolExecutionError`。`ToolNotFoundError` / `TraceNotFoundError` 不作为异常抛出,只作为 `ErrorPayload.code` 出现在 dispatch / API 路径上。

### 9.2 全局 handler(挂在 FastAPI app 上)

- `RequestValidationError`(Pydantic/FastAPI)→ 422 + `INVALID_REQUEST`,message 列出缺失/不合规字段名(不暴露内部 stack)
- `Exception` 兜底 → 500 + `INTERNAL_ERROR`,message 固定 "internal server error",**绝不带 traceback 字段**

主路径(`/agent/run`)走 dispatcher 返回的 `DispatchResult` 自行构造响应,不走 exception handler;handler 仅兜底 Pydantic 校验和未捕获异常。

### 9.3 日志

每次请求 INFO 一行:`trace_id`、`tool`、`status`、`latency_ms`。5xx 时 ERROR + traceback。

### 9.4 不做的事

- 不向用户返回堆栈
- 不把内部异常类名当 error.code
- 不在错误响应里放 hint / suggestion

## 10. 目录结构

```
.
├── LICENSE
├── README.md                    # 怎么跑、怎么测、接口示例
├── pyproject.toml               # 项目元数据 + 依赖(fastapi、uvicorn、pytest)
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app + exception handlers
│   ├── models.py                # Pydantic schemas (RunRequest)
│   ├── errors.py                # AppError 子类
│   ├── registry.py              # @register_tool + _REGISTRY
│   ├── dispatcher.py            # dispatch() + trace 落库
│   ├── trace_store.py           # SQLite 读写
│   ├── parser.py                # 手写四则运算解析器
│   └── tools/
│       ├── __init__.py          # 显式 import calculator, echo
│       ├── calculator.py
│       └── echo.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_calculator_parser.py
│   ├── test_dispatcher.py
│   ├── test_api_run.py
│   └── test_api_traces.py
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-06-09-agent-tool-endpoint-design.md
```

## 11. 实施顺序

1. `errors.py` + `models.py`(地基)
2. `parser.py` + `test_calculator_parser.py`(单测先行)
3. `registry.py` + `tools/echo.py` + `tools/calculator.py`
4. `trace_store.py` + 初始化
5. `dispatcher.py` + `test_dispatcher.py`
6. `main.py` + `test_api_run.py`
7. `test_api_traces.py` + README
8. 一遍 `pytest -q` + 手动 `curl` 验证

## 12. 测试方案

**框架:** `pytest` + FastAPI `TestClient`(同步)。

**`conftest.py` 关键 fixture:**
- `temp_db(monkeypatch, tmp_path)` → 把 `TRACE_DB_PATH` 改成临时文件
- `client(temp_db)` → TestClient(app)
- `stub_tool_registry` → 在测试里临时塞个会抛异常的假工具,跑完清理

**必跑用例(spec 明确要求 5 个 + 加强 2 个):**

| # | 文件 | 场景 | 验证点 |
|---|---|---|---|
| 1 | test_calculator_parser | `12 * 8` | 返回 96 |
| 2 | test_calculator_parser | `-3 + 4 * (2 - 1)` | 返回 1 |
| 3 | test_calculator_parser | `1/0` | 抛 ToolExecutionError |
| 4 | test_calculator_parser | `1++` | 抛 ToolExecutionError |
| 5 | test_api_run | calculator 正常 | 200, `result == 96`, `latency_ms >= 0`, 有 `trace_id` |
| 6 | test_api_run | echo 正常 | 200, `result == message` |
| 7 | test_api_run | tool 不存在 | 404, `error.code == "TOOL_NOT_FOUND"`, trace 落 error 分支 |
| 8 | test_api_run | 缺 `message` | 422, `error.code == "INVALID_REQUEST"` |
| 9 | test_api_run | calculator `1/0` | 400, `error.code == "TOOL_EXECUTION_ERROR"`, trace 落 error 分支 |
| 10 | test_api_traces | GET 命中真实 trace | 200, body 含 input/output |
| 11 | test_api_traces | GET 不存在 trace_id | 404, `error.code == "TRACE_NOT_FOUND"` |

**执行:** `pytest -q` 跑全部;`pytest -q tests/test_calculator_parser.py` 跑单测;FastAPI 启动 `uvicorn app.main:app --reload`。

## 13. 验收标准

- [ ] 能通过 HTTP 请求实际调用接口
- [ ] 成功响应里能看到工具结果和 trace_id
- [ ] 异常场景返回清晰错误,不暴露堆栈
- [ ] `pytest -q` 全部通过
- [ ] 工具调用逻辑和接口处理逻辑不混在一起,有清晰的 dispatcher 层
- [ ] 加新工具只需:`tools/<name>.py` 写一个 `@register_tool` 函数 + `tools/__init__.py` 加一行 import,dispatcher 无需改动

## 14. 配置与运行

**`pyproject.toml` 依赖:** `fastapi`、`uvicorn[standard]`、`pytest`(开发依赖)。

**环境变量:**
- `TRACE_DB_PATH`:trace SQLite 文件路径,默认 `./traces.db`

**运行:**
- 开发:`uvicorn app.main:app --reload`
- 测试:`pytest -q`
- 手动验证:`curl -X POST http://127.0.0.1:8000/agent/run -H "Content-Type: application/json" -d '{"message":"12 * 8","tool":"calculator"}'`
