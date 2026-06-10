from datetime import datetime
from pathlib import Path

from app.agent_run_store import get_run, init_db, insert_run, list_runs


def _base_record(**overrides) -> dict:
    """默认构造一条 success run 的 record;用例通过 overrides 改字段。"""
    record = {
        "run_id": "a" * 32,
        "input": "12 * 8",
        "selected_tool": "calculator",
        "tool_args": {"message": "12 * 8"},
        "tool_result": 96,
        "status": "success",
        "error_code": None,
        "error_message": None,
        "started_at": "2026-06-10T08:00:00+00:00",
        "finished_at": "2026-06-10T08:00:00.005+00:00",
    }
    record.update(overrides)
    return record


def test_insert_and_get_success_run(tmp_path):
    db = tmp_path / "runs.db"
    init_db(db)
    record = _base_record()
    insert_run(record, db)

    loaded = get_run(record["run_id"], db)

    assert loaded is not None
    assert loaded["run_id"] == record["run_id"]
    assert loaded["input"] == "12 * 8"
    assert loaded["selected_tool"] == "calculator"
    assert loaded["tool_result"] == 96
    assert loaded["status"] == "success"
    assert loaded["error"] is None


def test_insert_and_get_tool_error_run(tmp_path):
    db = tmp_path / "runs.db"
    init_db(db)
    record = _base_record(
        run_id="b" * 32,
        input="1/0",
        tool_args={"message": "1/0"},
        tool_result=None,
        status="tool_error",
        error_code="TOOL_EXECUTION_ERROR",
        error_message="division by zero",
    )
    insert_run(record, db)

    loaded = get_run(record["run_id"], db)

    assert loaded is not None
    assert loaded["tool_result"] is None
    assert loaded["status"] == "tool_error"
    assert loaded["error"] is not None
    assert loaded["error"]["code"] == "TOOL_EXECUTION_ERROR"
    assert loaded["error"]["message"] == "division by zero"


def test_insert_and_get_tool_not_found_run(tmp_path):
    db = tmp_path / "runs.db"
    init_db(db)
    record = _base_record(
        run_id="c" * 32,
        input="hi",
        selected_tool="weather",
        tool_args={"message": "hi"},
        tool_result=None,
        status="tool_not_found",
        error_code="TOOL_NOT_FOUND",
        error_message="tool 'weather' is not registered",
    )
    insert_run(record, db)

    loaded = get_run(record["run_id"], db)

    assert loaded is not None
    assert loaded["status"] == "tool_not_found"
    assert loaded["error"]["code"] == "TOOL_NOT_FOUND"


def test_tool_args_round_trip_is_decoded_dict(tmp_path):
    """关键:tool_args 入参是 dict,读出来还是 dict(已被 json.loads)。"""
    db = tmp_path / "runs.db"
    init_db(db)
    record = _base_record(tool_args={"message": "12 * 8", "extra": "x"})
    insert_run(record, db)

    loaded = get_run(record["run_id"], db)

    assert isinstance(loaded["tool_args"], dict)
    assert loaded["tool_args"] == {"message": "12 * 8", "extra": "x"}


def test_timestamps_are_iso8601_parseable(tmp_path):
    """started_at / finished_at 读出后能被 datetime.fromisoformat 解析。"""
    db = tmp_path / "runs.db"
    init_db(db)
    record = _base_record()
    insert_run(record, db)

    loaded = get_run(record["run_id"], db)

    started = datetime.fromisoformat(loaded["started_at"])
    finished = datetime.fromisoformat(loaded["finished_at"])
    assert started < finished


def test_get_missing_run_returns_none(tmp_path):
    db = tmp_path / "runs.db"
    init_db(db)

    loaded = get_run("nonexistent", db)

    assert loaded is None


def test_persistence_across_reopen(tmp_path):
    """跨调用持久化:写一条 → 不重新 init → 还能查到(db 文件已在磁盘)。"""
    db = tmp_path / "runs.db"
    init_db(db)
    record = _base_record()
    insert_run(record, db)

    # 不再调 init_db,模拟"进程重启后直接读 db"
    loaded = get_run(record["run_id"], db)

    assert loaded is not None
    assert loaded["run_id"] == record["run_id"]


def test_list_runs_returns_all_sorted_by_started_at_desc(tmp_path):
    db = tmp_path / "runs.db"
    init_db(db)
    for run_id, ts, label in [
        ("a" * 32, "2026-06-10T08:00:00+00:00", "a"),
        ("b" * 32, "2026-06-10T08:00:01+00:00", "b"),
        ("c" * 32, "2026-06-10T08:00:02+00:00", "c"),
    ]:
        insert_run(_base_record(
            run_id=run_id,
            input=label,
            tool_args={"message": label},
            tool_result=label,
            started_at=ts,
            finished_at=ts,
        ), db)

    runs = list_runs(path=db)

    assert len(runs) == 3
    assert [r["input"] for r in runs] == ["c", "b", "a"]


def test_list_runs_pagination(tmp_path):
    db = tmp_path / "runs.db"
    init_db(db)
    for i, ts in enumerate([
        "2026-06-10T08:00:00+00:00",
        "2026-06-10T08:00:01+00:00",
        "2026-06-10T08:00:02+00:00",
    ]):
        insert_run(_base_record(
            run_id=str(i).zfill(32),
            input=str(i),
            tool_args={"message": str(i)},
            tool_result=str(i),
            started_at=ts,
            finished_at=ts,
        ), db)

    page1 = list_runs(path=db, limit=2, offset=0)
    page2 = list_runs(path=db, limit=2, offset=2)

    assert len(page1) == 2
    assert len(page2) == 1
    # 没有任何重叠
    assert {r["run_id"] for r in page1}.isdisjoint({r["run_id"] for r in page2})
    # page1 是最新的 2 条
    assert {r["input"] for r in page1} == {"2", "1"}
    # page2 是最老的 1 条
    assert page2[0]["input"] == "0"


def test_list_runs_empty(tmp_path):
    db = tmp_path / "runs.db"
    init_db(db)

    runs = list_runs(path=db)

    assert runs == []
