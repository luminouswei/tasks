# Agent Run Traces 实施计划(教学版)

> **执行方式:** 本计划是**教学版**——你写代码,我 review。请按顺序完成每个任务,每个任务结束贴代码给我。
>
> **TDD 原则贯穿本计划:** 能先写测试就先写测试,实在不能(比如项目骨架)再写实现。AI 助手**禁止代写实现代码**,只能在 review 时给修改建议。
>
> **今日设计基线(在 brainstorming 中已经过用户确认):**
>
> 1. **新旧端点关系:** 替换。`GET /agent/traces/{trace_id}` 旧端点 + `traces` 表一次清理,迁移到 `GET /agent/runs/{run_id}` + `agent_runs` 表。
> 2. **存储模块改名:** `app/trace_store.py` → `app/agent_run_store.py`,env `TRACE_DB_PATH` → `AGENT_RUN_DB_PATH`,默认 db 文件 `traces.db` → `agent_runs.db`。
> 3. **`tool_args` 字段:** 存 `{"message": "<原 message>"}` 形式,为以后工具支持多参预留契约。
> 4. **`status` 取值集:** `success` / `tool_not_found` / `tool_error` / `internal_error`,跟 `errors.py` 的几个 code 一一对应。

**目标:** 在昨天 `POST /agent/run` + `GET /agent/traces/{id}` 基础上,把"一次调用的快照"升级为"一次调用的完整生命周期",引入 `AgentRun` 模型,新增 `GET /agent/runs` 列表接口,并(可选)提供 `POST /agent/runs/{id}/replay` 回放能力。

**架构:** 沿用昨天的 API → dispatcher → tool → store 分层,只是把 `trace_id / tool / input / output` 扁字段重排为 `AgentRun` dataclass,把 `latency_ms / created_at` 替换为 `started_at / finished_at` + `status`。

**技术栈:** Python 3.11+ / FastAPI / Pydantic / pytest / FastAPI TestClient / SQLite(std lib)

**每个任务的循环:**
1. 读"为什么这样设计"
2. 自己写代码(我只在 review 时介入)
3. 跑测试/手动验证
4. 贴 diff 给我 review
5. 我给反馈 → 你改 → 通过后 commit

---

## Task 0:分支与基线确认

**为什么:** 切到昨天的最新 main,再开新分支,这样我们 diff 干净。还要顺手把昨天的 `traces.db` 从 .gitignore 已经忽略的清单里挪个位(改成 `agent_runs.db`)。

**Files:**
- Modify: `.gitignore`

**步骤:**

- [ ] `git checkout main && git pull`
- [ ] `git checkout -b feature/agent-run-traces`
- [ ] 跑一遍 `pytest -q`,确认 11 个用例全绿(基线)
- [ ] 看一下 `.gitignore`:`traces.db` 这一行改成 `agent_runs.db`(旧的 `traces.db` 本来就被 ignore,新名字只是和模块对齐)

**学习点:** 在已经合并的 main 上开新分支,而不是从某个老 commit fork,这样冲突面最小。

---

## Task 1:AgentRun 数据模型

**为什么先做模型:** 这是新 schema 的"宪法"。`run_id / input / selected_tool / tool_args / tool_result / status / error / started_at / finished_at` 这套字段,后面所有层(dispatcher、store、API 响应、测试断言)都按它对齐。先把它定死,后面写哪儿都清楚。

**Files:**
- Create: `app/agent_run.py`
- Create: `tests/test_agent_run.py`

