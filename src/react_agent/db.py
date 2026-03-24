"""SQLite persistence for batch processing results."""

import json
import sqlite3
from datetime import datetime

DB_PATH = "batch_results.db"


def init_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Create DB and table if not exists, return connection."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS results (
            file_path    TEXT PRIMARY KEY,
            month        TEXT,
            error_id     TEXT,
            category     TEXT,
            confidence   REAL,
            result_json  TEXT,
            status       TEXT,
            error_msg    TEXT,
            processed_at TEXT
        )
    """)
    conn.commit()
    return conn


def is_processed(conn: sqlite3.Connection, file_path: str) -> bool:
    """Return True if this file was already successfully processed."""
    row = conn.execute(
        "SELECT 1 FROM results WHERE file_path = ? AND status = 'success'",
        (file_path,),
    ).fetchone()
    return row is not None


def save_result(
    conn: sqlite3.Connection, file_path: str, month: str, result: dict
) -> None:
    """Persist a successful analysis result."""
    conn.execute(
        """
        INSERT OR REPLACE INTO results
            (file_path, month, error_id, category, confidence, result_json, status, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, 'success', ?)
        """,
        (
            file_path,
            month,
            result.get("error_id"),
            result.get("category"),
            result.get("confidence"),
            json.dumps(result, ensure_ascii=False),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()


def save_error(
    conn: sqlite3.Connection, file_path: str, month: str, error_msg: str
) -> None:
    """Persist a failed processing attempt."""
    conn.execute(
        """
        INSERT OR REPLACE INTO results
            (file_path, month, status, error_msg, processed_at)
        VALUES (?, ?, 'failed', ?, ?)
        """,
        (file_path, month, error_msg, datetime.now().isoformat()),
    )
    conn.commit()


def summary(conn: sqlite3.Connection) -> None:
    """Print a quick summary of current DB contents."""
    rows = conn.execute("""
        SELECT month, status, COUNT(*) as cnt
        FROM results
        GROUP BY month, status
        ORDER BY month, status
    """).fetchall()

    print("\n=== DB Summary ===")
    for month, status, cnt in rows:
        print(f"  {month or '??'}  {status:8s}  {cnt}")
    print("==================\n")
