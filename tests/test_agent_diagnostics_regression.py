"""Agent diagnostics 回归评测套件。

这套测试不是单点 unit test,而是把"跑一次 agent → 落库 → 取 diagnostics"
这条端到端路径当作一个整体,断言关键不变量稳定。覆盖链路:

  dispatch → store → diagnostics(纯函数) → API(HTTP 响应 shaping)

目标:

  1. 正常工具调用成功 → status=completed + 完整 7 事件 timeline + 可读 summary。
  2. 工具执行失败 → 分类成 tool_error + 失败信息保留 + timeline 含 tool_failed。
  3. 非 JSON 可序列化结果 → 走 fallback + 警告 + serialization_error 分类。
  4. 持久化失败边界 → 业务结果保留 + status=trace_persist_failed + DB 查不到。
  5. HTTP API smoke → 走 POST /agent/run + GET /agent/runs/{id}/diagnostics,
     验证 7 字段契约在 HTTP response shaping 后不破。

跟已有 test_api_diagnostics.py / test_dispatcher.py 的区别:
  - 已有测试是单点契约(每个文件盯一个函数或一个端点)。
  - 这套测试盯"跨层稳定"——dispatch + store + diagnostics + API 一条龙,
    一旦某层悄悄改了行为,这套会先红,告诉运维"哪条不变量破了"。

跑法:  pytest -q tests/test_agent_diagnostics_regression.py
报告:  pytest -q -s tests/test_agent_diagnostics_regression.py  # 看每条用例的 pass/fail 打印
"""
import re
from datetime import datetime

import pytest

from app import registry
from app.agent_run_store import get_run, init_db
from app.diagnostics import (
    SUGGESTED_ACTIONS,
    build_diagnostics,
    classify_failure_type,
)
from app.dispatcher import dispatch
from app.errors import ToolExecutionError
from app.registry import ToolSpec


# ---------- fixtures ----------

@pytest.fixture
def temp_agent_run_db(tmp_path, monkeypatch):
    """每次起一份隔离 SQLite,monkeypatch app.agent_run_store._db_path。"""
    db_path = tmp_path / "agent_runs.db"
    init_db(db_path)
    monkeypatch.setattr("app.agent_run_store._db_path", db_path)
    return db_path


@pytest.fixture(autouse=True)
def clear_registry():
    """每个测试前清空 tool registry,结束后恢复,避免互相污染。"""
    snapshot = registry._registry.copy()
    registry._registry.clear()
    yield
    registry._registry.clear()
    registry._registry.update(snapshot)


def _register(name: str, run_fn):
    """塞一个本地函数进 registry,try/finally 自动清理。"""
    registry._registry[name] = ToolSpec(name=name, description="fake tool", run=run_fn)


# 成功路径的关键事件名(顺序固定)
SUCCESS_TIMELINE = [
    "request_received", "validation_passed", "tool_dispatch_started",
    "tool_executed", "trace_serialized", "trace_persisted",
    "response_returned",
]


# ============================================================
# 用例 1: 正常工具调用成功
#   风险: 业务结果正确落库 + 7 事件时间线完整 + summary 可读
#   参照: test_dispatcher.test_dispatch_success_persists_to_store
#          test_api_diagnostics.test_diagnostics_success_run_has_complete_timeline
# ============================================================

