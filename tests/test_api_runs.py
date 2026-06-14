import re
from datetime import datetime


# 10 字段契约:所有响应都包含这 10 个 key
ALL_AGENT_RUN_KEYS = (
    "run_id", "input", "selected_tool", "tool_args",
    "tool_result", "status", "error", "warnings",
    "started_at", "finished_at",
)


# ---------- POST /agent/run ----------


def test_post_calculator_success(client):
    response = client.post(
        "/agent/run",
        json={"message": "12 * 8", "tool": "calculator"},
    )

    assert response.status_code == 200
    body = response.json()

    for key in ALL_AGENT_RUN_KEYS:
        assert key in body, f"missing key: {key}"

    assert re.fullmatch(r"[0-9a-f]{32}", body["run_id"])
    assert body["status"] == "completed"
    assert body["tool_result"] == 96
    assert body["selected_tool"] == "calculator"
    assert body["error"] is None
    assert body["warnings"] == []
    # 时间戳能解析,且 started < finished
    started = datetime.fromisoformat(body["started_at"])
    finished = datetime.fromisoformat(body["finished_at"])
    assert started < finished


def test_post_echo_success(client):
    response = client.post(
        "/agent/run",
        json={"message": "hi", "tool": "echo"},
    )

    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "completed"
    assert body["tool_result"] == "hi"
    assert body["selected_tool"] == "echo"
    assert body["warnings"] == []


def test_post_tool_not_found(client):
    """tool 不存在 → 404,status=failed,error.code=TOOL_NOT_FOUND,但 run_id 仍返回(失败也落库)。"""
    response = client.post(
        "/agent/run",
        json={"message": "hi", "tool": "weather"},
    )

    assert response.status_code == 404
    body = response.json()

    for key in ALL_AGENT_RUN_KEYS:
        assert key in body, f"missing key: {key}"

    assert re.fullmatch(r"[0-9a-f]{32}", body["run_id"])
    assert body["status"] == "failed"
    assert body["error"]["code"] == "TOOL_NOT_FOUND"
    assert body["selected_tool"] == "weather"


def test_post_calculator_division_by_zero_is_tool_input_error(client):
    """calculator "1/0" → 400 TOOL_INPUT_ERROR(用户责任,改请求重发即可)。

    跟工具内部崩区分:用户给 0 作除数是用户错,HTTP 400,不需要告警。
    """
    response = client.post(
        "/agent/run",
        json={"message": "1/0", "tool": "calculator"},
    )

    assert response.status_code == 400
    body = response.json()

    for key in ALL_AGENT_RUN_KEYS:
        assert key in body, f"missing key: {key}"

    assert re.fullmatch(r"[0-9a-f]{32}", body["run_id"])
    assert body["status"] == "failed"
    assert body["error"]["code"] == "TOOL_INPUT_ERROR"
    assert body["selected_tool"] == "calculator"


def test_post_calculator_invalid_expression_is_tool_input_error(client):
    """非法表达式(用户给乱码)→ 400 TOOL_INPUT_ERROR。"""
    response = client.post(
        "/agent/run",
        json={"message": "1 + a", "tool": "calculator"},
    )

    assert response.status_code == 400
    body = response.json()

    assert body["status"] == "failed"
    assert body["error"]["code"] == "TOOL_INPUT_ERROR"


def test_post_validation_error_no_message(client):
    """缺 message → 422,INVALID_REQUEST,**不**生成 run。"""
    response = client.post(
        "/agent/run",
        json={"tool": "echo"},
    )

    assert response.status_code == 422
    body = response.json()

    assert body["error"]["code"] == "INVALID_REQUEST"
    assert "run_id" not in body


# ---------- GET /agent/runs/{run_id} ----------


def test_get_run_by_id_success(client):
    """POST 一次成功,拿 run_id,GET 拿回完整 trace(10 字段)。"""
    post = client.post(
        "/agent/run",
        json={"message": "12 * 8", "tool": "calculator"},
    )
    run_id = post.json()["run_id"]

    response = client.get(f"/agent/runs/{run_id}")

    assert response.status_code == 200
    body = response.json()

    # 10 字段都在
    for key in ALL_AGENT_RUN_KEYS:
        assert key in body, f"missing key: {key}"

    assert body["run_id"] == run_id
    assert body["input"] == "12 * 8"
    assert body["selected_tool"] == "calculator"
    assert body["tool_args"] == {"message": "12 * 8"}
    assert body["tool_result"] == 96
    assert body["status"] == "completed"
    assert body["error"] is None
    assert body["warnings"] == []


