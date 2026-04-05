"""Log text normalization for clustering. No ML/Streamlit imports."""

import re
from pathlib import Path


# ── Variable substitutions ────────────────────────────────────────────────────
# Replace per-log variable parts that carry no semantic signal

_SUBSTITUTIONS = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "[TIME]"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "[DATE]"),
    (re.compile(r"\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"), "[TIME]"),
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "[IP]"),
    (re.compile(r"0x[0-9a-fA-F]+"), "[HEX]"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "[UUID]"),
    (re.compile(r"(?:/[\w.\-]+){2,}"), "[PATH]"),
    (re.compile(r"\b\d+\b"), "[NUM]"),
]

# ── Airflow / Jupyter boilerplate line filter ─────────────────────────────────
# Lines matching this pattern carry no error-type signal and are dropped entirely

_BOILERPLATE_LINE = re.compile(
    r"^\s*(?:"
    r"INFO\s*-"
    r"|DEBUG\s*-"
    r"|WARNING\s*-\s*(?:No|The|Unable)"
    r"|-{10,}"           # separator lines
    r")",
    re.IGNORECASE,
)

# ── Traceback frame classification ────────────────────────────────────────────
# A "File ..." line inside a traceback is a FRAMEWORK frame if it comes from
# well-known infrastructure packages.  User frames (DAG code, notebooks) are kept.

_FRAMEWORK_FRAME = re.compile(
    r'File\s+"[^"]*(?:'
    r'site-packages'
    r'|dist-packages'
    r'|airflow'
    r'|ipykernel'
    r'|IPython'
    r'|ipython'
    r'|notebook'
    r'|nbconvert'
    r'|jupyter'
    r'|tornado'
    r'|asyncio'
    r'|concurrent'
    r'|threading'
    r'|runpy\.py'
    r'|importlib'
    r')[^"]*"',
    re.IGNORECASE,
)

# A "File ..." line is a USER frame if it comes from user code paths
_USER_FRAME = re.compile(
    r'File\s+"[^"]*(?:'
    r'<ipython-input'    # Jupyter cell
    r'|<string>'
    r'|dags/'            # Airflow DAG directory
    r'|/home/'
    r'|/workspace/'
    r'|/opt/airflow/dags'
    r')[^"]*"',
    re.IGNORECASE,
)


def _extract_traceback_essence(traceback_text: str) -> str:
    """
    From a traceback block, extract only the semantically rich lines:

    1. The final error line(s)  — e.g. "ModuleNotFoundError: No module named 'x'"
    2. User code frame lines    — e.g. File "<ipython-input-5>", line 3
    3. The code line under a user frame (the actual user statement that failed)

    Framework frames (airflow, ipykernel, site-packages …) are stripped.
    This prevents 100-line Jupyter tracebacks from polluting TF-IDF vectors.
    """
    lines = [l for l in traceback_text.splitlines() if l.strip()]

    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip pure framework frames and their code line
        if _FRAMEWORK_FRAME.search(line):
            i += 1
            # Skip the code line that follows a framework frame
            if i < len(lines) and not lines[i].strip().startswith("File "):
                i += 1
            continue

        # Keep user frames + the code line that follows
        if _USER_FRAME.search(line):
            result.append(line.strip())
            i += 1
            if i < len(lines) and not lines[i].strip().startswith("File "):
                result.append(lines[i].strip())
                i += 1
            continue

        # Everything that is NOT a "File ..." line is either:
        #   - The final error line (e.g. "ValueError: ...")
        #   - An error message continuation
        # Always keep these.
        if not line.strip().startswith("File "):
            result.append(line.strip())

        i += 1

    return "\n".join(result)


def extract_error_core(text: str) -> str:
    """
    Extract the highest-signal portion of a raw log for clustering.

    Strategy:
    - If the log contains a traceback, parse it with _extract_traceback_essence
      to strip framework noise and keep only the error lines + user code frames.
    - If no traceback, take the last 15 lines (short logs are already concise).
    - Hard cap: 50 lines max after extraction.
    """
    # Drop pure boilerplate lines first
    cleaned_lines = [
        l for l in text.splitlines()
        if not _BOILERPLATE_LINE.match(l)
    ]
    cleaned = "\n".join(cleaned_lines)

    if "Traceback (most recent call last):" in cleaned:
        # Take the LAST traceback block (most relevant in multi-exception logs)
        tb_block = cleaned.split("Traceback (most recent call last):")[-1]
        essence = _extract_traceback_essence(tb_block)

        # Fallback: if essence is empty (all lines were framework), take last 10 lines
        if not essence.strip():
            lines = [l.strip() for l in tb_block.splitlines() if l.strip()]
            essence = "\n".join(lines[-10:])

        lines = [l for l in essence.splitlines() if l.strip()]
        return "\n".join(lines[:50])

    # No traceback: short log, take last 15 lines
    lines = [l.strip() for l in cleaned.splitlines() if l.strip()]
    return "\n".join(lines[-15:])


def normalize(text: str) -> str:
    """Apply variable substitutions and return lowercase."""
    lines = []
    for line in text.splitlines():
        for pattern, replacement in _SUBSTITUTIONS:
            line = pattern.sub(replacement, line)
        line = line.strip()
        if line:
            lines.append(line.lower())
    return " ".join(lines)


def load_logs(root: str) -> list[dict]:
    """Walk root recursively, return list of {path, raw, normalized}."""
    records = []
    for path in sorted(Path(root).rglob("*.log")):
        raw = path.read_text(encoding="utf-8", errors="ignore")
        core = extract_error_core(raw)
        norm = normalize(core)
        if norm.strip():
            records.append({"path": str(path), "raw": raw, "normalized": norm})
    return records