def test_regression_case_1_happy_path_tool_call(temp_agent_run_db):
    """happy path: 结构化 dict 返回值,从 dispatch → store → diagnostics 一条龙。"""
    input_message = "hi"
    tool_name = "fake"

    def fake_tool(message: str):
        return {"echo": message}

    _register(tool_name, fake_tool)

    # ── A. dispatch 内存层 ────────────────────────────────────────
    result = dispatch(tool_name, input_message)
    run = result.run

    assert run.status == "completed"
    assert run.tool_result == {"echo": input_message}
    assert run.error is None
    assert run.warnings == []
    assert run.selected_tool == tool_name
    assert run.input == input_message
    assert re.fullmatch(r"[0-9a-f]{32}", run.run_id)
    assert [ev.name for ev in run.timeline] == SUCCESS_TIMELINE
    for ev in run.timeline:
        # at 字段是合法 ISO 8601,客户端能解析
        datetime.fromisoformat(ev.at)
    # 关键事件 detail 必须能区分阶段,便于运维一眼定位
    assert run.timeline[0].detail == f"tool={tool_name}"
    assert run.timeline[1].detail is None  # validation_passed
    assert run.timeline[2].detail == tool_name  # tool_dispatch_started
    assert run.timeline[6].detail is None  # response_returned

    # ── B. 持久化层 ───────────────────────────────────────────────
    loaded = get_run(run.run_id, temp_agent_run_db)
    assert loaded is not None
    assert loaded["status"] == "completed"
    assert loaded["tool_result"] == {"echo": input_message}
    assert loaded["error"] is None
    assert loaded["warnings"] == []
    assert loaded["tool_args"] == {"message": input_message}
    assert loaded["selected_tool"] == tool_name
    # DB 里 timeline 跟内存完全一致,不能丢尾部
    assert [ev["name"] for ev in loaded["timeline"]] == [ev.name for ev in run.timeline]

    # ── C. diagnostics 翻译层 ────────────────────────────────────
    diag = build_diagnostics(run)

    assert diag.run_id == run.run_id
    assert diag.status == "completed"
    assert diag.failure_type is None
    assert diag.failure_message is None
    assert diag.suggested_action is None
    assert [ev["name"] for ev in diag.timeline] == [ev.name for ev in run.timeline]
    # 摘要必须可读,出现工具名 + 状态
    assert tool_name in diag.diagnostic_summary
    assert "completed" in diag.diagnostic_summary.lower()

    print(f"[case_1] happy path tool call: PASS run_id={run.run_id}")


# ============================================================
# 用例 2: 工具执行失败
#   风险: tool/runtime 类失败能被稳定分类 + 失败原因可读 + 时间线保留 tool_failed
#   参照: test_dispatcher.test_dispatch_tool_execution_error
#          test_api_diagnostics.test_diagnostics_tool_execution_error_classified_as_tool_error
# ============================================================

def test_regression_case_2_tool_execution_failure(temp_agent_run_db):
    """tool failure: 工具主动抛 ToolExecutionError(工具责任,500)。

    跨层保护: failure_type=tool_error 稳定分类 + 失败原因不丢 + timeline
    用 tool_failed 而不是 tool_executed(运维一眼能区分业务成功 / 失败路径)。
    """
    tool_name = "fake"
    failure_message = "internal tool bug"

    def failing_tool(message: str):
        raise ToolExecutionError(failure_message)

    _register(tool_name, failing_tool)

    # ── A. dispatch 内存层 ────────────────────────────────────────
    result = dispatch(tool_name, "hi")
    run = result.run

    assert run.status == "failed"
    assert run.tool_result is None  # 失败不返回部分结果
    assert run.error is not None
    assert run.error.code == "TOOL_EXECUTION_ERROR"
    # 工具主动抛的异常,原始 message 不脱敏(运维需要定位)
    assert run.error.message == failure_message
    assert run.warnings == []

    event_names = [ev.name for ev in run.timeline]
    # 走 tool_failed 分支,不走 tool_executed
    assert "tool_failed" in event_names
    assert "tool_executed" not in event_names
    # validation + dispatch + 响应 阶段仍然记(响应确实出去了)
    assert "request_received" in event_names
    assert "validation_passed" in event_names
    assert "tool_dispatch_started" in event_names
    assert "trace_persisted" in event_names
    assert "response_returned" in event_names

    failed_ev = next(ev for ev in run.timeline if ev.name == "tool_failed")
    # detail 是 error.code,便于查询/告警直接拿字符串匹配
    assert failed_ev.detail == "TOOL_EXECUTION_ERROR"

    # ── B. 持久化层 ───────────────────────────────────────────────
    loaded = get_run(run.run_id, temp_agent_run_db)
    assert loaded is not None
    assert loaded["status"] == "failed"
    assert loaded["tool_result"] is None
    assert loaded["error"]["code"] == "TOOL_EXECUTION_ERROR"
    assert loaded["error"]["message"] == failure_message
    assert loaded["warnings"] == []
    # DB 里 timeline 不能丢 tool_failed
    assert "tool_failed" in [ev["name"] for ev in loaded["timeline"]]
    assert "tool_executed" not in [ev["name"] for ev in loaded["timeline"]]

    # ── C. diagnostics 翻译层 ────────────────────────────────────
    diag = build_diagnostics(run)

    assert diag.failure_type == "tool_error"
    assert classify_failure_type("TOOL_EXECUTION_ERROR", []) == "tool_error"
    assert diag.failure_message is not None
    assert diag.failure_message == failure_message
    assert diag.suggested_action == SUGGESTED_ACTIONS["tool_error"]
    # 摘要必须可读,运维一眼看到失败原因
    summary_lower = diag.diagnostic_summary.lower()
    assert (
        "toolexecutionerror" in summary_lower
        or failure_message in diag.diagnostic_summary
    )

    print(f"[case_2] tool execution failure: PASS failure_type={diag.failure_type}")


