import json
import os
import sqlite3
from pathlib import Path
from typing import Any

_db_path = Path(os.getenv("AGENT_RUN_DB_PATH", "agent_runs.db"))


def _resolve_path(path: str | Path | None = None) -> Path:
    if path is None:
        return _db_path
    return Path(path)


_UNSERIALIZABLE_REPR_LIMIT = 500


def safe_serialize_tool_result(value: Any) -> str:
    """把 tool_result / tool_args 转成可 JSON 序列化的字符串。

    正常值走 json.dumps 透传;不可序列化的值退化成结构化标记:
    {"_unserializable": True, "type": "<类名>", "repr": "<截断到 500 字符>"}。
    绝不抛异常,保证 trace 持久化路径不会因为单个字段而整条丢失。
    """
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return json.dumps(
            {
                "_unserializable": True,
                "type": type(value).__name__,
                "repr": repr(value)[:_UNSERIALIZABLE_REPR_LIMIT],
            },
            ensure_ascii=False,
        )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """把 sqlite Row 还原成跟 AgentRun 字段一一对应的 dict。

    集中处理 tool_args / tool_result 的 JSON 反序列化和 error 嵌套对象组装,
    get_run 和 list_runs 共用,避免两处漂移。
    """
    tool_args = json.loads(row["tool_args"]) if row["tool_args"] is not None else {}

    tool_result: Any = None
    if row["tool_result"] is not None:
        tool_result = json.loads(row["tool_result"])

    error: dict[str, str] | None = None
    if row["error_code"] is not None:
        error = {
            "code": row["error_code"],
            "message": row["error_message"],
        }

    return {
        "run_id": row["run_id"],
        "input": row["input"],
        "selected_tool": row["selected_tool"],
        "tool_args": tool_args,
        "tool_result": tool_result,
        "status": row["status"],
        "error": error,
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def init_db(path: str | Path | None = None) -> None:
    db_path = _resolve_path(path)

    with sqlite3.connect(db_path, check_same_thread=False) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                run_id         TEXT PRIMARY KEY,
                input          TEXT NOT NULL,
                selected_tool  TEXT NOT NULL,
                tool_args      TEXT NOT NULL,
                tool_result    TEXT,
                status         TEXT NOT NULL,
                error_code     TEXT,
                error_message  TEXT,
                started_at     TEXT NOT NULL,
                finished_at    TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_runs_started_at
            ON agent_runs(started_at DESC)
            """
        )


def insert_run(record: dict[str, Any], path: str | Path | None = None) -> None:
    db_path = _resolve_path(path)

    tool_args_json = safe_serialize_tool_result(record.get("tool_args", {}))

    tool_result = record.get("tool_result")
    tool_result_json: str | None = None
    if tool_result is not None:
        tool_result_json = safe_serialize_tool_result(tool_result)

    with sqlite3.connect(db_path, check_same_thread=False) as conn:
        conn.execute(
            """
            INSERT INTO agent_runs (
                run_id,
                input,
                selected_tool,
                tool_args,
                tool_result,
                status,
                error_code,
                error_message,
                started_at,
                finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["run_id"],
                record["input"],
                record["selected_tool"],
                tool_args_json,
                tool_result_json,
                record["status"],
                record.get("error_code"),
                record.get("error_message"),
                record["started_at"],
                record["finished_at"],
            ),
        )


def get_run(run_id: str, path: str | Path | None = None) -> dict[str, Any] | None:
    db_path = _resolve_path(path)

    with sqlite3.connect(db_path, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                run_id,
                input,
                selected_tool,
                tool_args,
                tool_result,
                status,
                error_code,
                error_message,
                started_at,
                finished_at
            FROM agent_runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()

    if row is None:
        return None

    return _row_to_dict(row)


def list_runs(
    *,
    limit: int = 50,
    offset: int = 0,
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    db_path = _resolve_path(path)

    with sqlite3.connect(db_path, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                run_id,
                input,
                selected_tool,
                tool_args,
                tool_result,
                status,
                error_code,
                error_message,
                started_at,
                finished_at
            FROM agent_runs
            ORDER BY started_at DESC, run_id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

    return [_row_to_dict(row) for row in rows]
