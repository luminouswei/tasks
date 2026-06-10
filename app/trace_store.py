import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_db_path = Path(os.getenv("TRACE_DB_PATH", "traces.db"))


def _resolve_db_path(db_path: str | Path | None = None) -> Path:
    if db_path is None:
        return _db_path
    return Path(db_path)


def init_db(path: str | Path | None = None) -> None:
    db_path = _resolve_db_path(path)

    with sqlite3.connect(db_path, check_same_thread=False) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS traces (
                trace_id TEXT PRIMARY KEY,
                tool TEXT NOT NULL,
                input TEXT NOT NULL,
                output TEXT,
                error_code TEXT,
                error_msg TEXT,
                latency_ms INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_traces_created_at
            ON traces(created_at)
            """
        )


def insert_trace(record: dict[str, Any], db_path: str | Path | None = None) -> None:
    path = _resolve_db_path(db_path)

    output = record.get("output")
    output_json = None

    if output is not None:
        try:
            output_json = json.dumps(output, ensure_ascii=False)
        except TypeError:
            output_json = None

    created_at = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(path, check_same_thread=False) as conn:
        conn.execute(
            """
            INSERT INTO traces (
                trace_id,
                tool,
                input,
                output,
                error_code,
                error_msg,
                latency_ms,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["trace_id"],
                record["tool"],
                record["input"],
                output_json,
                record.get("error_code"),
                record.get("error_msg"),
                record["latency_ms"],
                created_at,
            ),
        )


def get_trace(trace_id: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
    path = _resolve_db_path(db_path)

    with sqlite3.connect(path, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                trace_id,
                tool,
                input,
                output,
                error_code,
                error_msg,
                latency_ms,
                created_at
            FROM traces
            WHERE trace_id = ?
            """,
            (trace_id,),
        ).fetchone()

    if row is None:
        return None

    result = None
    if row["output"] is not None:
        result = json.loads(row["output"])

    error = None
    if row["error_code"] is not None:
        error = {
            "code": row["error_code"],
            "message": row["error_msg"],
        }

    return {
        "trace_id": row["trace_id"],
        "tool": row["tool"],
        "input": row["input"],
        "result": result,
        "error": error,
        "latency_ms": row["latency_ms"],
        "created_at": row["created_at"],
    }