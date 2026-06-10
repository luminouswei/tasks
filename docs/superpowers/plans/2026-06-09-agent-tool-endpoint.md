# Agent Tool Endpoint 实施计划(教学版)

> **执行方式:** 本计划是**教学版**——你写代码,我 review。请按顺序完成每个任务,每个任务结束贴代码给我。
>
> **TDD 原则贯穿本计划:** 能先写测试就先写测试,实在不能(比如项目骨架)再写实现。AI 助手**禁止代写实现代码**,只能在 review 时给修改建议。

**目标:** 实现 `POST /agent/run` 与 `GET /agent/traces/{trace_id}`,支持 calculator / echo 两个工具,trace 落 SQLite。

**架构:** 单进程 FastAPI 应用,API → dispatcher → tool 层级,trace_store 旁路写,装饰器自注册工具。

**技术栈:** Python 3.11+ / FastAPI / Pydantic / pytest / FastAPI TestClient / SQLite(std lib)

**每个任务的循环:**
1. 读"为什么这样设计"(我会先讲一段)
2. 自己写代码(我只在 review 时介入)
3. 跑测试/手动验证
4. 贴 diff 给我 review
5. 我给反馈 → 你改 → 通过后 commit

---

## Task 0:分支与项目骨架

**为什么:** 在 `task/agent-tool-endpoint` 分支上做事,不污染 main。也避免一次性把所有东西塞进 main 不便 review。

**Files:**
- Create: `pyproject.toml`
- Create: `app/__init__.py`(空)
- Create: `app/tools/__init__.py`(先空,后面填)
- Create: `tests/__init__.py`(空)
- Create: `.gitignore`

**步骤:**

- [ ] 切到 `main`,拉最新:`git checkout main && git pull`
- [ ] 建分支:`git checkout -b task/agent-tool-endpoint`
- [ ] 写 `pyproject.toml`:

