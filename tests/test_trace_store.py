import uuid

from app.trace_store import init_db, insert_trace, get_trace


def test_insert_and_get_success_trace(tmp_path):
    db_path = tmp_path / "traces.db"
    init_db(db_path)

    trace_id = uuid.uuid4().hex

    insert_trace(
        {
            "trace_id": trace_id,
            "tool": "echo",
            "input": "hi",
            "output": {"value": "hi"},
            "error_code": None,
            "error_msg": None,
            "latency_ms": 12,
        },
        db_path,
    )

    trace = get_trace(trace_id, db_path)

    assert trace is not None
    assert trace["trace_id"] == trace_id
    assert trace["tool"] == "echo"
    assert trace["input"] == "hi"
    assert trace["result"] == {"value": "hi"}
    assert trace["error"] is None
    assert trace["latency_ms"] == 12
    assert "created_at" in trace


def test_insert_and_get_error_trace(tmp_path):
    db_path = tmp_path / "traces.db"
    init_db(db_path)

    trace_id = uuid.uuid4().hex

    insert_trace(
        {
            "trace_id": trace_id,
            "tool": "weather",
            "input": "hi",
            "output": None,
            "error_code": "TOOL_NOT_FOUND",
            "error_msg": "tool 'weather' is not registered",
            "latency_ms": 0,
        },
        db_path,
    )

    trace = get_trace(trace_id, db_path)

    assert trace is not None
    assert trace["result"] is None
    assert trace["error"] == {
        "code": "TOOL_NOT_FOUND",
        "message": "tool 'weather' is not registered",
    }


def test_get_missing_trace_returns_none(tmp_path):
    db_path = tmp_path / "traces.db"
    init_db(db_path)

    assert get_trace("missing", db_path) is None


def test_trace_persists_across_init_calls(tmp_path):
    db_path = tmp_path / "traces.db"
    init_db(db_path)

    trace_id = uuid.uuid4().hex

    insert_trace(
        {
            "trace_id": trace_id,
            "tool": "calculator",
            "input": "12 * 8",
            "output": 96,
            "error_code": None,
            "error_msg": None,
            "latency_ms": 3,
        },
        db_path,
    )

    init_db(db_path)

    trace = get_trace(trace_id, db_path)

    assert trace is not None
    assert trace["result"] == 96