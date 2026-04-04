"""LangGraph agent execution logic. No Streamlit imports."""

import asyncio
import json
from typing import Generator

from react_agent.context import Context
from react_agent.graph import graph


def _build_steps_html(msg_type: str, msg) -> str | None:
    """Convert a single graph message into an HTML step string, or None if nothing to show."""
    if hasattr(msg, "tool_calls") and msg.tool_calls:
        parts = []
        for tc in msg.tool_calls:
            parts.append(
                f'<div class="step-box step-tool">'
                f'🔧 <b>Tool call:</b> <code>{tc["name"]}</code> — '
                f'{json.dumps(tc.get("args", {}))[:120]}'
                f'</div>'
            )
        return "".join(parts)

    if hasattr(msg, "content") and msg.content:
        content = str(msg.content)
        if msg_type == "ai":
            return (
                f'<div class="step-box step-ai">'
                f'🤖 <b>Agent:</b> {content[:300]}'
                f'</div>'
            )
        if msg_type == "tool":
            return (
                f'<div class="step-box step-tool">'
                f'📥 <b>Tool result:</b> {content[:200]}'
                f'</div>'
            )
        if msg_type == "human":
            return (
                f'<div class="step-box step-human">'
                f'💬 <b>Reviewer:</b> {content[:200]}'
                f'</div>'
            )
    return None


async def _stream_agent(log_text: str, model: str) -> tuple[list[str], str | None]:
    """Run the LangGraph agent and collect step HTML + final result."""
    inputs = {
        "messages": [(
            "user",
            f"Analyze this Airflow log and find the root cause. "
            f"Please provide a detailed JSON report: {log_text}",
        )],
        "raw_log": log_text,
    }
    ctx = Context(model=model)
    steps_html: list[str] = []
    final_result: str | None = None

    async for chunk in graph.astream(inputs, stream_mode="values", context=ctx):
        msg = chunk["messages"][-1]
        msg_type = getattr(msg, "type", "unknown")

        html = _build_steps_html(msg_type, msg)
        if html:
            steps_html.append(html)

        if msg_type == "ai" and hasattr(msg, "content") and msg.content:
            final_result = str(msg.content)

    return steps_html, final_result


def run_analysis(log_text: str, model: str) -> tuple[list[str], str | None]:
    """Synchronous wrapper for Streamlit callers."""
    return asyncio.run(_stream_agent(log_text, model))


def parse_result_json(raw: str) -> dict | None:
    """Strip markdown fences and parse JSON. Returns None on failure."""
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