```toml
[project]
name = "agent-tool-endpoint"
version = "0.1.0"
description = "Minimal AI agent tool calling backend"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

**为什么这么配:**
- `pythonpath = ["."]` 让 `from app.xxx import …` 在测试里能跑(不用装包)
- `[standard]` 给 uvicorn 带 uvloop / httptools,启动更快
- 不引 SQLAlchemy 等 ORM,用 stdlib `sqlite3` 即可

- [ ] 写 `.gitignore`:
```
__pycache__/
*.pyc
.pytest_cache/
.venv/
traces.db
*.egg-info/
```
- [ ] 写 `app/__init__.py`、`app/tools/__init__.py`、`tests/__init__.py`(都空文件)
- [ ] `pip install -e ".[dev]"` 装一下,确认 `pytest --version` 能跑
- [ ] commit:`chore: scaffold project skeleton`

---

## Task 1:错误基类与 code→http_status 映射

**为什么先做这个:** 后面所有代码都会用 `ToolExecutionError` 和 `CODE_TO_STATUS`,先把"什么是用户错、什么是服务错、什么状态码"这层抽象定下来,后面写 dispatcher 不会纠结。

**Files:**
- Create: `app/errors.py`
- Create: `tests/test_errors.py`

**要写什么:**
- `AppError`(基类,带 `code` 和 `http_status` 类属性,带 `message` 实例属性)
- `ToolExecutionError( AppError)`,`code="TOOL_EXECUTION_ERROR"`,`http_status=400`
- `CODE_TO_STATUS` 字典:`TOOL_NOT_FOUND→404`、`TOOL_EXECUTION_ERROR→400`、`TRACE_NOT_FOUND→404`、`INVALID_REQUEST→422`、`INTERNAL_ERROR→500`
- (不写 `ToolNotFoundError` / `TraceNotFoundError` 类——spec §9.1 决定它们只作为字符串 code 出现,不抛异常)

**为什么 `AppError` 基类还要保留:** 主要是给 `ToolExecutionError` 一个统一的父类,以后工具想抛新的业务异常可以继承。也让"有 code + http_status 的异常"有共同形态。

**TDD 步骤:**

- [ ] **写测试** `tests/test_errors.py`:
  - 测 `ToolExecutionError("oops").code == "TOOL_EXECUTION_ERROR"`
  - 测 `.http_status == 400`
  - 测 `.message == "oops"`
  - 测 `CODE_TO_STATUS["TOOL_NOT_FOUND"] == 404`
  - 测 `CODE_TO_STATUS["INTERNAL_ERROR"] == 500`
- [ ] 跑 `pytest -q tests/test_errors.py` —— **必须先看到红**
- [ ] 写 `app/errors.py` 实现
- [ ] 再跑 —— 必须绿
- [ ] commit:`feat(errors): add AppError base and code→status map`

**学习点:** 异常类只放数据,不放逻辑。`CODE_TO_STATUS` 单独成表是因为 dispatcher 返回 `ErrorPayload` 是字符串 code,API 层需要查这个表映射 HTTP 状态——单点修改。

---

## Task 2:Pydantic 请求模型

**为什么:** FastAPI 的请求校验靠 Pydantic。把校验规则(spec §4.1)集中在 `RunRequest` 上,API 层就只关心"这个对象有/没有"。

**Files:**
- Create: `app/models.py`
- Create: `tests/test_models.py`

**Pydantic v2 注意:** 用 `field_validator` 而不是 v1 的 `validator`。trim 长度检查需要先把空白去掉,这里需要自己写 validator。

**要写什么:**
- `RunRequest`:
  - `message: str`,`field_validator` 检查 trim 后非空且 ≤ 2000
  - `tool: str`,`field_validator` 检查 trim 后非空、≤ 64、匹配 `^[a-z][a-z0-9_]*$`
- 失败时 Pydantic 抛 `ValidationError`,FastAPI 自动转 422,handler 里我们再统一包成 `INVALID_REQUEST` envelope(这一层在 Task 7)

**TDD 步骤:**

- [ ] 写测试 `tests/test_models.py`:
  - `RunRequest(message="hi", tool="echo")` 成功构造
  - 缺 `message` → `ValidationError`
  - `message=""`(trim 后空)→ `ValidationError`
  - `tool="Weather"`(大写)→ `ValidationError`
  - `tool="my tool"`(含空格)→ `ValidationError`
  - `message` 超 2000 字符 → `ValidationError`
- [ ] 跑测试 → 必须先红
- [ ] 写 `app/models.py` 实现
- [ ] 再跑 → 必须绿
- [ ] commit:`feat(models): add RunRequest with validators`

**学习点:** Pydantic 校验失败抛 `ValidationError`,FastAPI 把它转成 `RequestValidationError`,我们在 `main.py` 用 exception handler 统一接住。**不要在 model 里 raise `ToolExecutionError`**——业务错误用工具异常,模型错误用 Pydantic,边界要清楚。

---

## Task 3:Calculator 解析器(本任务最大,预留 30-45 分钟)

**为什么:** 这是你"练手"的核心。手写递归下降解析器能让你理解语法分析、错误传播、和"不靠 eval"的安全模型。

**Files:**
- Create: `app/parser.py`
- Create: `tests/test_calculator_parser.py`

**要写什么 — 三个函数:**
- `tokenize(expr: str) -> list[Token]` — 把字符串切成 token
- `parse(tokens: list[Token]) -> list[Token]`(其实可以合并到 evaluate,先简单点)— 看你设计
- `parse_and_eval(expr: str) -> float` — 公开入口

**Token 类型建议:** 用 dataclass 或 NamedTuple,字段 `type`(`"NUMBER"` / `"PLUS"` / `"MINUS"` / `"STAR"` / `"SLASH"` / `"LPAREN"` / `"RPAREN"`)和 `value`(NUMBER 时是 float,其他是 None)。

**递归下降文法(spec §7):**
```
expression := term (('+' | '-') term)*
term       := factor (('*' | '/') factor)*
factor     := ('-' factor) | NUMBER | '(' expression ')'
```

**异常处理:**
- 任何不符合文法的输入(空串、字母、`1++`、`(1`、`1+`) → 抛 `ToolExecutionError("parse error: ...")`
- 除零 → `ToolExecutionError("division by zero")`
- 算术结果用 float,整数仍是 `96.0`(测试用 `== 96` 容忍,或 `pytest.approx`)

**TDD 步骤(先列要测的用例,再写):**

- [ ] 写测试 `tests/test_calculator_parser.py`(写 6-8 个):
  - `parse_and_eval("12 * 8") == 96`
  - `parse_and_eval("1 + 2 + 3") == 6`
  - `parse_and_eval("10 - 4 - 3") == 3`(左结合!)
  - `parse_and_eval("2 * 3 + 4") == 10`
  - `parse_and_eval("-3 + 5") == 2`
  - `parse_and_eval("2 * (3 + 4)") == 14`
  - `parse_and_eval("1/0")` 抛 `ToolExecutionError` 且 message 含 "division"
  - `parse_and_eval("1++")` 抛 `ToolExecutionError`
  - `parse_and_eval("1 + a")` 抛 `ToolExecutionError`
  - `parse_and_eval("")` 抛 `ToolExecutionError`
- [ ] 跑 → 红
- [ ] 写 `app/parser.py` 实现(先 tokenize 再 eval,中间不显式构造 AST——简单)
- [ ] 再跑 → 绿
- [ ] commit:`feat(parser): hand-rolled recursive-descent calculator`

**学习点(我会在你写完之后展开讲):**
- 左结合 vs 右结合:`a - b - c` 应该是 `(a - b) - c`,写法上 term 循环里 `result = result OP next_factor`。
- 一元负号的递归写法:`factor := ('-' factor) | NUMBER | '(' expression ')'`,直接递归调 `factor()` 处理负号。
- 怎么从 token 流识别"少了一个右括号":parse 完没到 `EOF` 就报错。

---

## Task 4:工具注册表 + echo 工具(最小可跑)

**为什么先做 echo 不做 calculator:** echo 是 1 行函数,能让注册表机制先跑通,calculator 是 Task 3 解析器的薄壳,放最后接。

**Files:**
- Create: `app/registry.py`
- Create: `app/tools/echo.py`
- Create: `tests/test_registry.py`

**要写什么:**
- `app/registry.py`:
  - 模块级 dict `_REGISTRY: dict[str, ToolSpec] = {}`
  - `@register_tool(name, *, description)` 装饰器
  - `ToolSpec` dataclass:`name`、`description`、`run: Callable[[str], Any]`
  - `get_tool(name) -> ToolSpec`(找不到 raise KeyError 或自己定义,统一上抛)
  - `registered_names() -> list[str]`
- `app/tools/echo.py`:
  - `@register_tool(name="echo", description="回显输入")`
  - `def run(message: str) -> str: return message`

**TDD 步骤:**

- [ ] 写测试 `tests/test_registry.py`:
  - 装饰器把函数注册到 `_REGISTRY` 里,key 是 name
  - `get_tool("echo")` 返回正确的 `ToolSpec`,`spec.run("hi") == "hi"`
  - `get_tool("nope")` 抛 `KeyError`
  - `registered_names()` 至少包含 "echo"
- [ ] 跑 → 红
- [ ] 写 `app/registry.py` + `app/tools/echo.py`
- [ ] 跑 → 绿
- [ ] commit:`feat(registry): tool registry + echo tool`

**学习点:** 全局 dict 做注册表最简单,但有副作用——测试顺序会影响。Task 6 写 dispatcher 测试时,我们会用一个 `stub_tool_registry` fixture 在测试里塞假工具,然后清理。

---

## Task 5:Calculator 工具(壳,接 Task 3 解析器)

**Files:**
- Create: `app/tools/calculator.py`
- Create: `tests/test_calculator_tool.py`(可选,API 测试已覆盖,这是单测壳层)

**要写什么:**
- `app/tools/calculator.py`:
  ```python
  @register_tool(name="calculator", description="四则运算")
  def run(message: str) -> float:
      return parse_and_eval(message)
  ```

- [ ] 写 1-2 个测试验证 calculator 工具本身能跑
- [ ] 跑 → 绿(应该很顺,因为 Task 3 已经测过 parser)
- [ ] commit:`feat(tools): calculator tool`

**学习点:** 工具层就是"取 message,调实现,返回结果",不碰异常转换——parser 抛 `ToolExecutionError`,工具不接,直接透传给 dispatcher。这是分层的好处:每一层只做一件事。

---

## Task 6:Trace 存储(SQLite)

**为什么:** 之前的设计选择(spec §8)——SQLite 单文件,跨重启可查。`trace_store.py` 是独立模块,可以被 dispatcher 和 API 路由独立测试。

**Files:**
- Create: `app/trace_store.py`
- Create: `tests/test_trace_store.py`

**要写什么:**
- `init_db(path: str | Path) -> None` — 建表 + 索引
- 模块级 `_db_path` 由环境变量 `TRACE_DB_PATH` 决定
- `insert_trace(record: dict) -> None`:
  - 字段:trace_id, tool, input, output(JSON 序列化,失败时 None), error_code, error_msg, latency_ms, created_at(ISO8601 UTC)
- `get_trace(trace_id: str) -> dict | None`:
  - 读到 output 是 JSON 字符串,`json.loads` 解出来
  - 返回 dict 结构与 spec §4.2 一致
- 连接用 `sqlite3.connect(path, check_same_thread=False)`,函数内 `with` 用游标

**TDD 步骤:**

- [ ] 写测试 `tests/test_trace_store.py`:
  - 用 `tmp_path` fixture,传临时 db 路径
  - 插入一条成功 trace,`get_trace(trace_id)` 取回,`output` 是 dict(已解 JSON)
  - 插入一条失败 trace(error_code="TOOL_NOT_FOUND"),`output is None`,`error` 字段有 code 和 message
  - 查不存在的 trace_id → `None`
  - **跨调用持久化**:写一条 → 重新打开 db(模拟重启)→ 还能查到
- [ ] 跑 → 红
- [ ] 写实现
- [ ] 再跑 → 绿
- [ ] commit:`feat(trace_store): SQLite trace persistence`

**学习点:**
- `created_at` 用 `datetime.now(timezone.utc).isoformat()` 拿 ISO8601,带 `+00:00`。
- JSON 序列化时 `ensure_ascii=False` 保留中文,免得 trace 里中文 message 被转成 `\uXXXX`。
- 测试用 `tmp_path` 而不是写死 `./traces.db`,避免污染仓库和并行测试互相干扰。

---

## Task 7:Dispatcher(分层粘合剂)

**为什么这是最关键的一步:** dispatcher 是 spec §3 架构里唯一接触 registry + trace_store + 错误映射的地方。把它写对,API 层就只是薄壳。

**Files:**
- Create: `app/dispatcher.py`
- Create: `tests/test_dispatcher.py`

**要写什么(spec §5.4):**

```python
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

def dispatch(tool_name: str, message: str) -> DispatchResult: ...
```

**流程(按 spec §5.4 写):**
1. `trace_id = uuid4().hex`
2. `start = perf_counter()`
3. 查 registry,没有 → `error = ErrorPayload("TOOL_NOT_FOUND", f"tool '{name}' is not registered")`,result=None,latency=0,继续(不 raise)
4. 调 `spec.run(message)`:
   - 正常 → `result = 实际值`,error=None
   - `ToolExecutionError` → `result=None, error=ErrorPayload("TOOL_EXECUTION_ERROR", str(e))`
   - 其他 `Exception` → `result=None, error=ErrorPayload("INTERNAL_ERROR", "internal server error")`,**原始堆栈写日志**(`logger.exception(...)`)
5. `latency_ms = int((perf_counter() - start) * 1000)`
6. 写 trace(成功:output=json.dumps(result);失败:error_code / error_msg)
7. 写 trace 失败 → `logger.exception("trace insert failed")`,**不阻断返回**

**TDD 步骤:**

- [ ] 写测试 `tests/test_dispatcher.py`:
  - 准备一个临时 db + 把 `app.tools.echo` 注册清空,塞一个 fake 工具
  - 正常调用 fake 工具 → `result == fake 工具返回值`,`error is None`
  - fake 工具抛 `ToolExecutionError` → `error.code == "TOOL_EXECUTION_ERROR"`,trace 落了(error 分支)
  - fake 工具抛 `ValueError`(非 ToolExecutionError)→ `error.code == "INTERNAL_ERROR"`,message 固定,不暴露堆栈
  - 不存在的 tool_name → `error.code == "TOOL_NOT_FOUND"`,trace 也落了
  - `DispatchResult.trace_id` 是 32 位 hex
  - `latency_ms >= 0`
- [ ] 跑 → 红
- [ ] 写实现
- [ ] 跑 → 绿
- [ ] commit:`feat(dispatcher): tool dispatch with trace + error mapping`

**学习点:**
- dispatcher **不 raise**(除了 trace 写库失败,那个只 log)。这保证 API 层写起来是线性的,不用 try/except 一锅端。
- fake 工具注入测试:在测试里 `@register_tool(name="fake", ...)` 装饰一个本地函数,跑完从 `_REGISTRY` 摘掉。也可以用 `monkeypatch.setitem(_REGISTRY, ...)`。

---

## Task 8:FastAPI app + 全局 handler + POST /agent/run

**为什么:** 把前面所有模块串起来。这层不写业务逻辑,只做参数校验后调 dispatcher、再把 `DispatchResult` 转成 JSON 响应。

**Files:**
- Create: `app/main.py`
- Create: `tests/test_api_run.py`
- Create: `tests/conftest.py`(共享 fixture)

**要写什么:**
- `app/main.py`:
  - `app = FastAPI()`
  - 注册 exception handlers:
    - `RequestValidationError` → 422 + `{"error": {"code": "INVALID_REQUEST", "message": "..."}}`(`exc.errors()` 拼成可读消息,不要直接 dump)
    - `Exception` 兜底 → 500 + `{"error": {"code": "INTERNAL_ERROR", "message": "internal server error"}}` + log traceback
  - `POST /agent/run`:
    - 接收 `RunRequest`
    - 调 `dispatch(req.tool, req.message)`
    - 翻译结果:
      - `error is None` → 200 + `{"trace_id", "tool", "result", "latency_ms"}`
      - `error is not None` → 查 `CODE_TO_STATUS[error.code]`,返回对应状态 + `{"error": {"code", "message"}}`(**注意此时不返回 `result` 字段**)
  - 启动事件:`init_db(TRACE_DB_PATH)`(从环境变量读)
- `tests/conftest.py`:
  - `temp_db` fixture:用 `tmp_path` 把 `TRACE_DB_PATH` 改成临时文件
  - `client` fixture:`TestClient(app)`
  - `reset_registry` fixture(可选):清掉 fake 工具

**TDD 步骤:**

- [ ] 写测试 `tests/test_api_run.py`(对应 spec §12 用例 5-9):
  - 缺 `message` → 422, `error.code == "INVALID_REQUEST"`
  - 正常 `calculator` "12 * 8" → 200, `result == 96`, 有 `trace_id`, `latency_ms >= 0`
  - 正常 `echo` "hi" → 200, `result == "hi"`
  - tool 不存在 "weather" → 404, `error.code == "TOOL_NOT_FOUND"`
  - `calculator "1/0"` → 400, `error.code == "TOOL_EXECUTION_ERROR"`
- [ ] 跑 → 红
- [ ] 写 `app/main.py`(先写 handler 再写路由)
- [ ] 跑 → 绿
- [ ] commit:`feat(api): POST /agent/run endpoint with handlers`

**学习点:**
- `RequestValidationError` 的 `exc.errors()` 返回一个 list of dict,挑 `loc` 和 `msg` 拼成一句话,别直接 `str(exc)`——会暴露 Pydantic 内部细节。
- 成功响应里**有 `tool` 字段**,但**没有 `input`**(input 只在 trace 详情里有)。spec §4.1 成功响应是 `{trace_id, tool, result, latency_ms}`。
- 失败响应里**没有 `trace_id` 在外层**——但你**应该**让用户在失败时也能拿到 trace_id 方便 debug。这里有一个设计选择需要你拍板:失败响应是否要附 `trace_id`?
  - 选项 A:不加(响应体更干净,但 debug 要靠日志)
  - 选项 B:加(`{"error": {...}, "trace_id": "..."}`),方便用户提工单时报 trace_id
  - **我的建议:选 B**。理由:trace 落库是有成本的(每次都写),让它对用户可见是 0 边际成本的高价值。我会在 review 时确认你这个选择。

---

## Task 9:GET /agent/traces/{trace_id} + 收尾

**Files:**
- Create: `tests/test_api_traces.py`
- Modify: `app/main.py`(加路由)
- Create: `README.md`

**要写什么:**
- `GET /agent/traces/{trace_id}`:
  - `trace_id` 是路径参数,FastAPI 自动校验为非空字符串
  - 调 `trace_store.get_trace(trace_id)`
  - 命中 → 200 + 完整 trace(成功:有 `result`;失败:有 `error`)
  - 没命中 → 404, `error.code == "TRACE_NOT_FOUND"`
- `README.md`:跑测试 / 跑服务 / curl 示例(成功、echo、calculator 失败、tool 不存在、查 trace)

**TDD 步骤:**

- [ ] 写测试 `tests/test_api_traces.py`:
  - 先 POST 一次成功 → 拿 trace_id → GET → 200, body 有 `result`
  - 先 POST 一次失败 → 拿 trace_id → GET → 200, body 有 `error`,无 `result`
  - GET 不存在的 trace_id → 404, `error.code == "TRACE_NOT_FOUND"`
- [ ] 跑 → 红
- [ ] 实现路由
- [ ] 跑 → 绿
- [ ] 写 README
- [ ] **手动验证**:`uvicorn app.main:app --reload`,curl 跑 4 种场景
- [ ] commit:`feat(api): GET /agent/traces/{id} and README`

**学习点:**
- 路径参数用 `def get_trace(trace_id: str)` 这种类型注解,FastAPI 自动处理。
- 404 响应按 spec §4.1 表,`{"error": {"code": "TRACE_NOT_FOUND", "message": "..."}}`。
- README 写"如何本地跑 + 关键 curl 例子"对将来 review/交接很重要。

---

## Task 10:全量回归与自我验收

**这一步不算实现,但是收尾必备:**

- [ ] `pytest -q` 全跑一遍,11 个用例全绿
- [ ] `uvicorn app.main:app --reload` 跑起来,curl 至少跑这 4 个:
  - `curl -X POST http://127.0.0.1:8000/agent/run -H "Content-Type: application/json" -d '{"message":"12*8","tool":"calculator"}'`
  - 同上 `tool=echo`
  - 同上 `tool=weather`(404)
  - `curl http://127.0.0.1:8000/agent/traces/<上面拿到的 trace_id>`
- [ ] 对照 spec §13 验收标准,逐条勾
- [ ] 写一次自我 review:
  - "如果明天加 `weather` 工具,我需要改哪些文件?"(答案:新增 `app/tools/weather.py` + `app/tools/__init__.py` 加一行 import。dispatcher / main / 路由都不用改)
- [ ] 推分支:`git push -u origin task/agent-tool-endpoint`
- [ ] commit(若有收尾变更):`docs: update README with verification steps`

---

## 学习路径总结(给你做完后复盘用)

1. **分层:** API 薄壳 / dispatcher 调度 / tool 纯函数 / store I/O,各做一件事
2. **异常分层:** Pydantic 422 / `ToolExecutionError` 400 / dispatcher 兜底 500,边界清楚
3. **可观测性:** trace 落库 + GET 接口,debug 时只看 trace 表就能复现
4. **可扩展性:** 加工具 = 1 个文件 + 1 行 import,0 行 dispatcher 改动
5. **错误 vs 状态:** 失败不靠 raise 冒泡,靠返回值携带 error envelope

**Spec ↔ Plan 覆盖检查:**

| Spec 节 | Plan Task |
|---|---|
| §3 架构 | Task 8(实现各层粘合) |
| §4 接口契约 | Task 8 + Task 9 |
| §5 注册与分发 | Task 4 + Task 7 |
| §6 工具实现 | Task 4 + Task 5 |
| §7 解析器 | Task 3 |
| §8 Trace 存储 | Task 6 |
| §9 错误处理 | Task 1 + Task 8(handler) |
| §10 目录结构 | Task 0 + 各 Task 创建对应文件 |
| §12 测试方案 | Task 1/2/3/6/7/8/9 各自的测试 |
| §13 验收 | Task 10 |

无遗漏。
