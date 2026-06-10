from app.trace_store import get_trace


def test_get_success_trace(client):
    post_response = client.post(
        "/agent/run",
        json={"message": "12 * 8", "tool": "calculator"},
    )
    trace_id = post_response.json()["trace_id"]

    response = client.get(f"/agent/traces/{trace_id}")

    assert response.status_code == 200
    body = response.json()

    assert body["trace_id"] == trace_id
    assert body["tool"] == "calculator"
    assert body["input"] == "12 * 8"
    assert body["result"] == 96
    assert body["error"] is None
    assert body["latency_ms"] >= 0
    assert "created_at" in body


def test_get_error_trace(client):
    post_response = client.post(
        "/agent/run",
        json={"message": "1/0", "tool": "calculator"},
    )
    trace_id = post_response.json()["trace_id"]

    response = client.get(f"/agent/traces/{trace_id}")

    assert response.status_code == 200
    body = response.json()

    assert body["trace_id"] == trace_id
    assert body["tool"] == "calculator"
    assert body["input"] == "1/0"
    assert body["result"] is None
    assert body["error"]["code"] == "TOOL_EXECUTION_ERROR"


def test_get_missing_trace_returns_404(client):
    response = client.get("/agent/traces/missing-trace-id")

    assert response.status_code == 404
    body = response.json()

    assert body["error"]["code"] == "TRACE_NOT_FOUND"