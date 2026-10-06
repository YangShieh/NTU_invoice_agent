"""Privacy-safe JSONL diagnostics for the OCR service."""

from __future__ import annotations

import json
import os
import re
import threading
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any


LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
_LOCK = threading.Lock()
_ID_PATTERN = re.compile(r"\b(?:[A-Z][12]\d{8}|[A-Z]{2}\d{8}|\d{10,16})\b")
RETENTION_DAYS = 30


def _safe_text(value: Any) -> str:
    return _ID_PATTERN.sub("[REDACTED]", str(value))[:2000]


def error_details(exc: BaseException) -> dict[str, Any]:
    frames = traceback.extract_tb(exc.__traceback__)[-12:]
    return {
        "error_type": type(exc).__name__,
        "error_message": _safe_text(exc),
        "stack": [
            {"file": os.path.basename(f.filename), "line": f.lineno, "function": f.name}
            for f in frames
        ],
    }


def log_event(case_id: str, stage: str, outcome: str, **details: Any) -> None:
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "case_id": case_id or "unassigned",
        "stage": stage,
        "outcome": outcome,
        "details": {key: _safe_text(value) if isinstance(value, str) else value for key, value in details.items()},
    }
    try:
        with _LOCK:
            os.makedirs(LOG_DIR, exist_ok=True)
            path = os.path.join(LOG_DIR, f"ocr-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
            for name in os.listdir(LOG_DIR):
                old_path = os.path.join(LOG_DIR, name)
                if name.startswith("ocr-") and datetime.fromtimestamp(
                    os.path.getmtime(old_path), tz=timezone.utc
                ) < cutoff:
                    os.remove(old_path)
    except Exception:
        pass