# ============================================================
# 用例 3: 非 JSON 可序列化结果 → 走 fallback
#   风险: 工具返回 set/bytes/自定义对象时,业务成功但不丢诊断,失败分类正确
#   参照: test_dispatcher.test_dispatch_unserializable_tool_result_still_persists
#          test_api_diagnostics.test_diagnostics_unserializable_classified_as_serialization_error
# ============================================================

def test_regression_case_3_unserializable_tool_result(temp_agent_run_db):
    """序列化 fallback: 工具返回 set(JSON 不可序列化)。

    跨层保护: 业务成功不丢诊断 — status 仍是 completed,但 diagnostics
    会从 warning 路径分类成 serialization_error,运维能区分"业务正常,
    序列化走了 fallback"和"业务真失败"。
    """
    tool_name = "fake"
    unserializable_value = {1, 2, 3}

    def set_tool(message: str):
        return unserializable_value

    _register(tool_name, set_tool)

    # ── A. dispatch 内存层 ────────────────────────────────────────
    result = dispatch(tool_name, "hi")
    run = result.run

    # 业务是成功的,序列化是辅助路径
    assert run.status == "completed"
    # 内存里仍是原始 set(主调用方拿得到)
    assert run.tool_result == unserializable_value
    assert run.error is None
    # 有 caveat
    assert len(run.warnings) == 1
    assert run.warnings[0].code == "TOOL_RESULT_SERIALIZATION_FALLBACK"
    assert run.warnings[0].message  # 非空就行

    event_names = [ev.name for ev in run.timeline]
    # 走 fallback 事件,不走 trace_serialized
    assert "trace_serialization_fallback" in event_names
    assert "trace_serialized" not in event_names
    # 但其他 6 个事件仍然齐
    for required in [
        "request_received", "validation_passed", "tool_dispatch_started",
        "tool_executed", "trace_persisted", "response_returned",
    ]:
        assert required in event_names, f"missing event: {required}"

    fallback_ev = next(
        ev for ev in run.timeline if ev.name == "trace_serialization_fallback"
    )
    # detail 是不可序列化值的类型名,运维一眼看到是哪种类型
    assert fallback_ev.detail == "set"

    # ── B. 持久化层 ───────────────────────────────────────────────
    loaded = get_run(run.run_id, temp_agent_run_db)
    assert loaded is not None
    assert loaded["status"] == "completed"
    # DB 里 tool_result 是结构化 fallback dict,不是 None 也不是原始 set
    assert loaded["tool_result"] is not None
    assert loaded["tool_result"]["_serialization"] == "fallback"
    assert loaded["tool_result"]["type"] == "set"
    assert "repr" in loaded["tool_result"]
    # warnings 也落库
    assert len(loaded["warnings"]) == 1
    assert loaded["warnings"][0]["code"] == "TOOL_RESULT_SERIALIZATION_FALLBACK"

    # ── C. diagnostics 翻译层 ────────────────────────────────────
    diag = build_diagnostics(run)

    # 注意: 走 warning 路径,所以 error 是 None,classification 看 warnings
    assert diag.failure_type == "serialization_error"
    assert classify_failure_type(None, run.warnings) == "serialization_error"
    # 业务状态仍然是 completed,不要因为有 warning 就标 failed
    assert diag.status == "completed"
    assert diag.suggested_action == SUGGESTED_ACTIONS["serialization_error"]
    summary_lower = diag.diagnostic_summary.lower()
    assert (
        "serial" in summary_lower
        or "fallback" in summary_lower
    )

    print(
        f"[case_3] unserializable tool result: PASS "
        f"failure_type={diag.failure_type} warning_code={run.warnings[0].code}"
    )