**要写什么:**
- `RunStatus`:`Literal["success", "tool_not_found", "tool_error", "internal_error"]`(跟 `errors.py` 的几个 code 一一对应)
- `AgentErrorPayload` dataclass:`code: str`、`message: str`(原 `ErrorPayload` 的同形 copy,放新文件里,后面 `dispatcher.py` 引用它,旧的 `ErrorPayload` 删掉)
- `AgentRun` dataclass,字段严格按 spec:
  - `run_id: str`(32 位 hex,跟昨天 `trace_id` 同源)
  - `input: str`(原始 message)
  - `selected_tool: str`(请求里带的 tool 名,**不论成功失败都按字面值落库**——昨天已经这么做了,保留)
  - `tool_args: dict[str, Any]`(这次固定存 `{"message": "<原 message>"}`)
  - `tool_result: Any | None`(成功时填 JSON-serializable 值,失败时 `None`)
  - `status: RunStatus`
  - `error: AgentErrorPayload | None`
  - `started_at: str`(ISO8601 UTC,**dispatcher 进函数第一件事就生成**)
  - `finished_at: str`(ISO8601 UTC,工具返回/异常抛出后生成)

**TDD 步骤:**

- [ ] 写测试 `tests/test_agent_run.py`:
  - 构造一个成功 run(`status="success"`,`tool_result=96`,`error is None`)
  - 构造一个失败 run(`status="tool_error"`,`tool_result is None`,`error.code == "TOOL_EXECUTION_ERROR"`)
  - 构造一个 tool 不存在的 run(`status="tool_not_found"`,`error.code == "TOOL_NOT_FOUND"`)
  - 验证 `started_at < finished_at`(用 `datetime.fromisoformat` 解析比对)
  - 验证 `tool_args == {"message": "12 * 8"}` 这种结构
- [ ] 跑测试 → **必须先看到红**
- [ ] 写 `app/agent_run.py` 实现
- [ ] 跑 → 绿
- [ ] commit:`feat(agent_run): AgentRun dataclass + RunStatus`

**学习点:** 跟昨天的 `DispatchResult` 比,这次模型多带了 `started_at / finished_at`,是为了"我现在能不能告诉用户这个 run 跑了多久、有没有正在跑"——spec 里"成功路径"和"失败路径"看似都结束了,但有 `finished_at` 才能区分"已完成"和"还在跑"。今天我们其实只有同步执行,所有 run 一进来就是已完成,但先把字段占好位,后面如果加 streaming / 长任务不会卡 schema。

---

## Task 2:新存储层 `agent_run_store.py`

**为什么:** 昨天 `trace_store.py` 的 schema 跟今天的 `AgentRun` 不一致(字段名、字段含义都换了),硬扩展会牵动 `output → tool_result`、`error_code → error.code` 这类改名,加一堆 migration 噪音。新表干净。

**Files:**
- Create: `app/agent_run_store.py`
- Delete: `app/trace_store.py`(替换,不是并存)
- Modify: `tests/conftest.py`(`init_db` / `_db_path` 改用新模块)
- Modify: `tests/test_trace_store.py` → 重命名为 `tests/test_agent_run_store.py`,断言改新 schema
- Modify: `README.md` `TRACE_DB_PATH` 那行改名

**要写什么:**
- 模块级 `_db_path` 读 `AGENT_RUN_DB_PATH` env,默认 `agent_runs.db`
- `init_db(path=None)` 建表 + 索引(按 `created_at` 排,这里表里其实有 `started_at`,索引改成 `started_at`)
- 表 schema:
  - `run_id TEXT PRIMARY KEY`
  - `input TEXT NOT NULL`
  - `selected_tool TEXT NOT NULL`
  - `tool_args TEXT NOT NULL`(`json.dumps(ensure_ascii=False)` 后的字符串)
  - `tool_result TEXT`(成功时 JSON 序列化,失败时 `NULL`)
  - `status TEXT NOT NULL`
  - `error_code TEXT NULL`
  - `error_message TEXT NULL`
  - `started_at TEXT NOT NULL`
  - `finished_at TEXT NOT NULL`
- `insert_run(record: dict, path=None)` —— 入参字段名跟 `AgentRun` 对齐
- `get_run(run_id, path=None) -> dict | None` —— 读出时 `tool_args` / `tool_result` 都 `json.loads` 解开,组装 dict 形态跟 `AgentRun` 字段一一对应
- `list_runs(*, limit: int = 50, offset: int = 0, path=None) -> list[dict]` —— 按 `started_at DESC` 排序,支持基础分页

