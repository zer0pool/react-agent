import re
from typing import Any, Dict, List, Optional

# Define your known regex patterns here
KNOWN_ERRORS: List[Dict[str, Any]] = [
    {
        "pattern": re.compile(
            r"google\.api_core\.exceptions\.Forbidden: 403.*Permission denied"
        ),
        "report": {
            "error_id": "ERR-GCP-403",
            "category": "Permission Error",
            "technical_root_cause": "The service account lacks the required IAM roles to access the resource.",
            "evidence_line": "google.api_core.exceptions.Forbidden: 403",
            "resolution_step": "Check IAM permissions for the Service Account. Ensure it has roles/bigquery.dataViewer or equivalent.",
            "confidence": "high",
        },
    },
    {
        "pattern": re.compile(r"TimeoutError: Database connection timeout"),
        "report": {
            "error_id": "ERR-DB-001",
            "category": "Network / Connection",
            "technical_root_cause": "The worker failed to reach the database within the configured timeout period.",
            "evidence_line": "TimeoutError: Database connection timeout",
            "resolution_step": "Verify DB network firewall rules and ensure the database is not overloaded.",
            "confidence": "high",
        },
    },
    {
        "pattern": re.compile(r"Connection lost"),
        "report": {
            "error_id": "ERR-NET-002",
            "category": "Network",
            "technical_root_cause": "The connection was interrupted by the peer.",
            "evidence_line": "Connection lost",
            "resolution_step": "Retry the operation or check network stability.",
            "confidence": "medium",
        },
    },
]


def check_regex_patterns(log_text: str) -> Optional[Dict[str, Any]]:
    """Evaluates the log against known regex patterns and returns a report if matched."""
    if not log_text:
        return None

    for rule in KNOWN_ERRORS:
        if rule["pattern"].search(log_text):
            return rule["report"]

    return None
