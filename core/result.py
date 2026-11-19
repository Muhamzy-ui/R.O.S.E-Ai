"""
R.O.S.E (Project Mirror) - Core Result Model
Truthful structured results for all tools and system actions.
Rule A2: No fake success.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Result:
    """
    Structured outcome of every tool call and system action.
    A tool may only set verified=True if it checked the real-world effect.
    """
    ok: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    verified: bool = False

    @classmethod
    def success(
        cls,
        data: Optional[Dict[str, Any]] = None,
        verified: bool = True,
        message: Optional[str] = None,
    ) -> Result:
        d = dict(data or {})
        if message and "message" not in d:
            d["message"] = message
        return cls(ok=True, data=d, error=None, verified=verified)

    @classmethod
    def unverified(
        cls,
        data: Optional[Dict[str, Any]] = None,
        message: Optional[str] = None,
    ) -> Result:
        """Action triggered but real-world confirmation could not be verified."""
        d = dict(data or {})
        if message and "message" not in d:
            d["message"] = message
        return cls(ok=True, data=d, error=None, verified=False)

    @classmethod
    def failure(
        cls,
        error: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Result:
        return cls(ok=False, data=dict(data or {}), error=str(error), verified=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "verified": self.verified,
        }

    def to_user_message(self) -> str:
        """
        Human-readable speech/chat string for Gemini to deliver truthfully.
        Enforces Rule A2: Plain speech, no assumed success.
        """
        if not self.ok:
            err = self.error or "Unknown error"
            return f"Action failed: {err}"

        msg = self.data.get("message")
        if not self.verified:
            base = msg or "Action was triggered"
            return f"{base}, but I could not confirm it took effect."

        if msg:
            return msg

        # If data has a direct text summary
        if "text" in self.data:
            return str(self.data["text"])
        if "summary" in self.data:
            return str(self.data["summary"])

        return "Action completed and verified successfully."