**TDD 步骤:**

- [ ] 重写测试 `tests/test_agent_run_store.py`(把 `tests/test_trace_store.py` 内容搬过来改字段名):
  - 插入一条成功 run,`get_run` 取回,`tool_result == 96`,`status == "success"`,`error is None`
  - 插入一条失败 run,`tool_result is None`,`status == "tool_error"`,`error["code"] == "TOOL_EXECUTION_ERROR"`
  - 插入一条 tool_not_found,`status == "tool_not_found"`
  - **关键:验证 `tool_args` 是 dict(已解 JSON),且等于 `{"message": "12 * 8"}`**
  - 验证 `started_at` / `finished_at` 都能 `datetime.fromisoformat` 解析成功
  - 查不存在的 `run_id` → `None`
  - 跨调用持久化:写一条 → 重新开 db(模拟重启)→ 还能查到
  - **新增:list_runs**:插 3 条,`list_runs()` 返回 3 条,按 `started_at DESC` 排;`limit=2` 只返回 2 条;`offset=1` 跳过第一条
- [ ] 跑 → 红(此时 `app/agent_run_store.py` 还没建)
- [ ] 写实现
- [ ] 跑 → 绿
- [ ] 删 `app/trace_store.py` + `tests/test_trace_store.py`
- [ ] 改 `conftest.py`:`from app.agent_run_store import init_db` + `monkeypatch.setattr("app.agent_run_store._db_path", db_path)`
- [ ] 跑全量 `pytest -q` —— **这个时刻应该 11 个旧测试全 fail**(因为它们都 import 旧 store)。这是预期的
- [ ] 跑新的 `tests/test_agent_run_store.py` → 绿
- [ ] commit:`feat(agent_run_store): SQLite persistence for AgentRun`

**学习点:** 删旧文件 + 改 schema 一步到位,看起来"破坏性"大,但**所有旧测试我们会在 Task 3/4 一起改对**。这比"两套共存"省心:不用写 migration,不用想"新请求走哪条路径"。

---

## Task 3:dispatcher 改造

**为什么:** dispatcher 是唯一接触 store + registry + 异常映射的地方。`DispatchResult` 也要按新 `AgentRun` 字段重排。

**Files:**
- Modify: `app/dispatcher.py`
- Modify: `tests/test_dispatcher.py`(旧测试改字段名 + 加新断言)

**要写什么:**

```text
@dataclass
class DispatchResult:
    run: AgentRun        # 整个 AgentRun 对象返回(包含所有持久化字段)
```

(注意:返回整个 `AgentRun` 对象,而不是昨天那种扁平的 `trace_id / tool / result / error / latency_ms` 字段。API 层读 `result.run.tool_result` 之类的,层级更清晰。)

**dispatch 流程重写:**
1. `run_id = uuid4().hex`
2. `started_at = datetime.now(timezone.utc).isoformat()`
3. 查 `get_tool(tool_name)`:
   - 找不到 → `status="tool_not_found"`,`error = AgentErrorPayload("TOOL_NOT_FOUND", ...)`,`tool_result = None`
4. 找到就执行 `spec.run(message)`:
   - 正常 → `status="success"`,`tool_result = <实际值>`,`error = None`
   - `ToolExecutionError` → `status="tool_error"`,`error = AgentErrorPayload("TOOL_EXECUTION_ERROR", str(e))`
   - 其他 `Exception` → `status="internal_error"`,`error = AgentErrorPayload("INTERNAL_ERROR", "internal server error")`,`logger.exception(...)`
5. `finished_at = datetime.now(timezone.utc).isoformat()`
6. `tool_args = {"message": message}`
7. 调 `insert_run(...)` —— **失败只 log,不阻断**(沿用昨天的设计)
8. 返回 `DispatchResult(run=AgentRun(...))`

