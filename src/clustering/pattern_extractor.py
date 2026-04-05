"""Extract patterns and keywords from a cluster's logs.

Used to:
  - Suggest additions to an existing error definition (keywords, examples)
  - Generate a skeleton for a new error definition
No Streamlit imports.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

from sklearn.feature_extraction.text import CountVectorizer

from clustering.preprocessor import extract_error_core, normalize

# ── Stop words to ignore in keyword extraction ────────────────────────────────
_STOP = {
    "var", "num", "time", "date", "ip", "hex", "uuid", "path",  # our placeholders
    "file", "line", "in", "at", "the", "a", "an", "of", "for",
    "to", "is", "was", "be", "on", "with", "from", "and", "or",
    "not", "no", "it", "as", "by", "are", "has", "have",
    "traceback", "most", "recent", "call", "last",
}

# Lines that end with the actual error message (not stack frame)
_ERROR_LINE = re.compile(
    r"^(?:"
    r"[A-Za-z][\w.]*(?:Error|Exception|Warning|Fault|Failed|Failure|Timeout)"
    r"|ERROR\s*[-:]"
    r"|CRITICAL\s*[-:]"
    r")",
    re.IGNORECASE,
)


def _load_cluster_logs(paths: list[str]) -> list[str]:
    """Read raw log files and return their preprocessed texts."""
    texts = []
    for p in paths:
        try:
            raw = Path(p).read_text(encoding="utf-8", errors="ignore")
            core = extract_error_core(raw)
            texts.append(core)
        except OSError:
            pass
    return texts


def _extract_error_lines(texts: list[str]) -> list[str]:
    """Pull out the final error message lines from each log."""
    error_lines = []
    for text in texts:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line and _ERROR_LINE.match(line):
                error_lines.append(line)
                break
    return error_lines


def _top_keywords(texts: list[str], top_n: int = 20) -> list[str]:
    """Return the most frequent meaningful terms across all texts."""
    normalized = [normalize(t) for t in texts]
    try:
        vec = CountVectorizer(
            ngram_range=(1, 2),
            stop_words=list(_STOP),
            max_features=200,
            token_pattern=r"[a-zA-Z][a-zA-Z0-9_]{2,}",
        )
        X = vec.fit_transform(normalized)
        counts = X.sum(axis=0).A1
        vocab = vec.get_feature_names_out()
        top_idx = counts.argsort()[::-1][:top_n]
        return [vocab[i] for i in top_idx if counts[i] > 0]
    except ValueError:
        # Too few documents
        words = " ".join(normalized).split()
        return [w for w, _ in Counter(words).most_common(top_n) if w not in _STOP]


def _deduplicate_examples(existing: list[str], candidates: list[str], max_add: int = 3) -> list[str]:
    """Return candidates not already covered by existing examples."""
    existing_lower = {e.lower() for e in existing}
    new_ones = []
    for c in candidates:
        if c.lower() not in existing_lower and len(new_ones) < max_add:
            new_ones.append(c)
    return new_ones


# ── Public API ────────────────────────────────────────────────────────────────

def suggest_for_existing(
    paths: list[str],
    definition: dict,
) -> dict:
    """
    Given the log files of a cluster confirmed as `definition`,
    return a suggestion dict:
      {
        "new_keywords":   list[str],   # not in definition["keywords"]
        "new_examples":   list[str],   # not in representative_examples
        "updated_definition": dict,    # copy of definition with additions applied
      }
    """
    texts = _load_cluster_logs(paths)
    if not texts:
        return {"new_keywords": [], "new_examples": [], "updated_definition": definition}

    error_lines = _extract_error_lines(texts)
    top_kw = _top_keywords(texts)

    existing_kw = {k.lower() for k in definition.get("keywords", [])}
    new_keywords = [k for k in top_kw if k.lower() not in existing_kw][:10]

    existing_examples = definition.get("representative_examples", [])
    new_examples = _deduplicate_examples(existing_examples, error_lines, max_add=3)

    # Build updated definition
    updated = json.loads(json.dumps(definition))  # deep copy
    updated["keywords"] = definition.get("keywords", []) + new_keywords
    updated["representative_examples"] = existing_examples + new_examples
    updated["last_updated"] = date.today().isoformat()

    return {
        "new_keywords": new_keywords,
        "new_examples": new_examples,
        "updated_definition": updated,
    }


def suggest_new_definition(
    paths: list[str],
    existing_ids: list[str],
    notes: str = "",
) -> dict:
    """
    Given the log files of a cluster confirmed as a NEW error type,
    return a skeleton definition dict ready to be added to error_definitions.json.
    """
    texts = _load_cluster_logs(paths)
    error_lines = _extract_error_lines(texts) if texts else []
    top_kw = _top_keywords(texts) if texts else []

    # Next error ID
    nums = []
    for eid in existing_ids:
        m = re.match(r"E(\d+)", eid)
        if m:
            nums.append(int(m.group(1)))
    next_num = max(nums) + 1 if nums else 21
    new_id = f"E{next_num:03d}"

    # Derive a pattern_name from the first error line
    pattern_name = "Unknown Error"
    if error_lines:
        first = error_lines[0].split(":")[0].strip()
        pattern_name = first[:60] if first else "Unknown Error"

    # Build a simple regex pattern from the first error line
    pattern = ""
    if error_lines:
        # Escape the first error line and replace variable parts
        escaped = re.escape(error_lines[0][:120])
        # Replace escaped numbers/paths back to wildcards
        escaped = re.sub(r"\\\d+", r"\\d+", escaped)
        escaped = re.sub(r"\\/[a-zA-Z0-9_.\\/]+", r"[^'\"]+", escaped)
        pattern = escaped

    skeleton = {
        "error_id": new_id,
        "pattern_name": pattern_name,
        "pattern_type": "regex",
        "pattern": pattern,
        "category": "UNKNOWN",
        "sub_category": "UNKNOWN",
        "severity": "MEDIUM",
        "priority": "3",
        "description": notes or f"Auto-detected cluster: {pattern_name}",
        "root_cause": "(fill in root cause)",
        "resolution_steps": ["(fill in resolution steps)"],
        "prevention_tips": ["(fill in prevention tips)"],
        "representative_examples": error_lines[:4],
        "keywords": top_kw[:10],
        "confidence": 5,
        "related_errors": [],
        "source": "clustering",
        "last_updated": date.today().isoformat(),
    }
    return skeleton


def apply_to_existing(definitions_path: str, updated_definition: dict) -> None:
    """Replace the matching entry in error_definitions.json with updated_definition."""
    with open(definitions_path, encoding="utf-8") as f:
        data = json.load(f)

    target_id = updated_definition["error_id"]
    for i, d in enumerate(data):
        if d["error_id"] == target_id:
            data[i] = updated_definition
            break

    with open(definitions_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def apply_new_definition(definitions_path: str, new_definition: dict) -> None:
    """Append new_definition to error_definitions.json."""
    with open(definitions_path, encoding="utf-8") as f:
        data = json.load(f)

    # Prevent duplicate IDs
    existing_ids = {d["error_id"] for d in data}
    if new_definition["error_id"] in existing_ids:
        raise ValueError(f"error_id {new_definition['error_id']} already exists.")

    data.append(new_definition)

    with open(definitions_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
