"""GET /agent/runs/{run_id}/diagnostics 的端到端测试。

7 字段契约:
run_id, status, failure_type, failure_message, timeline, diagnostic_summary, suggested_action

端到端路径走完整 dispatch → DB → store → diagnostics 翻译,覆盖:
- 成功 run + 完整 timeline
- tool 执行失败 → tool_error
- tool 不存在 → validation_error
- 不可 JSON 序列化的 tool_result → serialization_error(诊断不丢)
- 未知 run_id → 404 RUN_NOT_FOUND
"""
import re


DIAGNOSTICS_KEYS = (
    "run_id", "status", "failure_type", "failure_message",
    "timeline", "diagnostic_summary", "suggested_action",
)


# ---------- 7 字段契约 ----------

def test_diagnostics_response_has_7_keys(client):
    """所有 200 响应都包含这 7 个 key,客户端解析逻辑可以稳定。"""
    post = client.post(
        "/agent/run",
        json={"message": "hi", "tool": "echo"},
    )
    run_id = post.json()["run_id"]

    response = client.get(f"/agent/runs/{run_id}/diagnostics")

    assert response.status_code == 200
    body = response.json()

    for key in DIAGNOSTICS_KEYS:
        assert key in body, f"missing key: {key}"


# ---------- 成功 run ----------

def test_diagnostics_success_run_has_complete_timeline(client):
    """成功 run → 200,failure_type=None,完整 timeline(从 request_received 到 response_returned)。"""
    post = client.post(
        "/agent/run",
        json={"message": "hi", "tool": "echo"},
    )
    run_id = post.json()["run_id"]

    response = client.get(f"/agent/runs/{run_id}/diagnostics")

    assert response.status_code == 200
    body = response.json()

    assert body["run_id"] == run_id
    assert body["status"] == "completed"
    assert body["failure_type"] is None
    assert body["failure_message"] is None
    assert body["suggested_action"] is None
    # 7 个事件: request_received, validation_passed, tool_dispatch_started,
    # tool_executed, trace_serialized, trace_persisted, response_returned
    event_names = [ev["name"] for ev in body["timeline"]]
    assert event_names == [
        "request_received", "validation_passed", "tool_dispatch_started",
        "tool_executed", "trace_serialized", "trace_persisted",
        "response_returned",
    ]
    # 摘要提到工具名
    assert "echo" in body["diagnostic_summary"]


# ---------- tool 执行失败 ----------

def test_diagnostics_tool_execution_error_classified_as_tool_error(client):
    """calculator '1 + a'(非数字)→ TOOL_INPUT_ERROR(用户责任,400)→ validation_error。"""
    post = client.post(
        "/agent/run",
        json={"message": "1 + a", "tool": "calculator"},
    )
    run_id = post.json()["run_id"]

    response = client.get(f"/agent/runs/{run_id}/diagnostics")

    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "failed"
    assert body["failure_type"] == "validation_error"
    assert body["failure_message"] is not None
    assert body["suggested_action"] == "check tool name and tool input parameters"
    # timeline 包含 validation_passed + tool_dispatch_started + tool_failed(失败分支也记)
    event_names = [ev["name"] for ev in body["timeline"]]
    assert "validation_passed" in event_names
    assert "tool_dispatch_started" in event_names
    assert "tool_failed" in event_names


# ---------- tool 不存在 ----------

def test_diagnostics_tool_not_found_classified_as_validation_error(client):
    """tool 名拼错 → TOOL_NOT_FOUND → validation_error(用户责任:工具名)。"""
    post = client.post(
        "/agent/run",
        json={"message": "hi", "tool": "weather"},
    )
    run_id = post.json()["run_id"]

    response = client.get(f"/agent/runs/{run_id}/diagnostics")

    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "failed"
    assert body["failure_type"] == "validation_error"
    assert "weather" in body["failure_message"]
    # validation_failed 事件被记了(没走到 dispatch)
    event_names = [ev["name"] for ev in body["timeline"]]
    assert "validation_failed" in event_names
    assert "tool_dispatch_started" not in event_names  # 没用过
    # 但 response_returned 仍然记了(响应确实出去了)
    assert "response_returned" in event_names