**TDD 步骤:**

- [ ] 重写 `tests/test_dispatcher.py`(沿用昨天的 fake 工具模式):
  - 正常调用 fake 工具 → `result.run.status == "success"`,`tool_result == fake 返回值`,`error is None`
  - fake 工具抛 `ToolExecutionError` → `status == "tool_error"`,`error.code == "TOOL_EXECUTION_ERROR"`
  - fake 工具抛 `ValueError` → `status == "internal_error"`,`error.message` 固定为 "internal server error",**不暴露堆栈**
  - 不存在的 tool → `status == "tool_not_found"`,`error.code == "TOOL_NOT_FOUND"`
  - **`result.run.run_id` 是 32 位 hex**(`re.fullmatch(r"[0-9a-f]{32}", run_id)`)
  - **`result.run.tool_args == {"message": "<原 message>"}`**
  - **`started_at < finished_at`**(两个都能 `fromisoformat`)
  - **trace 落库验证**:跑一次 fake 工具,直接读 store,确认 `get_run(result.run.run_id)` 取回的对象字段都一致
  - **失败也落库**:fake 工具抛 `ToolExecutionError`,验证 `get_run` 取回的 `status == "tool_error"`
  - `tool_not_found` 也落库验证(昨天已经测过,这次只改字段名)
- [ ] 跑 → 红
- [ ] 改 `app/dispatcher.py` 实现
- [ ] 跑 → 绿
- [ ] commit:`feat(dispatcher): emit AgentRun with started/finished/status`

**学习点:** 这次 `DispatchResult` 改成"包一个 `AgentRun`"而不是"扁字段",**API 层就要用 `result.run.tool_result` 这种嵌套访问**。看起来比昨天啰嗦,但好处是**响应序列化直接 `model_dump()` 就能拿到一致的 dict 结构,不用在 API 层做手工拼装。

---

## Task 4:API 改造 + 新端点

**Files:**
- Modify: `app/main.py`
- Delete: `tests/test_api_traces.py`
- Create: `tests/test_api_runs.py`

**要写什么(`app/main.py`):**

1. `POST /agent/run`:
   - 成功 → 200,响应体:`{"run_id": ..., "selected_tool": ..., "tool_result": ..., "status": "success", "started_at": ..., "finished_at": ...}`
   - 失败 → 查 `CODE_TO_STATUS[error.code]` 状态,响应体:`{"run_id": ..., "selected_tool": ..., "status": <status>, "error": {"code": ..., "message": ...}, "started_at": ..., "finished_at": ...}`
2. **`GET /agent/runs`(新)**:
   - query 参数 `limit`(默认 50,最大 200)、`offset`(默认 0)
   - 调 `list_runs(limit, offset)`
   - 200 + `{"runs": [...], "limit": ..., "offset": ...}`,每条 run 跟 GET 单条同形
3. **`GET /agent/runs/{run_id}`(替换旧端点)**:
   - 调 `get_run(run_id)`,命中 200,没命中 404 `RUN_NOT_FOUND`
4. **删 `GET /agent/traces/{trace_id}`** —— 替换
5. **errors.py 加 `"RUN_NOT_FOUND": 404`**(旧 `"TRACE_NOT_FOUND"` 删掉)

**TDD 步骤:**

