import re


# ---------- 1. replay 成功 run → 新 run,同样入参,新 run_id ----------


def test_replay_success_run_creates_new_run_with_same_args(client):
    post = client.post(
        "/agent/run",
        json={"message": "12 * 8", "tool": "calculator"},
    )
    original_run_id = post.json()["run_id"]

    response = client.post(f"/agent/runs/{original_run_id}/replay")

    assert response.status_code == 200
    body = response.json()

    # 新 run_id 跟原 run_id 不一样
    assert body["run_id"] != original_run_id
    assert re.fullmatch(r"[0-9a-f]{32}", body["run_id"])
    # 关键字段跟原 run 一致
    assert body["input"] == "12 * 8"
    assert body["selected_tool"] == "calculator"
    assert body["tool_args"] == {"message": "12 * 8"}
    assert body["status"] == "success"
    assert body["tool_result"] == 96


# ---------- 2. replay tool_error run → 仍然 tool_error(replay 不"修复") ----------


def test_replay_tool_error_run_still_tool_error(client):
    post = client.post(
        "/agent/run",
        json={"message": "1/0", "tool": "calculator"},
    )
    original_run_id = post.json()["run_id"]
    assert post.json()["status"] == "tool_error"

    response = client.post(f"/agent/runs/{original_run_id}/replay")

    assert response.status_code == 400
    body = response.json()

    assert body["run_id"] != original_run_id
    assert body["status"] == "tool_error"
    assert body["error"]["code"] == "TOOL_EXECUTION_ERROR"


# ---------- 3. replay tool_not_found run → 仍然 tool_not_found ----------


def test_replay_tool_not_found_run_still_tool_not_found(client):
    post = client.post(
        "/agent/run",
        json={"message": "hi", "tool": "weather"},
    )
    original_run_id = post.json()["run_id"]
    assert post.json()["status"] == "tool_not_found"

    response = client.post(f"/agent/runs/{original_run_id}/replay")

    assert response.status_code == 404
    body = response.json()

    assert body["run_id"] != original_run_id
    assert body["status"] == "tool_not_found"
    assert body["error"]["code"] == "TOOL_NOT_FOUND"


# ---------- 4. replay 不存在的 run_id → 404 RUN_NOT_FOUND ----------


def test_replay_nonexistent_run_returns_404(client):
    response = client.post("/agent/runs/nonexistent-run-id/replay")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "RUN_NOT_FOUND"


# ---------- 5. replay 不修改原 run,新 run 也能独立查到 ----------


def test_replay_persists_new_run_without_modifying_original(client):
    post = client.post(
        "/agent/run",
        json={"message": "12 * 8", "tool": "calculator"},
    )
    original_run_id = post.json()["run_id"]
    original_finished_at = post.json()["finished_at"]

    replay = client.post(f"/agent/runs/{original_run_id}/replay")
    new_run_id = replay.json()["run_id"]

    # 新 run 能查
    new_get = client.get(f"/agent/runs/{new_run_id}")
    assert new_get.status_code == 200
    assert new_get.json()["status"] == "success"

    # 原 run 状态没被改
    original_get = client.get(f"/agent/runs/{original_run_id}")
    assert original_get.status_code == 200
    assert original_get.json()["finished_at"] == original_finished_at
    assert original_get.json()["run_id"] == original_run_id

    # 列表里两条都在
    list_response = client.get("/agent/runs")
    run_ids = {r["run_id"] for r in list_response.json()["runs"]}
    assert original_run_id in run_ids
    assert new_run_id in run_ids
