def test_run_missing_message_returns_422(client):
    response = client.post(
        "/agent/run",
        json={"tool": "echo"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "INVALID_REQUEST"


def test_validation_error_response_has_no_trace_id(client):
    """契约锁定:422 错误不生成 trace,响应也不带 trace_id 字段。"""
    response = client.post(
        "/agent/run",
        json={"tool": "echo"},
    )

    assert response.status_code == 422
    body = response.json()
    assert "trace_id" not in body


def test_run_calculator_success(client):
    response = client.post(
        "/agent/run",
        json={"message": "12 * 8", "tool": "calculator"},
    )

    assert response.status_code == 200
    body = response.json()

    assert body["tool"] == "calculator"
    assert body["result"] == 96
    assert "trace_id" in body
    assert body["latency_ms"] >= 0


def test_run_echo_success(client):
    response = client.post(
        "/agent/run",
        json={"message": "hi", "tool": "echo"},
    )

    assert response.status_code == 200
    body = response.json()

    assert body["tool"] == "echo"
    assert body["result"] == "hi"
    assert "trace_id" in body


def test_run_tool_not_found(client):
    response = client.post(
        "/agent/run",
        json={"message": "hi", "tool": "weather"},
    )

    assert response.status_code == 404
    body = response.json()

    assert body["error"]["code"] == "TOOL_NOT_FOUND"
    assert "trace_id" in body


def test_run_calculator_division_by_zero(client):
    response = client.post(
        "/agent/run",
        json={"message": "1/0", "tool": "calculator"},
    )

    assert response.status_code == 400
    body = response.json()

    assert body["error"]["code"] == "TOOL_EXECUTION_ERROR"
    assert "trace_id" in body