- [ ] 删 `tests/test_api_traces.py`
- [ ] 写新 `tests/test_api_runs.py`:
  - **POST 成功**:`{"message": "12 * 8", "tool": "calculator"}` → 200,响应里有 `run_id`(32 hex)、`status == "success"`、`tool_result == 96`、`selected_tool == "calculator"`、两个时间戳能解析
  - **POST 失败(tool 不存在)**:`tool="weather"` → 404,`status == "tool_not_found"`,`error.code == "TOOL_NOT_FOUND"`,有 `run_id`
  - **POST 失败(tool 执行异常)**:`calculator "1/0"` → 400,`status == "tool_error"`,`error.code == "TOOL_EXECUTION_ERROR"`
  - **GET /agent/runs/{run_id} 成功**:POST 一次拿 run_id → GET → 200,body 字段完整(`run_id / input / selected_tool / tool_args / tool_result / status / error / started_at / finished_at` 都在)
  - **GET /agent/runs/{run_id} 不存在** → 404,`error.code == "RUN_NOT_FOUND"`
  - **GET /agent/runs 列表**:POST 3 次(echo "a" / echo "b" / echo "c")→ GET /agent/runs → 200,`runs` 长度 3,按 `started_at DESC` 排(c 最新)
  - **GET /agent/runs 分页**:`?limit=2` → `runs` 长度 2;`?limit=2&offset=2` → 长度 1
  - **旧端点 404**:`GET /agent/traces/xxx` → 404(确认路由已删)
- [ ] 跑 → 红
- [ ] 改 `app/main.py`
- [ ] 改 `app/errors.py`:`TRACE_NOT_FOUND` 改成 `RUN_NOT_FOUND`
- [ ] 跑 → 绿
- [ ] commit:`feat(api): /agent/runs list + /agent/runs/{id} + remove legacy traces endpoint`

**学习点:** 把 `error.code` 当 HTTP status 的 single source of truth,这次再加一个 `RUN_NOT_FOUND`,改 `errors.py` + `CODE_TO_STATUS` 两处就能扩,API 层和 dispatcher 都不用动。

---

## Task 5:失败场景覆盖

**Files:** 已经在 `tests/test_api_runs.py` 里覆盖了(三个失败路径:validation 422、tool_not_found 404、tool_error 400)。这里只做"补一个 dispatcher 层级的 INTERNAL_ERROR 落库验证",确保 `status="internal_error"` 也能进 store。

- [ ] 在 `tests/test_dispatcher.py` 加一个用例:fake 工具抛 `ValueError` → dispatcher 跑完,`get_run(run_id)` 取回,`status == "internal_error"`,`error.code == "INTERNAL_ERROR"`,`tool_result is None`
- [ ] 跑 → 绿(应该已经过)
- [ ] commit:`test(dispatcher): cover internal_error path lands in store`

**学习点:** 4 个 status 全路径(success / tool_not_found / tool_error / internal_error)都要有"跑完落库"的证据。spec §12 测试方案的精神是"每个状态码都有一行 trace 对应"。

---

## Task 6:README 更新

**Files:** `README.md`

**要改什么:**
- `TRACE_DB_PATH` 全部改成 `AGENT_RUN_DB_PATH`,默认值 `agent_runs.db`
- `POST /agent/run` 的响应示例改新 schema(`run_id` / `selected_tool` / `tool_result` / `status` / `started_at` / `finished_at`)
- 删掉 `GET /agent/traces/{trace_id}` 段落
- 加 `GET /agent/runs` 段:列表响应 + 分页示例
- 加 `GET /agent/runs/{run_id}` 段:成功 / 失败两个示例
- 一段小说明:"失败路径也落 run 表,可以用 `run_id` 查到完整 trace"

- [ ] 改 README
- [ ] `uvicorn app.main:app --reload` 起服务,4 个 curl 走一遍(成功 / tool_not_found / tool_error / list)
- [ ] commit:`docs: README updated for AgentRun model + /agent/runs endpoints`

---

## Task 7:全量回归 + 验收

- [ ] `pytest -q` 全跑一遍
  - 旧 11 个测试 → **这次应该都不存在了**(被 Task 2/4 改名 / 删除)
  - 新的应该有:`test_agent_run` (1 个文件,~4 用例) + `test_agent_run_store` (~8 用例) + `test_dispatcher` 重写 (~9 用例) + `test_api_runs` (~8 用例) + 旧的 `test_calculator_parser / test_calculator / test_models / test_errors / test_registry` 不动
  - **总数 ≥ 25 个**
