"""
R.O.S.E (Project Mirror) - Audit Logger
Append-only structured audit log (logs/audit.jsonl) with automatic secret redaction.
Rule A3 & A5: Audit trail for every tool call and system action.
"""

from __future__ import annotations
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.result import Result

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUDIT_LOG_FILE = PROJECT_ROOT / "logs" / "audit.jsonl"
AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

_LOCK = threading.Lock()

# Sensitive key names to redact
SENSITIVE_KEY_PATTERNS = re.compile(
    r"(key|token|secret|password|passwd|auth|credential|api_key|pin|bearer|authorization|dsn)",
    re.IGNORECASE,
)

# Common API key / token value patterns (Gemini, OpenAI, GitHub, Telegram Bot tokens, DSN creds)
API_KEY_REGEX = re.compile(
    r"AIza[0-9A-Za-z-_]{35}|sk-[a-zA-Z0-9]{32,}|ghp_[a-zA-Z0-9]{36}|"
    r"(?:bot)?\d{7,12}:[A-Za-z0-9_-]{28,55}|postgres(?:ql)?://[^:\s]+:[^@\s]+@"
)


def redact_secrets(val: Any) -> Any:
    """Recursively redact sensitive keys and token values."""
    if isinstance(val, dict):
        cleaned = {}
        for k, v in val.items():
            if SENSITIVE_KEY_PATTERNS.search(str(k)):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = redact_secrets(v)
        return cleaned
    elif isinstance(val, (list, tuple)):
        return [redact_secrets(item) for item in val]
    elif isinstance(val, str):
        if API_KEY_REGEX.search(val):
            return API_KEY_REGEX.sub("[REDACTED_KEY]", val)
        return val
    return val


def audit_log(
    tool: str,
    args: Dict[str, Any],
    tier: int,
    result: Result,
    source: str = "voice",
    confirmed_by_user: Optional[bool] = None,
) -> None:
    """
    Appends a single JSON line to logs/audit.jsonl.
    Thread-safe and failsafe.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "args": redact_secrets(args),
        "tier": tier,
        "ok": result.ok,
        "verified": result.verified,
        "source": source,
    }
    if result.error:
        entry["error"] = result.error
    if confirmed_by_user is not None:
        entry["confirmed_by_user"] = confirmed_by_user

    line = json.dumps(entry, ensure_ascii=False) + "\n"

    try:
        with _LOCK:
            with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception as e:
        # Failsafe: never crash app on audit logging failure
        import sys
        print(f"[ROSE Audit] Failed to write audit record: {e}", file=sys.stderr)

