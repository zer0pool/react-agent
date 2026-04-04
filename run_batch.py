"""CLI entry point for batch processing Airflow error logs."""

import argparse
import asyncio
import logging

import os

from dotenv import load_dotenv

load_dotenv()

# langchain-google-genai reads GOOGLE_API_KEY
if not os.environ.get("GOOGLE_API_KEY") and os.environ.get("GEMINI_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Batch-analyze Airflow error logs with the ReAct agent."
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--month", metavar="MM", help="Process a single month (e.g. 03)")
    group.add_argument("--all", action="store_true", help="Process all months")

    # Model options:
    #   Local Ollama (default)     : "ollama/qwen2.5-coder:7b"
    #   Google AI Studio           : "google_genai/gemini-2.5-flash"
    p.add_argument(
        "--model", default="ollama/qwen2.5-coder:7b",
        help="LLM to use (default: ollama/qwen2.5-coder:7b)",
    )
    p.add_argument(
        "--log-dir", default="error_logs",
        help="Root directory of error logs (default: error_logs/)",
    )
    p.add_argument(
        "--db", default="batch_results.db",
        help="SQLite DB file path (default: batch_results.db)",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Show debug logs per file",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )

    from react_agent.batch import run_batch

    months = [args.month] if args.month else None

    asyncio.run(
        run_batch(
            log_dir=args.log_dir,
            months=months,
            model=args.model,
            db_path=args.db,
        )
    )


if __name__ == "__main__":
    main()