- [ ] `uvicorn app.main:app --reload` 起服务,curl 走 5 个场景:
  - `POST /agent/run {"message":"12*8","tool":"calculator"}` → 拿 `run_id`
  - `POST /agent/run {"message":"1/0","tool":"calculator"}` → 拿 `run_id`(验证 `status="tool_error"`)
  - `POST /agent/run {"message":"hi","tool":"weather"}` → 404,`run_id` 还在
  - `GET /agent/runs` → 看到上面 3 条都在,按时间倒序
  - `GET /agent/runs/{run_id}` → 拿第 1 条的完整 trace
- [ ] 对照今天的"复盘检查点"5 条,逐条勾
- [ ] 推分支:`git push -u origin feature/agent-run-traces`
- [ ] 开 PR,描述里写:
  - 新增字段
  - 成功 / 失败路径如何记录
  - curl 验证步骤
  - 测试命令 + 结果

---

## (可选)Task 8:Replay 端点

**Files:**
- Modify: `app/main.py`
- Create: `tests/test_api_replay.py`

**要做的事:**
- `POST /agent/runs/{run_id}/replay`:
  - 读历史 run 的 `input` 和 `tool_args`(优先用 `tool_args["message"]`,fallback `input`)
  - 调 `dispatch(selected_tool, message)` —— **新生成一个 run_id**,跟历史 run 完全独立
  - 返回 200 + 新 run 的 dispatch 结果(同 `POST /agent/run` 响应形态)
  - 历史 run 不存在 → 404 `RUN_NOT_FOUND`

**TDD:**
- [ ] 写测试:POST /agent/run 一次,拿 run_id A → POST /agent/runs/{A}/replay → 拿到新 run_id B,B ≠ A,B 的 input / tool_args 跟 A 一致
- [ ] 跑 → 红 → 实现 → 绿
- [ ] 验证:replay 一个之前 tool_not_found 的 run,新 run 仍然 tool_not_found(replay 是"重跑",不是"修复")
- [ ] 验证:replay 失败路径也生成 run 记录(从这次 Task 5 的覆盖自然来)
- [ ] commit:`feat(api): POST /agent/runs/{id}/replay`

**学习点:** Replay 的边界是"读历史 + 重新调度",**不修改**历史 run。这是"日志"和"动作"的区别:历史 run 是日志,replay 是动作。如果未来加"修复 + 回写历史",那要新开一个 `PATCH` 端点,不要让 replay 越界。

---

## 几点小约定

- 每个 task 结束贴 diff 给我 review,我给反馈 → 你改 → 通过再 commit
- 跑 `pytest -q` 全量绿是 commit 的最低门槛
- 如果中途发现某个 task 太大(比如 dispatcher 改造,可能比预想多 30%),告诉我我们再拆

---

## 复盘检查点(任务里指定的,验收时逐条过)

1. 这份 trace 是否真的能帮助你定位"为什么 agent 选了这个工具、工具为什么失败"?
2. `run_id` 是否贯穿了请求、工具调用、错误返回和查询接口?
3. 错误处理是否清楚地区分了"用户输入问题"和"系统内部问题"?
4. 测试是不是只测了 happy path,还是也覆盖了失败路径?
5. trace 结构未来是否容易扩展到多轮 agent、多工具链式调用?

## Spec ↔ Plan 覆盖检查

| Spec 节 | Plan Task |
|---|---|
| §1 AgentRun 字段定义 | Task 1 |
| §2 run_id 唯一 | Task 1 + Task 3(dispatcher 生成) |
| §3 简单持久化(沿用 SQLite) | Task 2 |
| §4 GET /agent/runs / GET /agent/runs/{run_id} | Task 4 |
| §5 失败场景记录 | Task 3 + Task 5 |
| §6 测试覆盖 | Task 1/2/3/4/5 各自 TDD |
| §7 README | Task 6 |
| §8 (可选) Replay | Task 8 |
| §9 复盘检查点 | Task 7 验收步骤 |

无遗漏。
