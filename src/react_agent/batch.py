"""Core batch processing logic for Airflow error log analysis."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional


from react_agent.context import Context
from react_agent.db import init_db, is_processed, save_error, save_result, summary
from react_agent.graph import graph
from react_agent.utils import extract_json_from_markdown

logger = logging.getLogger(__name__)


def _parse_result_json(content: str) -> dict:
    """Extract JSON dict from LLM response content. Returns raw content dict on parse failure."""
    return extract_json_from_markdown(content)


async def _process_one(
    file_path: Path,
    month: str,
    model: str,
    conn,
) -> str:
    """Process a single log file. Returns 'success' or 'failed'."""
    raw_log = file_path.read_text(encoding="utf-8", errors="ignore")

    inputs = {
        "messages": [
            (
                "user",
                f"Analyze this Airflow log and find the root cause. Please provide a detailed JSON report: {raw_log}",
            )
        ],
        "raw_log": raw_log,
    }

    try:
        result_state = await graph.ainvoke(
            inputs,
            context=Context(model=model),
        )
        last_msg = result_state["messages"][-1]
        result = _parse_result_json(last_msg.content)
        save_result(conn, str(file_path), month, result)
        logger.debug("OK  %s", file_path.name)
        return "success"

    except Exception as exc:
        save_error(conn, str(file_path), month, str(exc))
        logger.warning("FAIL %s — %s", file_path.name, exc)
        return "failed"


def _collect_month_dirs(base: Path, year: Optional[str], months: Optional[list[str]]) -> list[tuple[Path, str]]:
    """Return list of (month_dir, label) under base.

    Supports two layouts:
      - New : error_logs/YYYY/MM/   → label "YYYY-MM"
      - Old : error_logs/MM/        → label "MM"

    Filtering:
      months = ["2025/01", "2025/03"]  → specific year/month pairs
      year   = "2025"                  → all months under that year
      both None                        → everything
    """
    result: list[tuple[Path, str]] = []

    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue

        # New layout: entry is a year directory (4-digit)
        if entry.name.isdigit() and len(entry.name) == 4:
            yyyy = entry.name
            if year and yyyy != year:
                continue
            for month_entry in sorted(entry.iterdir()):
                if not month_entry.is_dir():
                    continue
                label = f"{yyyy}-{month_entry.name}"
                if months and f"{yyyy}/{month_entry.name}" not in months:
                    continue
                result.append((month_entry, label))

        # Old layout: entry is a month directory (2-digit)
        elif entry.name.isdigit() and len(entry.name) == 2:
            if months and entry.name not in months:
                continue
            result.append((entry, entry.name))

    return result


async def run_batch(
    log_dir: str = "error_logs",
    months: Optional[list[str]] = None,
    year: Optional[str] = None,
    # model options:
    #   Local Ollama (default)     : "ollama/qwen2.5-coder:7b"
    #   Google AI Studio           : "google_genai/gemini-2.5-flash"
    model: str = "ollama/qwen2.5-coder:7b",
    db_path: str = "batch_results.db",
) -> None:
    """Scan log files and run the agent sequentially (one at a time)."""
    conn = init_db(db_path)
    base = Path(log_dir)

    month_dirs = _collect_month_dirs(base, year, months)

    # Collect all candidate files
    all_files: list[tuple[Path, str]] = []
    for month_dir, label in month_dirs:
        if not month_dir.is_dir():
            logger.warning("Directory not found: %s", month_dir)
            continue
        for f in sorted(month_dir.glob("*.log")):
            all_files.append((f, label))

    # Filter already processed
    pending = [(f, m) for f, m in all_files if not is_processed(conn, str(f))]

    print(f"Total files : {len(all_files)}")
    print(f"Already done: {len(all_files) - len(pending)}")
    print(f"Pending     : {len(pending)}")
    print(f"Model       : {model}\n")

    if not pending:
        print("Nothing to process.")
        summary(conn)
        conn.close()
        return

    success = 0
    failed = 0
    for i, (f, m) in enumerate(pending, 1):
        print(f"[{i}/{len(pending)}] {f.name}", end=" ... ", flush=True)
        status = await _process_one(f, m, model, conn)
        print(status)
        if status == "success":
            success += 1
        else:
            failed += 1

    print(f"\nFinished — success: {success}, failed: {failed}")
    summary(conn)
    conn.close()
