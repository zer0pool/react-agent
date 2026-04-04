"""UI history and batch results DB access. No Streamlit imports."""

import json
import sqlite3
from datetime import datetime

DB_PATH = "batch_results.db"


def get_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ui_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            analyzed_at  TEXT NOT NULL,
            model        TEXT,
            log_snippet  TEXT,
            raw_log      TEXT,
            error_id     TEXT,
            category     TEXT,
            severity     TEXT,
            confidence   REAL,
            result_json  TEXT
        )
    """)
    conn.commit()
    return conn


# ── UI History ───────────────────────────────────────────────────────────────

def save_ui_history(conn: sqlite3.Connection, model: str, raw_log: str, data: dict) -> None:
    conn.execute("""
        INSERT INTO ui_history
            (analyzed_at, model, log_snippet, raw_log, error_id, category, severity, confidence, result_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        model,
        raw_log[:120] + ("..." if len(raw_log) > 120 else ""),
        raw_log,
        data.get("error_id"),
        data.get("category"),
        data.get("severity"),
        data.get("confidence") or data.get("confidence_score"),
        json.dumps(data, ensure_ascii=False),
    ))
    conn.commit()


def load_ui_history(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM ui_history ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def delete_ui_history(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM ui_history")
    conn.commit()


# ── Batch Results ─────────────────────────────────────────────────────────────

def load_batch_results(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT * FROM results ORDER BY processed_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def update_batch_result(
    conn: sqlite3.Connection,
    file_path: str,
    error_id: str,
    category: str,
    confidence: float | None,
    result_json: str,
) -> None:
    conn.execute(
        "UPDATE results SET error_id=?, category=?, confidence=?, result_json=? WHERE file_path=?",
        (error_id, category, confidence, result_json, file_path),
    )
    conn.commit()


def delete_batch_result(conn: sqlite3.Connection, file_path: str) -> None:
    conn.execute("DELETE FROM results WHERE file_path=?", (file_path,))
    conn.commit()