def test_get_run_by_id_not_found(client):
    response = client.get("/agent/runs/nonexistent-run-id")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "RUN_NOT_FOUND"


# ---------- GET /agent/runs(列表) ----------


def test_list_runs_returns_all_sorted_by_started_at_desc(client):
    """POST 3 次,GET /agent/runs 拿回 3 条,按 started_at DESC 排。"""
    for label in ["a", "b", "c"]:
        client.post(
            "/agent/run",
            json={"message": label, "tool": "echo"},
        )

    response = client.get("/agent/runs")

    assert response.status_code == 200
    body = response.json()

    assert body["limit"] == 50
    assert body["offset"] == 0
    assert len(body["runs"]) == 3
    # 倒序:c 最新,b,a 最老
    assert [r["input"] for r in body["runs"]] == ["c", "b", "a"]
    # list items 也带 warnings 字段
    for r in body["runs"]:
        assert r["warnings"] == []


def test_list_runs_pagination(client):
    """limit + offset 翻页。"""
    for label in ["a", "b", "c"]:
        client.post(
            "/agent/run",
            json={"message": label, "tool": "echo"},
        )

    page1 = client.get("/agent/runs?limit=2&offset=0").json()
    page2 = client.get("/agent/runs?limit=2&offset=2").json()

    assert len(page1["runs"]) == 2
    assert len(page2["runs"]) == 1
    # 没有重叠
    page1_ids = {r["run_id"] for r in page1["runs"]}
    page2_ids = {r["run_id"] for r in page2["runs"]}
    assert page1_ids.isdisjoint(page2_ids)
    # page1 是最新的 2 条
    assert {r["input"] for r in page1["runs"]} == {"c", "b"}
    # page2 是最老的 1 条
    assert page2["runs"][0]["input"] == "a"


def test_list_runs_default_limit_applied(client):
    """不传 limit → 用默认 50(测试只插 1 条,所以 runs 长度 1,limit 字段是 50)。"""
    client.post(
        "/agent/run",
        json={"message": "hi", "tool": "echo"},
    )

    body = client.get("/agent/runs").json()

    assert body["limit"] == 50
    assert body["offset"] == 0
    assert len(body["runs"]) == 1


def test_list_runs_limit_out_of_range_rejected(client):
    """limit > 200 → 422,FastAPI 的 Query 约束自动挡。"""
    response = client.get("/agent/runs?limit=201")

    assert response.status_code == 422


# ---------- 旧端点已删除 ----------


def test_legacy_traces_endpoint_404(client):
    """GET /agent/traces/xxx → 404,确认旧路由已删。"""
    response = client.get("/agent/traces/any-id")

    # 路由不存在,FastAPI 默认 404
    assert response.status_code == 404


# ---------- trace 持久化失败 → 500 TRACE_PERSIST_FAILED ----------


def test_run_endpoint_returns_500_when_trace_persist_fails(client, monkeypatch):
    """dispatcher.insert_run 抛异常 → POST /agent/run 返 500,
    响应体仍 10 字段,error.code = TRACE_PERSIST_FAILED,
    业务结果(completed + tool_result)被覆盖成 None(因为 trace 没拿到)。
    """
    def broken_insert_run(record, path=None):
        raise RuntimeError("disk full: simulated")
    monkeypatch.setattr("app.dispatcher.insert_run", broken_insert_run)

    response = client.post(
        "/agent/run",
        json={"message": "hi", "tool": "echo"},
    )

    assert response.status_code == 500
    body = response.json()

    # 10 字段都在(契约不破)
    for key in ALL_AGENT_RUN_KEYS:
        assert key in body, f"missing key: {key}"

    # 业务执行成功了,所以 status=trace_persist_failed(盖在 completed 上);trace 没记下来
    assert body["status"] == "trace_persist_failed"
    # error.code 显式带出失败原因
    assert body["error"]["code"] == "TRACE_PERSIST_FAILED"
    assert "disk full" in body["error"]["message"]
    # tool_result 强制 None(trace 没记,客户端拿到也不能信)
    assert body["tool_result"] is None
    # run_id 仍然返回,主调用方可以拿这个 ID 去后台排查
    assert re.fullmatch(r"[0-9a-f]{32}", body["run_id"])