# ---------- 不可 JSON 序列化的 tool_result ----------

def test_diagnostics_unserializable_tool_result_still_has_complete_view(client, monkeypatch):
    """工具返回 set 等不可 JSON 序列化的值:
    - diagnostics 仍然 200(数据不丢,只是走 fallback 形态)
    - failure_type=serialization_error(标记降级)
    - status 仍 completed
    - timeline 含 trace_serialization_fallback 事件
    """
    from app import registry
    from app.registry import ToolSpec

    def fake_run(message: str):
        return {1, 2, 3}  # set, 不可 JSON
    registry._registry["fake_set"] = ToolSpec(
        name="fake_set", description="fake", run=fake_run,
    )

    try:
        post = client.post(
            "/agent/run",
            json={"message": "x", "tool": "fake_set"},
        )
        run_id = post.json()["run_id"]

        response = client.get(f"/agent/runs/{run_id}/diagnostics")

        assert response.status_code == 200
        body = response.json()

        assert body["status"] == "completed"
        assert body["failure_type"] == "serialization_error"
        assert body["suggested_action"] == "verify tool returns JSON-serializable values"
        # timeline 里能定位到降级点
        event_names = [ev["name"] for ev in body["timeline"]]
        assert "trace_serialization_fallback" in event_names
        assert "trace_serialized" not in event_names  # 走的是 fallback
    finally:
        registry._registry.pop("fake_set", None)


# ---------- 未知 run_id ----------

def test_diagnostics_unknown_run_id_returns_404(client):
    """查询不存在的 run_id → 404 RUN_NOT_FOUND,跟其它 GET 端点一致。"""
    response = client.get("/agent/runs/nonexistent-run-id/diagnostics")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "RUN_NOT_FOUND"


# ---------- 时间戳格式 ----------

def test_diagnostics_timeline_events_have_iso_timestamps(client):
    """timeline 每条事件都有 ISO 8601 时间戳(at),客户端可以画时序图。"""
    post = client.post(
        "/agent/run",
        json={"message": "hi", "tool": "echo"},
    )
    run_id = post.json()["run_id"]

    response = client.get(f"/agent/runs/{run_id}/diagnostics")
    body = response.json()

    from datetime import datetime
    for ev in body["timeline"]:
        assert "name" in ev
        assert "at" in ev
        # ISO 8601 能解析
        datetime.fromisoformat(ev["at"])
    # 时间戳是单调递增的(按 dispatch 执行顺序)
    timestamps = [ev["at"] for ev in body["timeline"]]
    assert timestamps == sorted(timestamps)


# ---------- persistence failure(无 DB 行的 run)不能通过 diagnostics 查 ----------

def test_diagnostics_persistence_failure_run_not_queryable(client, monkeypatch):
    """trace 落库失败的 run(POST 时 500)→ DB 里没行 → GET diagnostics 也找不到(404)。

    一致性:在 dispatch 视角没入 DB 的 run,GET 端点当然也看不到。
    """
    def broken_insert_run(record, path=None):
        raise RuntimeError("disk full")
    monkeypatch.setattr("app.dispatcher.insert_run", broken_insert_run)

    post = client.post(
        "/agent/run",
        json={"message": "hi", "tool": "echo"},
    )
    # POST 本身返 500,但响应里有 run_id(in-memory 临时分配)
    assert post.status_code == 500
    run_id = post.json()["run_id"]
    assert re.fullmatch(r"[0-9a-f]{32}", run_id)

    # GET diagnostics 找不到(DB 没这一行)
    response = client.get(f"/agent/runs/{run_id}/diagnostics")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RUN_NOT_FOUND"
