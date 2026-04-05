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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cluster_sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at    TEXT NOT NULL,
            log_dir       TEXT,
            eps           REAL,
            min_samples   INTEGER,
            max_features  INTEGER,
            n_logs        INTEGER,
            n_clusters    INTEGER,
            n_noise       INTEGER,
            coverage_rate REAL,
            status        TEXT DEFAULT 'in_progress'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cluster_reviews (
            session_id          INTEGER NOT NULL,
            cluster_id          INTEGER NOT NULL,
            count               INTEGER,
            matched_definition  TEXT,
            match_ratio         REAL,
            closest_definition  TEXT,
            closest_similarity  REAL,
            representative      TEXT,
            representative_path TEXT,
            all_paths           TEXT,
            confirmed_as        TEXT,
            notes               TEXT,
            reviewed_at         TEXT,
            PRIMARY KEY (session_id, cluster_id),
            FOREIGN KEY (session_id) REFERENCES cluster_sessions(id)
        )
    """)
    conn.commit()
    return conn


# ── Cluster Sessions ──────────────────────────────────────────────────────────

def create_cluster_session(
    conn: sqlite3.Connection,
    log_dir: str,
    eps: float,
    min_samples: int,
    max_features: int,
    n_logs: int,
    n_clusters: int,
    n_noise: int,
    coverage_rate: float,
) -> int:
    cur = conn.execute("""
        INSERT INTO cluster_sessions
            (created_at, log_dir, eps, min_samples, max_features,
             n_logs, n_clusters, n_noise, coverage_rate, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'in_progress')
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        log_dir, eps, min_samples, max_features,
        n_logs, n_clusters, n_noise, round(coverage_rate, 4),
    ))
    conn.commit()
    return cur.lastrowid


def save_cluster_reviews(conn: sqlite3.Connection, session_id: int, summaries: list[dict]) -> None:
    for s in summaries:
        conn.execute("""
            INSERT OR REPLACE INTO cluster_reviews
                (session_id, cluster_id, count, matched_definition, match_ratio,
                 closest_definition, closest_similarity,
                 representative, representative_path, all_paths,
                 confirmed_as, notes, reviewed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
        """, (
            session_id,
            s["cluster_id"],
            s["count"],
            s.get("matched_definition"),
            s.get("match_ratio", 0.0),
            s.get("closest_definition"),
            s.get("closest_similarity", 0.0),
            s.get("representative", "")[:2000],
            s.get("representative_path", ""),
            json.dumps(s.get("paths", [])),
        ))
    conn.commit()


def load_cluster_sessions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM cluster_sessions ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def load_cluster_reviews(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM cluster_reviews WHERE session_id=? ORDER BY cluster_id",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def save_review_decision(
    conn: sqlite3.Connection,
    session_id: int,
    cluster_id: int,
    confirmed_as: str | None,
    notes: str,
) -> None:
    conn.execute("""
        UPDATE cluster_reviews
        SET confirmed_as=?, notes=?, reviewed_at=?
        WHERE session_id=? AND cluster_id=?
    """, (
        confirmed_as,
        notes,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        session_id,
        cluster_id,
    ))
    conn.commit()


def update_session_status(conn: sqlite3.Connection, session_id: int, status: str) -> None:
    conn.execute(
        "UPDATE cluster_sessions SET status=? WHERE id=?", (status, session_id)
    )
    conn.commit()


def delete_cluster_session(conn: sqlite3.Connection, session_id: int) -> None:
    conn.execute("DELETE FROM cluster_reviews WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM cluster_sessions WHERE id=?", (session_id,))
    conn.commit()


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
