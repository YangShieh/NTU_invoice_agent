"""Privacy-safe structured diagnostics for the local invoice application."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
RETENTION_DAYS = 30
_LOCK = threading.Lock()
_SENSITIVE_KEYS = {
    "account_number", "bank_code", "invoice_number", "invoice_numbers",
    "ntu_password", "password", "payee", "payee_id", "payee_name",
    "raw_output", "username",
}
_SECRET_PATTERNS = (
    (re.compile(r"(?i)(password|passwd|pwd)(\s*[:=]\s*)\S+"), r"\1\2[REDACTED]"),
    (re.compile(r"\b[A-Z][12]\d{8}\b"), "[REDACTED_ID]"),
    (re.compile(r"\b[A-Z]{2}\d{8}\b"), "[REDACTED_INVOICE]"),
    (re.compile(r"\b\d{10,16}\b"), "[REDACTED_NUMBER]"),
)


def new_case_id() -> str:
    """Return an opaque ID that can correlate events without retaining personal data."""
    return uuid.uuid4().hex


def anonymous_id(value: Any) -> str:
    """Create a non-reversible short identifier for values needed only for correlation."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12] if value else ""


def _redact_text(value: Any) -> str:
    text = str(value)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:2000]


def _sanitize(value: Any, key: str = "") -> Any:
    if key.lower() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {str(k): _sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value[:50]]
    return _redact_text(repr(value))


def error_details(exc: BaseException) -> dict[str, Any]:
    """Capture useful stack locations without serializing locals or request payloads."""
    frames = traceback.extract_tb(exc.__traceback__)[-12:]
    return {
        "error_type": type(exc).__name__,
        "error_message": _redact_text(exc),
        "stack": [
            {"file": os.path.basename(f.filename), "line": f.lineno, "function": f.name}
            for f in frames
        ],
    }


def log_event(case_id: str, stage: str, outcome: str, **details: Any) -> None:
    """Append one JSON event. Logging failures never interrupt a reimbursement."""
    now = datetime.now(timezone.utc)
    record = {
        "timestamp_utc": now.isoformat(timespec="milliseconds"),
        "case_id": case_id or "unassigned",
        "stage": stage,
        "outcome": outcome,
        "details": _sanitize(details),
    }
    try:
        with _LOCK:
            os.makedirs(LOG_DIR, exist_ok=True)
            path = os.path.join(LOG_DIR, f"operations-{now:%Y-%m-%d}.jsonl")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            _remove_expired_logs(now)
    except Exception:
        # Diagnostics must never become a new failure mode.
        pass


def _remove_expired_logs(now: datetime) -> None:
    cutoff = now - timedelta(days=RETENTION_DAYS)
    for name in os.listdir(LOG_DIR):
        if not name.startswith("operations-") or not name.endswith(".jsonl"):
            continue
        path = os.path.join(LOG_DIR, name)
        try:
            modified = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
            if modified < cutoff:
                os.remove(path)
        except OSError:
            pass