# ============================================================
# 可选 stretch: 用例 4: persistence_error 边界
#   风险: insert_run 抛异常时,业务结果保留 + status 改 trace_persist_failed
#         + 在 DB 里查不到这条 run,但 diagnostics 仍能从内存响应里翻译出来
#   参照: test_dispatcher.test_dispatch_surfaces_persistence_failure
# ============================================================

def test_regression_case_4_persistence_failure_boundary(temp_agent_run_db, monkeypatch):
    """持久化失败边界: insert_run 抛 RuntimeError(模拟磁盘满 / DB 锁)。

    跨层保护: 业务结果保留 + status 盖成 trace_persist_failed + timeline
    把 trace_persisted 替换成 trace_persist_failed 但 response_returned 仍在。
    DB 里查不到这条 run(因为插入没成功),但内存响应 + diagnostics 完整。
    """
    tool_name = "fake"
    sim_exception = "disk full: simulated"

    def broken_insert_run(record, path=None):
        raise RuntimeError(sim_exception)

    monkeypatch.setattr("app.dispatcher.insert_run", broken_insert_run)

    def ok_tool(message: str):
        return {"ok": True}

    _register(tool_name, ok_tool)

    # ── A. dispatch 内存层 ────────────────────────────────────────
    result = dispatch(tool_name, "hi")
    run = result.run

    # 业务结果保留(主调用方拿得到)
    assert run.tool_result == {"ok": True}
    # status 盖成 trace_persist_failed(只在内存,DB 没这行)
    assert run.status == "trace_persist_failed"
    assert run.error is not None
    assert run.error.code == "TRACE_PERSIST_FAILED"
    # error.message 带异常类名 + 文本,便于运维定位
    assert "RuntimeError" in run.error.message
    assert sim_exception in run.error.message

    event_names = [ev.name for ev in run.timeline]
    # trace_persisted 被替换,response_returned 仍在
    assert "trace_persist_failed" in event_names
    assert "response_returned" in event_names
    assert "trace_persisted" not in event_names
    failed_ev = next(
        ev for ev in run.timeline if ev.name == "trace_persist_failed"
    )
    # detail 是异常类名,operator-facing
    assert failed_ev.detail == "RuntimeError"

    # ── B. 持久化层 ───────────────────────────────────────────────
    assert get_run(run.run_id, temp_agent_run_db) is None

    # ── C. diagnostics 翻译层 ────────────────────────────────────
    diag = build_diagnostics(run)

    assert diag.failure_type == "persistence_error"
    assert classify_failure_type("TRACE_PERSIST_FAILED", []) == "persistence_error"
    assert diag.status == "trace_persist_failed"
    assert diag.suggested_action == SUGGESTED_ACTIONS["persistence_error"]
    # 摘要能区分这种"业务成但持久化失败"的特殊形态
    summary_lower = diag.diagnostic_summary.lower()
    assert "persist" in summary_lower or "trace" in summary_lower

    print(
        f"[case_4] persistence failure boundary: PASS "
        f"failure_type={diag.failure_type} tool_result_preserved=True"
    )


