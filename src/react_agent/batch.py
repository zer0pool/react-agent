"""Core batch processing logic for Airflow error log analysis."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from langchain_core.messages import HumanMessage

from react_agent.context import Context
from react_agent.db import init_db, is_processed, save_error, save_result, summary
from react_agent.graph import graph

logger = logging.getLogger(__name__)


def _parse_result_json(content: str) -> dict:
    """Extract JSON dict from LLM response content."""
    if "```json" in content:
        content = content.split("```json")[-1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]
    return json.loads(content.strip())


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
            HumanMessage(
                content=(
                    "Analyze this Airflow log and find the root cause. "
                    f"Please provide a detailed JSON report: {raw_log}"
                )
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


async def run_batch(
    log_dir: str = "error_logs",
    months: Optional[list[str]] = None,
    # model options:
    #   VertexAI (default) : "google_vertexai/gemini-2.0-flash-001"
    #   Local Ollama       : "ollama/qwen2.5-coder:7b"
    model: str = "google_vertexai/gemini-2.0-flash-001",
    db_path: str = "batch_results.db",
) -> None:
    """Scan log files and run the agent sequentially (one at a time)."""
    conn = init_db(db_path)
    base = Path(log_dir)

    # Collect month directories
    if months:
        month_dirs = [base / m for m in months]
    else:
        month_dirs = sorted(d for d in base.iterdir() if d.is_dir())

    # Collect all candidate files
    all_files: list[tuple[Path, str]] = []
    for month_dir in month_dirs:
        if not month_dir.is_dir():
            logger.warning("Directory not found: %s", month_dir)
            continue
        for f in sorted(month_dir.glob("*.log")):
            all_files.append((f, month_dir.name))

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
