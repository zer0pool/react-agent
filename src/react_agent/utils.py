"""Utility & helper functions."""

import json

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage


def get_message_text(msg: BaseMessage) -> str:
    """Get the text content of a message."""
    content = msg.content
    if isinstance(content, str):
        return content
    elif isinstance(content, dict):
        return content.get("text", "")
    else:
        txts = [c if isinstance(c, str) else (c.get("text") or "") for c in content]
        return "".join(txts).strip()


def extract_json_from_markdown(content: str) -> dict:
    """Extract a JSON object from a markdown-wrapped LLM response.

    Handles ```json ... ``` and ``` ... ``` wrappers.
    Returns a dict with parse_error=True on failure.
    """
    try:
        if "```json" in content:
            content = content.split("```json", 1)[-1].split("```", 1)[0]
        elif "```" in content:
            parts = content.split("```")
            if len(parts) >= 3:
                content = parts[1]
        return json.loads(content.strip())
    except Exception:
        return {"error_id": "UNKNOWN", "raw_content": content[:500], "parse_error": True}


def load_chat_model(fully_specified_name: str) -> BaseChatModel:
    """Load a chat model from a fully specified name.

    Args:
        fully_specified_name (str): String in the format 'provider/model'.
    """
    provider, model = fully_specified_name.split("/", maxsplit=1)
    return init_chat_model(model, model_provider=provider)