# ============================================================
# 用例 5: HTTP API smoke — 走完整 HTTP 链路
#   风险: dispatch / store / diagnostics 都在内部测试了,但 HTTP response
#         shaping(7 字段契约序列化 + 状态码)这条链没人盯。
#         一旦 main.py 里 _diagnostics_to_response 改了字段名 / 顺序 / 类型,
#         客户端 JSON 解析会破,但纯函数测试不会红。
#   参照: test_api_diagnostics.test_diagnostics_response_has_7_keys
#          test_api_diagnostics.test_diagnostics_success_run_has_complete_timeline
# ============================================================

def test_regression_case_5_api_smoke_via_http(client):
    """HTTP smoke: 走 POST /agent/run → GET /agent/runs/{id}/diagnostics 一条龙。

    跟 case_1 的区别:
      - case_1 直接调 build_diagnostics()(纯函数层断言)。
      - case_5 走 HTTP(TestClient 模拟真实请求),盯 response shaping
        (7 字段契约 + HTTP 200 + 摘要文本在 JSON 里完好)。

    两层各自独立断言:任一层破了,各自的用例会先红。
    """
    tool_name = "fake_http"
    input_message = "hi"

    def fake_tool(message: str):
        return {"echo": message}

    _register(tool_name, fake_tool)

    # ── POST /agent/run ───────────────────────────────────────────
    post = client.post(
        "/agent/run",
        json={"tool": tool_name, "message": input_message},
    )
    assert post.status_code == 200, f"POST failed: {post.status_code} {post.text}"
    run_id = post.json()["run_id"]
    assert re.fullmatch(r"[0-9a-f]{32}", run_id)

    # ── GET /agent/runs/{id}/diagnostics ─────────────────────────
    diag_resp = client.get(f"/agent/runs/{run_id}/diagnostics")
    assert diag_resp.status_code == 200, f"GET failed: {diag_resp.status_code} {diag_resp.text}"
    body = diag_resp.json()

    # 7 字段契约必须全在,客户端解析逻辑依赖这个
    DIAGNOSTICS_KEYS = (
        "run_id", "status", "failure_type", "failure_message",
        "timeline", "diagnostic_summary", "suggested_action",
    )
    for key in DIAGNOSTICS_KEYS:
        assert key in body, f"missing key: {key}"

    assert body["run_id"] == run_id
    assert body["status"] == "completed"
    assert body["failure_type"] is None
    assert body["failure_message"] is None
    assert body["suggested_action"] is None

    # timeline 通过 HTTP 后顺序仍然对齐 7 事件常量
    assert [ev["name"] for ev in body["timeline"]] == SUCCESS_TIMELINE

    # summary 在 JSON 里完好,运维一眼看到工具名 + 状态
    assert tool_name in body["diagnostic_summary"]
    assert "completed" in body["diagnostic_summary"].lower()

    # ── 负样本: 不存在的 run_id 走 404 RUN_NOT_FOUND ─────────────
    not_found = client.get("/agent/runs/00000000000000000000000000000000/diagnostics")
    assert not_found.status_code == 404
    # 404 响应里 code 嵌在 error 子对象里(看 main.py:_not_found_response)
    assert not_found.json()["error"]["code"] == "RUN_NOT_FOUND"

    print(f"[case_5] api smoke via http: PASS run_id={run_id}")


# ============================================================
# 自检 helper: 验证 SUCCESS_TIMELINE 常量跟 dispatcher 实际产出对齐
# 这条不是评测用例,是为了让"成功 7 事件顺序"这个不变量有显式断言,
#   一旦 dispatcher 改了顺序,这条会先红,提示该把常量同步。
# ============================================================

def test_regression_constant_success_timeline_is_still_7_events(temp_agent_run_db):
    """SUCCESS_TIMELINE 常量必须跟 dispatcher 实际产出一致。

    如果这条红了,先去 dispatcher.py 看哪里改了顺序,
    然后回来同步本常量 + README + agent_run.py 的 docstring。
    """
    def fake_tool(message: str):
        return {"echo": "x"}
    _register("fake", fake_tool)

    result = dispatch("fake", "x")

    actual = [ev.name for ev in result.run.timeline]
    assert actual == SUCCESS_TIMELINE
    assert len(actual) == 7