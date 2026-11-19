"""
R.O.S.E (Project Mirror) - Phase 6: Telegram Remote Control
Long-polling `python-telegram-bot` (v20+) running in its own daemon thread & asyncio event loop.

Security & Architecture Guarantees:
- Never opens ports (long-polling only).
- Strict numeric user ID allowlist (`TELEGRAM_ALLOWED_USER_IDS` from `.env`).
- Unauthorized users receive NO reply, are logged & audited without secret leakage,
  and trigger an owner alert on Telegram after every 3rd unauthorized attempt.
- Rate limit: 20 messages/minute per authorized user cleanly enforced.
- Long outputs split into chunks <= 4000 characters on clean boundaries.
- All plain-language commands and transcribed voice notes route through `tools.registry.registry`
  and obey R.O.S.E permission tiers (Tier 0-3) with 60s inline Confirm/Cancel buttons for Tier 2.
- `/kill` triggers `security_manager.trigger_kill_switch`, stops WhatsApp & Telegram cleanly,
  and puts R.O.S.E into locked mode. Unlock can NEVER happen via Telegram — only directly on the laptop.
- Laptop wake notification sends `"ROSE back online"` cleanly to authorized owner chat(s).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Protocol, Tuple
import uuid

from dotenv import load_dotenv

from core.audit import audit_log, redact_secrets
from core.logging_setup import get_logger
from core.permissions import Tier, security_manager
from core.power_control import register_service_stopper
from core.result import Result
from tools.registry import registry

# Ensure built-in tools are registered cleanly
import tools.builtins  # noqa: F401

logger = get_logger("telegram_bot")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"

TELEGRAM_MAX_CHUNK = 4000
RATE_LIMIT_MAX_MESSAGES = 20
RATE_LIMIT_WINDOW_SECONDS = 60.0
CONFIRM_TIMEOUT_SECONDS = 60.0

_BOT_START_MONO = time.monotonic()

# Actions inside multi-purpose tools that escalate to Tier 2 Confirmation over Telegram/voice
_TIER2_LAPTOP_ACTIONS = {
    "sleep",
    "sleep_laptop",
    "sleep_pc",
    "standby",
    "suspend",
    "put my laptop to sleep",
    "hibernate",
    "restart",
    "restart_laptop",
    "shutdown",
    "shutdown_laptop",
    "shutdown_self",
    "sleep_and_shutdown_self",
    "sleep_and_exit",
    "signout",
    "logoff",
}
_TIER2_FILE_ACTIONS = {"delete", "remove", "unlink", "move", "rename", "overwrite"}
_TIER2_PC_OPT_ACTIONS = {"clean", "cleanup", "kill", "kill_process", "delete"}

_UNLOCK_PHRASE_RE = re.compile(
    r"^(?:/?unlock(?:\s+(?:rose|jarvis|kill\s*switch|lock))?|unlock\s+yourself|disable\s+kill\s*switch)\b",
    re.IGNORECASE,
)


# ── Configuration Helpers ────────────────────────────────────────────────────

@dataclass
class TelegramConfig:
    token: str
    allowed_user_ids: set[int]
    enabled: bool
    reason: str = ""


def parse_allowed_user_ids(raw: str) -> set[int]:
    """Parses comma/whitespace-separated numeric Telegram user IDs cleanly."""
    ids: set[int] = set()
    if not raw:
        return ids
    for token in re.split(r"[\s,;]+", str(raw).strip()):
        tok = token.strip()
        if not tok:
            continue
        if tok.lstrip("-").isdigit():
            val = int(tok)
            if val > 0:
                ids.add(val)
        else:
            logger.warning("Ignoring non-numeric entry in TELEGRAM_ALLOWED_USER_IDS.")
    return ids


def load_telegram_config(env_path: Optional[Path] = None) -> TelegramConfig:
    """Loads Telegram credentials cleanly from `.env` + environment without logging secrets."""
    target_env = env_path or DEFAULT_ENV_PATH
    if target_env.exists():
        load_dotenv(dotenv_path=target_env, override=False)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    allowed_raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    allowed_ids = parse_allowed_user_ids(allowed_raw)

    if not token:
        return TelegramConfig(
            token="",
            allowed_user_ids=allowed_ids,
            enabled=False,
            reason="TELEGRAM_BOT_TOKEN is not configured in .env.",
        )
    if not allowed_ids:
        return TelegramConfig(
            token=token,
            allowed_user_ids=set(),
            enabled=False,
            reason="TELEGRAM_ALLOWED_USER_IDS is empty or contains no valid numeric IDs.",
        )
    return TelegramConfig(token=token, allowed_user_ids=allowed_ids, enabled=True, reason="Configured")


# ── Message Splitting & Rate Limiting ────────────────────────────────────────

def split_telegram_message(text: str, max_len: int = TELEGRAM_MAX_CHUNK) -> List[str]:
    """Splits arbitrarily long text into Telegram-safe chunks (<= max_len, always < 4096)."""
    clean = str(text or "").strip()
    if not clean:
        return ["(empty response)"]

    limit = max(200, min(4096, int(max_len)))
    if len(clean) <= limit:
        return [clean]

    chunks: List[str] = []
    remaining = clean
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        window = remaining[:limit]
        split_at = window.rfind("\n\n")
        if split_at < limit // 3:
            split_at = window.rfind("\n")
        if split_at < limit // 3:
            split_at = window.rfind(" ")
        if split_at <= 0:
            split_at = limit

        part = remaining[:split_at].rstrip()
        if not part:
            part = remaining[:limit]
            split_at = limit
        chunks.append(part)
        remaining = remaining[split_at:].lstrip()

    return chunks


class RateLimiter:
    """Sliding-window per-user rate limiter (default: 20 messages per 60 seconds)."""

    def __init__(
        self,
        max_messages: int = RATE_LIMIT_MAX_MESSAGES,
        window_seconds: float = RATE_LIMIT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_messages = int(max_messages)
        self.window_seconds = float(window_seconds)
        self.clock = clock
        self._buckets: Dict[int, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, user_id: int) -> Tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = self.clock()
        cutoff = now - self.window_seconds
        with self._lock:
            dq = self._buckets[user_id]
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= self.max_messages:
                retry_after = max(0.5, (dq[0] + self.window_seconds) - now)
                return False, retry_after
            dq.append(now)
            return True, 0.0


# ── Pending Tier 2 Confirmation Store (60s Expiry) ───────────────────────────

@dataclass
class PendingConfirmation:
    confirm_id: str
    tool_name: str
    args: Dict[str, Any]
    user_id: int
    chat_id: int
    summary: str
    created_at: float
    expires_at: float


class PendingConfirmationStore:
    """Stores pending Tier 2 actions awaiting owner tap of inline Confirm/Cancel buttons."""

    def __init__(
        self,
        ttl_seconds: float = CONFIRM_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ttl_seconds = float(ttl_seconds)
        self.clock = clock
        self._items: Dict[str, PendingConfirmation] = {}
        self._lock = threading.Lock()

    def create(
        self,
        tool_name: str,
        args: Dict[str, Any],
        user_id: int,
        chat_id: int,
        summary: str,
    ) -> PendingConfirmation:
        now = self.clock()
        self.prune_expired(now)
        cid = uuid.uuid4().hex[:14]
        item = PendingConfirmation(
            confirm_id=cid,
            tool_name=tool_name,
            args=dict(args or {}),
            user_id=user_id,
            chat_id=chat_id,
            summary=summary,
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        with self._lock:
            self._items[cid] = item
        return item

    def prune_expired(self, now: Optional[float] = None) -> int:
        ts = self.clock() if now is None else now
        with self._lock:
            expired_ids = [cid for cid, it in self._items.items() if ts >= it.expires_at]
            for cid in expired_ids:
                self._items.pop(cid, None)
            return len(expired_ids)

    def resolve(self, confirm_id: str, acting_user_id: int) -> Tuple[str, Optional[PendingConfirmation]]:
        """
        Attempts to claim a pending confirmation for execution.
        Returns status in: "ok", "expired", "not_found", "wrong_user".
        """
        now = self.clock()
        with self._lock:
            item = self._items.get(confirm_id)
            if item is None:
                return "not_found", None
            if acting_user_id != item.user_id:
                return "wrong_user", None
            if now >= item.expires_at:
                self._items.pop(confirm_id, None)
                return "expired", item
            self._items.pop(confirm_id, None)
            return "ok", item

    def cancel(self, confirm_id: str, acting_user_id: int) -> Tuple[str, Optional[PendingConfirmation]]:
        """Cancels a pending action without executing it."""
        now = self.clock()
        with self._lock:
            item = self._items.get(confirm_id)
            if item is None:
                return "not_found", None
            if acting_user_id != item.user_id:
                return "wrong_user", None
            self._items.pop(confirm_id, None)
            if now >= item.expires_at:
                return "expired", item
            return "cancelled", item

    def cancel_all(self, reason: str = "") -> int:
        with self._lock:
            count = len(self._items)
            self._items.clear()
            return count

    def active_count(self) -> int:
        self.prune_expired()
        with self._lock:
            return len(self._items)


# ── Effective Permission Tier & NL Router ────────────────────────────────────

def get_effective_tier(tool_name: str, args: Dict[str, Any]) -> int:
    """
    Returns effective Tier for a tool invocation via Telegram.
    Destructive/power sub-actions inside multi-action tools elevate to Tier 2.
    """
    tool_def = registry.get_tool(tool_name)
    base_tier = int(tool_def.permission_tier) if tool_def else int(Tier.TIER_1_REVERSIBLE)
    safe_args = args or {}

    if tool_name == "laptop_control":
        act = str(safe_args.get("action", "")).strip().lower()
        if act in _TIER2_LAPTOP_ACTIONS:
            return int(Tier.TIER_2_CONFIRM)
    elif tool_name == "file_intelligence":
        act = str(safe_args.get("action", "")).strip().lower()
        if act in _TIER2_FILE_ACTIONS:
            return int(Tier.TIER_2_CONFIRM)
    elif tool_name == "pc_optimizer":
        act = str(safe_args.get("action", "")).strip().lower()
        if act in _TIER2_PC_OPT_ACTIONS:
            return int(Tier.TIER_2_CONFIRM)

    return base_tier


def format_confirmation_summary(tool_name: str, args: Dict[str, Any]) -> str:
    """Human-readable confirmation prompt showing safe non-secret arguments."""
    safe_args = redact_secrets(args or {})
    if tool_name == "send_whatsapp_message":
        rec = safe_args.get("recipient_name") or safe_args.get("phone_number") or safe_args.get("recipient", "recipient")
        msg = str(safe_args.get("message", ""))
        preview = msg if len(msg) <= 140 else msg[:137] + "..."
        return f"Send WhatsApp message to '{rec}': \"{preview}\""
    if tool_name == "laptop_control":
        act = str(safe_args.get("action", "power action")).replace("_", " ")
        return f"Execute laptop power action: {act}"
    arg_str = ", ".join(f"{k}={v}" for k, v in safe_args.items() if k != "confirmed")
    return f"Run Tier 2 tool `{tool_name}`" + (f" ({arg_str})" if arg_str else "")


def _fastpath_nl_intent(text: str) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """
    Deterministic fast-path routing for clear everyday remote commands.
    Falls back cleanly to Gemini function calling when ambiguous or conversational.
    """
    raw = str(text or "").strip()
    low = raw.lower()
    if not raw:
        return None, None, "Empty message."

    # Power intents
    if re.search(r"\b(put\s+(?:the\s+|my\s+)?laptop\s+to\s+sleep|sleep\s+(?:the\s+|my\s+)?laptop)\b", low):
        return "laptop_control", {"action": "sleep_laptop"}, None
    if re.search(r"\b(lock\s+(?:the\s+|my\s+)?laptop|lock\s+screen)\b", low):
        return "laptop_control", {"action": "lock"}, None
    if re.search(r"\b(restart\s+(?:the\s+|my\s+)?laptop|reboot\s+(?:the\s+|my\s+)?laptop)\b", low):
        return "laptop_control", {"action": "restart_laptop"}, None
    if re.search(r"\b(shut\s*down\s+(?:the\s+|my\s+)?laptop)\b", low):
        return "laptop_control", {"action": "shutdown_laptop"}, None

    # WhatsApp send intents:
    # "send whatsapp to Tunde: Hi there"
    # "send a whatsapp message to Tunde saying Hey bro"
    # "whatsapp Tunde: On my way"
    wa_patterns = [
        r"^(?:please\s+)?send\s+(?:a\s+)?whatsapp(?:\s+message)?\s+to\s+(.+?)\s*(?::|saying|that|msg|message)\s+(.+)$",
        r"^(?:please\s+)?(?:whatsapp|message\s+on\s+whatsapp)\s+(.+?)\s*(?::|saying|that)\s+(.+)$",
        r"^(?:please\s+)?send\s+(?:a\s+)?message\s+to\s+(.+?)\s+on\s+whatsapp\s*(?::|saying|that)?\s+(.+)$",
    ]
    for pat in wa_patterns:
        m = re.match(pat, raw, re.IGNORECASE)
        if m:
            rec = m.group(1).strip().strip("\"'")
            msg = m.group(2).strip().strip("\"'")
            if rec and msg:
                digits = re.sub(r"\D+", "", rec)
                if len(digits) >= 10 and not re.search(r"[a-zA-Z]", rec):
                    return "send_whatsapp_message", {"phone_number": rec, "message": msg}, None
                return "send_whatsapp_message", {"recipient_name": rec, "message": msg}, None

    # Unread WhatsApp
    if re.search(r"\b(unread\s+whatsapp|whatsapp\s+unread|check\s+whatsapp)\b", low):
        return "get_whatsapp_unread", {}, None

    # Open application: "open Chrome", "launch VS Code"
    m_open = re.match(r"^(?:please\s+)?(?:open|launch|start)\s+([a-zA-Z0-9 ._\-+]{2,40})$", raw, re.IGNORECASE)
    if m_open:
        target_app = m_open.group(1).strip()
        if target_app.lower() not in {"the door", "eyes", "camera"} and "." not in target_app:
            return "open_app", {"app_name": target_app}, None

    # Business Advisor Mode intents
    if re.search(r"\b(business\s+advisor|advise\s+me|critique|venture\s+review|business\s+brain)\b", low):
        return "business_advisor", {"query": raw, "channel": "telegram"}, None

    # SwiftSats / business report & telemetry questions
    if re.search(r"\b(swiftsats|business\s+report|daily\s+report|how\s+many\s+logins|signups\s+today|stuck\s+payments)\b", low):
        return "swiftsats_intelligence", {"query": raw}, None

    # System vitals
    if re.search(r"\b(battery\s+(?:level|status|percent)|check\s+battery)\b", low):
        return "sys_info", {"query": "battery"}, None
    if re.search(r"\b(cpu\s+usage|ram\s+usage|disk\s+space|system\s+info)\b", low):
        return "sys_info", {"query": "all"}, None

    # Explicit literal laptop announcement: e.g. "say Hello Boss on my laptop"
    # Note: questions like "What is 2+2 and say it out on laptop?" skip fastpath so Gemini answers first.
    literal_speech = extract_literal_laptop_speech(raw)
    if literal_speech:
        return "speak_on_laptop", {"text": literal_speech}, None

    return None, None, None


_LAPTOP_DEVICE_RE = r"(?:the\s+|my\s+)?(?:laptop|pc|computer|desktop|speakers?)"
_LAPTOP_SPEECH_PATTERNS = (
    re.compile(
        rf"\b(?:say|speak|read|announce|tell)\s+(?:it|this|that|the\s+answer|them|aloud|out)?\s*(?:out\s+)?(?:loud\s+)?(?:on|through|via|over|from|to)\s+{_LAPTOP_DEVICE_RE}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:say|speak|read|announce)\b.{{0,80}}\b(?:on|through|via|over|from)\s+{_LAPTOP_DEVICE_RE}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:on|through|via)\s+{_LAPTOP_DEVICE_RE}\b.{{0,60}}\b(?:say|speak|read\s+aloud|read\s+out|announce|out\s+loud)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:say\s+it\s+out\s+loud|speak\s+it\s+out\s+loud|read\s+it\s+out\s+loud)\b",
        re.IGNORECASE,
    ),
)
_QUESTION_OR_TOOL_HINT_RE = re.compile(
    r"(\?|\b(?:what|who|when|where|why|how|which|whose|whom|explain|define|describe|tell\s+me\s+about|"
    r"calculate|solve|compare|summarize|check|weather|battery|cpu|ram|disk|time|date|jobs|news|"
    r"report|calendar|reminder|it|this|that|the\s+answer|the\s+result)\b)",
    re.IGNORECASE,
)


def wants_laptop_speech(text: str) -> bool:
    """Returns True when the Telegram user asked R.O.S.E. to speak an answer/message out on the laptop."""
    raw = str(text or "").strip()
    if not raw:
        return False
    return any(p.search(raw) for p in _LAPTOP_SPEECH_PATTERNS)


def extract_literal_laptop_speech(text: str) -> Optional[str]:
    """
    Extracts a literal phrase from commands like 'say Hello Boss on laptop' or
    'speak on my laptop: Meeting starts now'. Returns None if the input looks like
    a question or multi-step request needing AI reasoning first.
    """
    raw = " ".join(str(text or "").strip().split())
    if not raw or "?" in raw:
        return None

    m1 = re.match(
        rf"^(?:please\s+)?(?:say|speak|announce|read(?:\s+aloud|\s+out(?:\s+loud)?)?)\s+(?:this\s+|that\s+)?(?:out\s+(?:loud\s+)?)?(?:on|through|via|over|from)\s+{_LAPTOP_DEVICE_RE}\s*[:,\-]?\s+(.+)$",
        raw,
        re.IGNORECASE,
    )
    m2 = re.match(
        rf"^(?:please\s+)?(?:say|speak|announce|read(?:\s+aloud|\s+out(?:\s+loud)?)?)\s*[:,\-]?\s*(.+?)\s+(?:out\s+(?:loud\s+)?)?(?:on|through|via|over|from)\s+{_LAPTOP_DEVICE_RE}\s*[.!]?$",
        raw,
        re.IGNORECASE,
    )
    candidate = None
    if m1:
        candidate = m1.group(1).strip().strip("\"'")
    elif m2:
        candidate = m2.group(1).strip().strip("\"'")
    if not candidate:
        return None
    if _QUESTION_OR_TOOL_HINT_RE.search(candidate) or re.search(r"\band\s+(?:say|speak|tell|read)\b", raw, re.IGNORECASE):
        return None
    return candidate or None


def _clean_text_for_speech(text: str, max_chars: int = 1200) -> str:
    """Strips Markdown/emoji clutter so Gemini Live or Windows TTS speaks cleanly."""
    clean = str(text or "").strip()
    clean = re.sub(r"https?://\S+", "", clean)
    clean = clean.replace("```", " ").replace("`", "")
    clean = re.sub(r"[*_~]+", "", clean)
    clean = re.sub(r"^[\s\-*•#>]+", "", clean, flags=re.MULTILINE)
    clean = " ".join(clean.split())
    if len(clean) > max_chars:
        cut = clean[:max_chars].rsplit(" ", 1)[0].strip()
        clean = (cut or clean[:max_chars]) + "..."
    return clean


def _is_speakable_reply(text: str) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False
    error_prefixes = (
        "could not process natural language command",
        "could not match a built-in command",
        "i could not understand that request",
        "understood, though no tool action was selected",
        "empty message",
    )
    return not any(low.startswith(p) for p in error_prefixes)


def _gemini_nl_route(text: str) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """Routes freeform natural language via Gemini with the R.O.S.E tool declarations."""
    try:
        from tools.declarations import get_api_key

        api_key = get_api_key()
        if not api_key:
            return (
                None,
                None,
                "Could not match a built-in command and Gemini API key is not set. Use /help or a clearer phrasing.",
            )

        from app_config import generate_gemini_content
        from google.genai import types

        decls = registry.to_gemini_declarations()
        sys_inst = (
            "You are R.O.S.E responding to the laptop owner remotely over Telegram. "
            "If the user requests an action or system query covered by a tool, invoke the exact matching tool. "
            "LAPTOP SPEECH CAPABILITY: You CAN speak out loud through the laptop speakers using `speak_on_laptop(text=...)`. "
            "If the user asks you to say, speak, announce, or read something aloud on the laptop — OR asks you a question and tells you to say/speak the answer out on the laptop — "
            "compute the clear, natural spoken English answer and pass THAT COMPLETE ANSWER into `speak_on_laptop(text=...)` "
            "(unless a live laptop/weather/status tool must be queried first, in which case call that tool). "
            "Never say you cannot speak on the laptop, and never claim an action happened without calling its tool. "
            "Otherwise reply concisely in plain English."
        )
        resp = generate_gemini_content(
            api_key,
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=sys_inst,
                tools=[{"function_declarations": decls}],
                temperature=0.2,
            ),
        )
        candidates = getattr(resp, "candidates", None) or []
        for cand in candidates:
            parts = getattr(getattr(cand, "content", None), "parts", None) or []
            for part in parts:
                fc = getattr(part, "function_call", None)
                if fc and getattr(fc, "name", None):
                    return str(fc.name), dict(fc.args or {}), None

        txt = (getattr(resp, "text", None) or "").strip()
        return None, None, txt or "Understood, though no tool action was selected."
    except Exception as exc:
        logger.warning(f"Gemini NL route error: {exc}")
        return None, None, f"Could not process natural language command right now ({type(exc).__name__})."


def route_natural_language(
    text: str,
    nl_router_fn: Optional[Callable[[str], Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]]] = None,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """Routes plain text to either a tool call `(name, args, None)` or direct reply `(None, None, text)`."""
    if nl_router_fn is not None:
        return nl_router_fn(text)
    tool_name, args, reply = _fastpath_nl_intent(text)
    if tool_name or reply:
        return tool_name, args, reply
    return _gemini_nl_route(text)


def transcribe_voice_note(
    audio_bytes: bytes,
    mime_type: str = "audio/ogg",
    transcriber_fn: Optional[Callable[[bytes, str], str]] = None,
) -> Tuple[bool, str]:
    """Transcribes Telegram voice note audio bytes via Gemini multimodal API."""
    if not audio_bytes:
        return False, "Empty voice note received."

    if transcriber_fn is not None:
        try:
            text = str(transcriber_fn(audio_bytes, mime_type) or "").strip()
            if not text:
                return False, "Transcription returned empty text."
            return True, text
        except Exception as exc:
            return False, f"Voice transcription failed: {exc}"

    try:
        from tools.declarations import get_api_key

        api_key = get_api_key()
        if not api_key:
            return False, "Gemini API key not configured; cannot transcribe Telegram voice note."

        from app_config import generate_gemini_content
        from google.genai import types

        resp = generate_gemini_content(
            api_key,
            contents=[
                types.Part.from_bytes(data=audio_bytes, mime_type=mime_type or "audio/ogg"),
                "Transcribe this voice message verbatim. Return ONLY the spoken words as plain text with no commentary.",
            ],
            config=types.GenerateContentConfig(temperature=0.0),
        )
        transcript = (getattr(resp, "text", None) or "").strip()
        if not transcript:
            return False, "Could not hear clear speech in voice note."
        return True, transcript
    except Exception as exc:
        logger.warning(f"Telegram voice transcription failed: {exc}")
        return False, f"Voice transcription error: {type(exc).__name__}: {exc}"


# ── Honest Command Data Builders ─────────────────────────────────────────────

def format_uptime(seconds: float) -> str:
    sec = max(0, int(seconds))
    days, rem = divmod(sec, 86400)
    hrs, rem = divmod(rem, 3600)
    mins, s = divmod(rem, 60)
    parts: List[str] = []
    if days:
        parts.append(f"{days}d")
    if hrs or days:
        parts.append(f"{hrs}h")
    parts.append(f"{mins}m")
    if not days and not hrs:
        parts.append(f"{s}s")
    return " ".join(parts)


def build_status_report() -> str:
    """Honest Windows + R.O.S.E runtime status report (/status)."""
    lines: List[str] = ["R.O.S.E System Status"]
    try:
        import psutil

        cpu_pct = psutil.cpu_percent(interval=0.25)
        cores = psutil.cpu_count(logical=True) or 0
        vm = psutil.virtual_memory()
        ram_used = vm.used / (1024 ** 3)
        ram_total = vm.total / (1024 ** 3)
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        du = psutil.disk_usage(drive)
        disk_free = du.free / (1024 ** 3)
        disk_total = du.total / (1024 ** 3)
        disk_pct = du.percent
        bat = psutil.sensors_battery()
        bat_str = (
            f"{bat.percent:.0f}% ({'Charging' if bat.power_plugged else 'On battery'})"
            if bat is not None
            else "No battery sensor"
        )
        sys_uptime = format_uptime(time.time() - psutil.boot_time())
        bot_uptime = format_uptime(time.monotonic() - _BOT_START_MONO)

        lines.extend([
            f"- CPU: {cpu_pct:.1f}% ({cores} logical cores)",
            f"- RAM: {ram_used:.1f} GB / {ram_total:.1f} GB ({vm.percent:.0f}%)",
            f"- Disk ({drive}): {disk_free:.1f} GB free / {disk_total:.1f} GB ({disk_pct:.0f}% used)",
            f"- Battery: {bat_str}",
            f"- Windows Uptime: {sys_uptime}",
            f"- Telegram Bridge Uptime: {bot_uptime}",
        ])
    except Exception as exc:
        lines.append(f"- System telemetry error: {exc}")

    try:
        from actions.whatsapp_bridge import get_whatsapp_status

        wa = get_whatsapp_status()
        wa_state = "Connected" if wa.get("connected") else ("Running (unpaired)" if wa.get("running") else "Offline")
        lines.append(f"- WhatsApp Bridge: {wa_state}")
    except Exception:
        lines.append("- WhatsApp Bridge: Unknown")

    lines.append(f"- Security State: {'LOCKED (Kill Switch)' if security_manager.is_killed else 'ARMED'}")
    return "\n".join(lines)


def capture_telegram_screenshot(
    screenshot_fn: Optional[Callable[[], Tuple[bool, bytes | None, str]]] = None,
) -> Tuple[bool, Optional[bytes], str]:
    """Captures active primary monitor PNG bytes cleanly and deletes any temp file."""
    if screenshot_fn is not None:
        return screenshot_fn()

    try:
        from actions.screen_vision import _capture_targets

        ok, err_msg, captures = _capture_targets("monitor_1")
        if not ok or not captures:
            return False, None, err_msg or "Failed to capture monitor screenshot."

        cap = captures[0]
        img_path = Path(str(cap.get("image_path") or ""))
        try:
            if not img_path.exists():
                return False, None, "Captured screenshot file missing on disk."
            raw_bytes = img_path.read_bytes()
            label = str(cap.get("label") or "Monitor 1")
            return True, raw_bytes, label
        finally:
            for item in captures:
                try:
                    p = Path(str(item.get("image_path") or ""))
                    if p.exists():
                        p.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception as exc:
        return False, None, f"Screenshot capture failed: {exc}"


def build_jobs_report(
    jobs_fetcher: Optional[Callable[[], Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]] = None,
    limit: int = 5,
) -> str:
    """Formats Top 5 picks from Job Command Center cleanly."""
    try:
        if jobs_fetcher is not None:
            ranked, health = jobs_fetcher()
        else:
            from actions.job_engine import scan_all_sources

            ranked, health = scan_all_sources(force=False)
    except Exception as exc:
        return f"Could not load Job Command Center feed: {exc}"

    eligible = [
        j
        for j in (ranked or [])
        if not j.get("blacklisted") and j.get("eligibility_status") != "excluded"
    ]
    eligible.sort(key=lambda j: float(j.get("score") or 0), reverse=True)
    picks = eligible[: max(1, int(limit))]

    if not picks:
        return "Job Command Center: No eligible Nigeria-compatible picks found right now."

    lines = [f"Top {len(picks)} Job Picks (Nigeria/WAT Eligible):"]
    for idx, job in enumerate(picks, start=1):
        score = job.get("score", "?")
        title = str(job.get("title") or "Untitled Role").strip()
        company = str(job.get("company") or "Unknown").strip()
        elig = str(job.get("eligibility_label") or job.get("eligibility_status") or "remote").strip()
        jtype = str(job.get("job_type") or "").strip()
        url = str(job.get("url") or "").strip()
        meta_parts = [f"Score {score}/100", elig]
        if jtype and jtype.lower() != "unknown":
            meta_parts.append(jtype)
        lines.append(f"{idx}. {title} — {company}")
        lines.append("   " + " | ".join(meta_parts))
        if url:
            lines.append(f"   {url}")

    return "\n".join(lines)


def build_business_report(
    report_fn: Optional[Callable[[], str]] = None,
    query: str = "daily_report",
) -> str:
    """Formats business / SwiftSats report cleanly."""
    try:
        if report_fn is not None:
            return str(report_fn() or "No business report output.")
        from actions.swiftsats_bi import swiftsats_intelligence

        return str(swiftsats_intelligence(query or "daily_report") or "No SwiftSats report data available.")
    except Exception as exc:
        return f"Business report failed: {exc}"


# ── Transport Abstraction (supports live PTB Bot OR offline test mock) ────────

class BotTransport(Protocol):
    async def send_text(
        self,
        chat_id: int,
        text: str,
        buttons: Optional[List[Tuple[str, str]]] = None,
    ) -> Any:
        ...

    async def send_photo(self, chat_id: int, photo_bytes: bytes, caption: str = "") -> Any:
        ...

    async def edit_text(self, chat_id: int, message_id: int, text: str) -> Any:
        ...

    async def answer_callback(self, callback_id: str, text: str = "") -> Any:
        ...


class _PTBTransport:
    """Wraps python-telegram-bot `Bot` cleanly with auto-chunking."""

    def __init__(self, bot: Any):
        self.bot = bot

    async def send_text(
        self,
        chat_id: int,
        text: str,
        buttons: Optional[List[Tuple[str, str]]] = None,
    ) -> Any:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        chunks = split_telegram_message(text)
        reply_markup = None
        if buttons:
            keyboard = [[InlineKeyboardButton(label, callback_data=cb_data) for label, cb_data in buttons]]
            reply_markup = InlineKeyboardMarkup(keyboard)

        last_msg = None
        for idx, chunk in enumerate(chunks):
            rm = reply_markup if idx == len(chunks) - 1 else None
            last_msg = await self.bot.send_message(chat_id=chat_id, text=chunk, reply_markup=rm)
        return last_msg

    async def send_photo(self, chat_id: int, photo_bytes: bytes, caption: str = "") -> Any:
        import io

        buf = io.BytesIO(photo_bytes)
        buf.name = "rose_screenshot.png"
        return await self.bot.send_photo(chat_id=chat_id, photo=buf, caption=(caption or "")[:1000])

    async def edit_text(self, chat_id: int, message_id: int, text: str) -> Any:
        clean = split_telegram_message(text, max_len=TELEGRAM_MAX_CHUNK)[0]
        return await self.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=clean)

    async def answer_callback(self, callback_id: str, text: str = "") -> Any:
        return await self.bot.answer_callback_query(callback_query_id=callback_id, text=text[:190])


# ── Core Controller (PTB-wired AND 100% offline-testable) ───────────────────

class RoseTelegramController:
    """Handles authorization, rate limiting, commands, Tier 2 confirmations, voice, kill switch & wake alerts."""

    def __init__(
        self,
        allowed_user_ids: set[int],
        transport: Optional[BotTransport] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        nl_router_fn: Optional[Callable[[str], Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]]] = None,
        transcriber_fn: Optional[Callable[[bytes, str], str]] = None,
        screenshot_fn: Optional[Callable[[], Tuple[bool, Optional[bytes], str]]] = None,
        jobs_fetcher: Optional[Callable[[], Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]] = None,
        report_fn: Optional[Callable[[], str]] = None,
        shutdown_cb: Optional[Callable[[], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
    ):
        self.allowed_user_ids = {int(uid) for uid in allowed_user_ids if int(uid) > 0}
        self.transport = transport
        self.clock = clock
        self.rate_limiter = RateLimiter(clock=clock)
        self.confirm_store = PendingConfirmationStore(clock=clock)
        self.nl_router_fn = nl_router_fn
        self.transcriber_fn = transcriber_fn
        self.screenshot_fn = screenshot_fn
        self.jobs_fetcher = jobs_fetcher
        self.report_fn = report_fn
        self.shutdown_cb = shutdown_cb
        self.log_cb = log_cb

        self._unauth_total = 0
        self._unauth_by_user: Dict[int, int] = defaultdict(int)
        self._unauth_lock = threading.Lock()

    def _emit_ui_log(self, msg: str) -> None:
        if self.log_cb:
            try:
                self.log_cb(msg)
            except Exception:
                pass

    def is_authorized(self, user_id: Optional[int]) -> bool:
        if user_id is None:
            return False
        return int(user_id) in self.allowed_user_ids

    async def _handle_unauthorized(self, user_id: Optional[int], username: str, preview: str) -> bool:
        """
        Silently ignores the unauthorized sender (no reply sent to sender),
        logs + audits the attempt, and sends a Telegram security alert to owner(s) after 3 attempts.
        Returns True if owner alert was sent.
        """
        uid = int(user_id or 0)
        safe_preview = str(redact_secrets(preview or ""))[:60]
        with self._unauth_lock:
            self._unauth_total += 1
            self._unauth_by_user[uid] += 1
            total = self._unauth_total
            per_user = self._unauth_by_user[uid]

        logger.warning(f"Unauthorized Telegram attempt #{total} from user_id={uid} (user={username or 'unknown'})")
        audit_log(
            tool="telegram_unauthorized_access",
            args={"user_id": uid, "attempt_total": total, "attempt_user": per_user, "preview": safe_preview},
            tier=int(Tier.TIER_0_READ_ONLY),
            result=Result.failure(f"Ignored unauthorized Telegram user {uid}"),
            source="telegram",
        )

        # Alert owner after every 3 unauthorized attempts
        if total % 3 == 0 and self.transport and self.allowed_user_ids:
            alert_text = (
                f"⚠️ SECURITY ALERT: {total} unauthorized Telegram access attempts detected!\n"
                f"Latest attempt: user_id={uid} ({username or 'unknown'}), sender count={per_user}.\n"
                f"All unauthorized requests were silently ignored."
            )
            for owner_id in sorted(self.allowed_user_ids):
                try:
                    await self.transport.send_text(owner_id, alert_text)
                except Exception as exc:
                    logger.error(f"Failed sending unauthorized alert to owner {owner_id}: {exc}")
            return True
        return False

    async def _send(
        self,
        chat_id: int,
        text: str,
        buttons: Optional[List[Tuple[str, str]]] = None,
    ) -> None:
        if self.transport is None:
            return
        await self.transport.send_text(chat_id, text, buttons=buttons)

    def _is_unlock_attempt(self, text: str) -> bool:
        return bool(_UNLOCK_PHRASE_RE.match(str(text or "").strip()))

    async def _reject_if_killed(self, chat_id: int, text: str = "") -> Tuple[bool, str]:
        if not security_manager.is_killed:
            return False, ""
        if self._is_unlock_attempt(text):
            msg = (
                "🔒 Unlock refused: Kill Switch CANNOT be unlocked from Telegram. "
                "Unlock R.O.S.E directly on the laptop UI/local control."
            )
        else:
            msg = (
                "🔒 R.O.S.E is LOCKED by Kill Switch. All Telegram commands are disabled until unlocked directly on the laptop."
            )
        await self._send(chat_id, msg)
        return True, msg

    async def _check_gate(
        self,
        user_id: Optional[int],
        chat_id: Optional[int],
        username: str,
        preview: str,
        raw_text: str = "",
    ) -> Tuple[bool, int, str]:
        """Checks auth -> kill-switch -> rate limit cleanly."""
        if not self.is_authorized(user_id):
            await self._handle_unauthorized(user_id, username, preview)
            return False, 0, ""

        assert user_id is not None
        uid = int(user_id)
        cid = int(chat_id if chat_id is not None else uid)

        is_killed, kill_msg = await self._reject_if_killed(cid, raw_text)
        if is_killed:
            return False, cid, kill_msg

        allowed, retry_after = self.rate_limiter.allow(uid)
        if not allowed:
            rl_msg = (
                f"⏳ Rate limit exceeded ({RATE_LIMIT_MAX_MESSAGES} messages/min). "
                f"Please wait {retry_after:.0f}s before sending another command."
            )
            await self._send(cid, rl_msg)
            return False, cid, rl_msg

        return True, cid, ""

    # ── Command Handlers ─────────────────────────────────────────────────────

    async def handle_command(
        self,
        command: str,
        *,
        user_id: Optional[int],
        chat_id: Optional[int],
        username: str = "",
        args_text: str = "",
    ) -> str:
        cmd = str(command or "").strip().lower().lstrip("/")
        if "@" in cmd:
            cmd = cmd.split("@", 1)[0]

        ok, cid, block_msg = await self._check_gate(user_id, chat_id, username, f"/{cmd}", f"/{cmd} {args_text}")
        if not ok:
            return block_msg

        if cmd in ("start", "help"):
            msg = (
                "R.O.S.E Remote Control Commands:\n"
                "/status — Laptop CPU, RAM, disk, battery, uptime & WhatsApp status\n"
                "/screenshot — Capture & send current monitor screenshot\n"
                "/jobs — Top 5 Nigeria-eligible Job Command Center picks\n"
                "/report — SwiftSats report (`24h` | `7d` | `30d` | `security`)\n"
                "/advise — Candid Business Advisor Mode (reads `business_brain/*.md`)\n"
                "/sleep — Put laptop to sleep (asks Confirm / Cancel)\n"
                "/lock — Lock laptop screen immediately\n"
                "/kill — EMERGENCY KILL SWITCH (locks R.O.S.E, stops WhatsApp & Telegram)\n"
                "/help — Show this help menu\n\n"
                "You can also send plain-English requests (e.g. \"open Chrome\", "
                "\"send WhatsApp to Tunde: Hi\") or Telegram voice notes."
            )
            await self._send(cid, msg)
            return msg

        if cmd == "status":
            report = await asyncio.to_thread(build_status_report)
            await self._send(cid, report)
            return report

        if cmd == "screenshot":
            cap_ok, png_bytes, detail = await asyncio.to_thread(capture_telegram_screenshot, self.screenshot_fn)
            if not cap_ok or not png_bytes:
                err = f"Screenshot failed: {detail}"
                await self._send(cid, err)
                return err
            if self.transport:
                await self.transport.send_photo(cid, png_bytes, caption=f"📸 {detail}")
            audit_log("telegram_screenshot", {"label": detail}, int(Tier.TIER_0_READ_ONLY), Result.success(verified=True), source="telegram")
            return f"Screenshot sent ({detail})"

        if cmd == "jobs":
            report = await asyncio.to_thread(build_jobs_report, self.jobs_fetcher, 5)
            await self._send(cid, report)
            return report

        if cmd == "report":
            q = args_text.strip() or "daily_report"
            if self.report_fn is not None and not args_text.strip():
                report = await asyncio.to_thread(build_business_report, self.report_fn, q)
            else:
                report = await asyncio.to_thread(build_business_report, self.report_fn if not args_text.strip() else None, q)
            await self._send(cid, report)
            return report

        if cmd in ("advise", "advisor"):
            from actions.business_advisor import advise_business

            q = args_text.strip() or "Review my active ventures in business_brain and point out top risks and missing numbers."
            res = await asyncio.to_thread(advise_business, q, "", "telegram")
            full_text = str(res.get("full_analysis") or "No Business Advisor output.")
            await self._send(cid, full_text)
            return full_text

        if cmd == "lock":
            res = await registry.async_execute(
                "laptop_control",
                {"action": "lock"},
                confirmed=False,
                source="telegram",
                voice_verified=False,
            )
            msg = res.to_user_message()
            await self._send(cid, msg)
            return msg

        if cmd == "sleep":
            return await self._prompt_tier2_confirmation(
                user_id=int(user_id),  # type: ignore[arg-type]
                chat_id=cid,
                tool_name="laptop_control",
                args={"action": "sleep_laptop"},
            )

        if cmd == "kill":
            cancelled = self.confirm_store.cancel_all("Kill switch via Telegram /kill")
            reply = (
                "🛑 KILL SWITCH ACTIVATED.\n"
                f"Cancelled {cancelled} pending confirmation(s), stopped WhatsApp & Telegram services, and locked R.O.S.E.\n"
                "Unlock MUST be done directly on the laptop."
            )
            await self._send(cid, reply)
            self._emit_ui_log("ERR: 🔒 Telegram /kill received — activating Kill Switch.")
            # Trigger kill switch asynchronously right after sending final notification cleanly
            await asyncio.to_thread(security_manager.trigger_kill_switch, "Telegram /kill command")
            if self.shutdown_cb:
                try:
                    self.shutdown_cb()
                except Exception:
                    pass
            return reply

        if cmd == "unlock":
            msg = (
                "🔒 Unlock refused: Kill Switch CANNOT be unlocked from Telegram. "
                "Unlock must be triggered locally on the laptop."
            )
            await self._send(cid, msg)
            return msg

        unknown = f"Unknown command `/{cmd}`. Send /help for available commands or write in plain English."
        await self._send(cid, unknown)
        return unknown

    # ── Tier 2 Inline Confirmation Prompt & Execution ────────────────────────

    async def _prompt_tier2_confirmation(
        self,
        *,
        user_id: int,
        chat_id: int,
        tool_name: str,
        args: Dict[str, Any],
    ) -> str:
        summary = format_confirmation_summary(tool_name, args)
        pending = self.confirm_store.create(tool_name, args, user_id, chat_id, summary)
        prompt_text = (
            f"⚠️ Confirmation Required (Tier 2)\n"
            f"{summary}\n"
            f"Tap Confirm within {int(CONFIRM_TIMEOUT_SECONDS)}s to execute, or Cancel."
        )
        buttons = [
            ("✅ Confirm", f"confirm:{pending.confirm_id}"),
            ("❌ Cancel", f"cancel:{pending.confirm_id}"),
        ]
        await self._send(chat_id, prompt_text, buttons=buttons)
        audit_log(
            tool=tool_name,
            args={"pending_confirm_id": pending.confirm_id, **(args or {})},
            tier=int(Tier.TIER_2_CONFIRM),
            result=Result.unverified(message="Awaiting Telegram owner confirmation"),
            source="telegram",
            confirmed_by_user=False,
        )
        return prompt_text

    async def _execute_tool_with_tiers_detail(
        self,
        *,
        user_id: int,
        chat_id: int,
        tool_name: str,
        args: Dict[str, Any],
    ) -> Tuple[str, bool, int]:
        tier = get_effective_tier(tool_name, args)
        if tier >= int(Tier.TIER_3_HARD_BLOCKED):
            res = await registry.async_execute(
                tool_name,
                args,
                confirmed=False,
                source="telegram",
                voice_verified=False,
            )
            msg = f"🚫 Blocked: {res.to_user_message()}"
            await self._send(chat_id, msg)
            return msg, False, tier

        if tier == int(Tier.TIER_2_CONFIRM):
            prompt = await self._prompt_tier2_confirmation(
                user_id=user_id,
                chat_id=chat_id,
                tool_name=tool_name,
                args=args,
            )
            return prompt, False, tier

        res = await registry.async_execute(
            tool_name,
            args,
            confirmed=False,
            source="telegram",
            voice_verified=False,
        )
        msg = res.to_user_message()
        if tool_name == "speak_on_laptop" and res.ok and not msg.startswith("🔊"):
            msg = f"🔊 {msg}"
        await self._send(chat_id, msg)
        return msg, bool(res.ok), tier

    async def _execute_tool_with_tiers(
        self,
        *,
        user_id: int,
        chat_id: int,
        tool_name: str,
        args: Dict[str, Any],
    ) -> str:
        msg, _, _ = await self._execute_tool_with_tiers_detail(
            user_id=user_id,
            chat_id=chat_id,
            tool_name=tool_name,
            args=args,
        )
        return msg

    async def _speak_text_followup(self, text: str) -> Tuple[bool, str]:
        spoken = _clean_text_for_speech(text)
        if not spoken:
            return False, "No speakable text."
        speak_res = await registry.async_execute(
            "speak_on_laptop",
            {"text": spoken},
            confirmed=False,
            source="telegram",
            voice_verified=False,
        )
        return bool(speak_res.ok), speak_res.to_user_message()

    async def _dispatch_nl_text(
        self,
        raw: str,
        *,
        uid: int,
        cid: int,
        force_laptop_speech: bool = False,
    ) -> str:
        speak_requested = force_laptop_speech or wants_laptop_speech(raw)
        route_query = raw
        if force_laptop_speech and not wants_laptop_speech(raw):
            route_query = (
                f"{raw}\n(Answer concisely or perform any needed query, then speak the answer aloud on the laptop via speak_on_laptop.)"
            )

        tool_name, args, direct_reply = await asyncio.to_thread(route_natural_language, route_query, self.nl_router_fn)
        if tool_name:
            msg, exec_ok, tier = await self._execute_tool_with_tiers_detail(
                user_id=uid,
                chat_id=cid,
                tool_name=tool_name,
                args=args or {},
            )
            if speak_requested and exec_ok and tool_name != "speak_on_laptop" and tier < int(Tier.TIER_2_CONFIRM):
                sp_ok, sp_msg = await self._speak_text_followup(msg)
                note = "🔊 Also spoken on laptop." if sp_ok else f"⚠️ Laptop speech failed: {sp_msg}"
                await self._send(cid, note)
                return f"{msg}\n\n{note}"
            return msg

        reply = direct_reply or "I could not understand that request."
        if speak_requested and _is_speakable_reply(reply):
            sp_ok, sp_msg = await self._speak_text_followup(reply)
            suffix = "🔊 Spoken on laptop." if sp_ok else f"⚠️ Laptop speech failed: {sp_msg}"
            reply = f"{reply}\n\n{suffix}"
        await self._send(cid, reply)
        return reply

    # ── Plain Language & Voice Routing ───────────────────────────────────────

    async def handle_say_command(
        self,
        args_text: str,
        *,
        user_id: Optional[int],
        chat_id: Optional[int],
        username: str = "",
    ) -> str:
        ok, cid, block_msg = await self._check_gate(user_id, chat_id, username, f"/say {args_text}", args_text)
        if not ok:
            return block_msg
        assert user_id is not None
        uid = int(user_id)
        payload = str(args_text or "").strip()
        if not payload:
            usage = "Usage: `/say <message or question to speak aloud on laptop>`"
            await self._send(cid, usage)
            return usage
        if not _QUESTION_OR_TOOL_HINT_RE.search(payload):
            return await self._execute_tool_with_tiers(
                user_id=uid,
                chat_id=cid,
                tool_name="speak_on_laptop",
                args={"text": payload},
            )
        return await self._dispatch_nl_text(payload, uid=uid, cid=cid, force_laptop_speech=True)

    async def handle_text_message(
        self,
        text: str,
        *,
        user_id: Optional[int],
        chat_id: Optional[int],
        username: str = "",
    ) -> str:
        raw = str(text or "").strip()
        if raw.startswith("/"):
            parts = raw.split(None, 1)
            cmd_token = parts[0].strip().lower().lstrip("/").split("@", 1)[0]
            args_txt = parts[1] if len(parts) > 1 else ""
            if cmd_token in ("say", "speak"):
                return await self.handle_say_command(
                    args_txt,
                    user_id=user_id,
                    chat_id=chat_id,
                    username=username,
                )
            return await self.handle_command(
                parts[0],
                user_id=user_id,
                chat_id=chat_id,
                username=username,
                args_text=args_txt,
            )

        ok, cid, block_msg = await self._check_gate(user_id, chat_id, username, raw, raw)
        if not ok:
            return block_msg

        assert user_id is not None
        uid = int(user_id)

        if self._is_unlock_attempt(raw):
            msg = "🔒 Unlock refused: Kill Switch can ONLY be unlocked directly on the laptop."
            await self._send(cid, msg)
            return msg

        return await self._dispatch_nl_text(raw, uid=uid, cid=cid)

    async def handle_voice_message(
        self,
        audio_bytes: bytes,
        *,
        mime_type: str = "audio/ogg",
        user_id: Optional[int],
        chat_id: Optional[int],
        username: str = "",
    ) -> str:
        ok, cid, block_msg = await self._check_gate(user_id, chat_id, username, "[voice note]")
        if not ok:
            return block_msg

        t_ok, transcript = await asyncio.to_thread(
            transcribe_voice_note, audio_bytes, mime_type, self.transcriber_fn
        )
        if not t_ok:
            msg = f"🎤 {transcript}"
            await self._send(cid, msg)
            return msg

        await self._send(cid, f"🎤 Heard: \"{transcript}\"")
        return await self.handle_text_message(
            transcript,
            user_id=user_id,
            chat_id=cid,
            username=username,
        )

    # ── Inline Callback Query Handler (Confirm / Cancel) ─────────────────────

    async def handle_callback_query(
        self,
        callback_data: str,
        *,
        callback_id: str,
        user_id: Optional[int],
        chat_id: Optional[int],
        message_id: Optional[int] = None,
        username: str = "",
    ) -> str:
        if not self.is_authorized(user_id):
            await self._handle_unauthorized(user_id, username, f"callback:{callback_data}")
            return ""

        assert user_id is not None
        uid = int(user_id)
        cid = int(chat_id if chat_id is not None else uid)

        if self.transport and callback_id:
            try:
                await self.transport.answer_callback(callback_id, "")
            except Exception:
                pass

        if security_manager.is_killed:
            locked_msg = "🔒 Action denied: R.O.S.E is locked by Kill Switch. Unlock directly on the laptop."
            await self._send(cid, locked_msg)
            return locked_msg

        if ":" not in str(callback_data or ""):
            return ""

        action, confirm_id = str(callback_data).split(":", 1)
        if action == "cancel":
            status, item = self.confirm_store.cancel(confirm_id, uid)
            if status == "not_found":
                msg = "ℹ️ This confirmation is no longer active."
            elif status == "wrong_user":
                msg = "🚫 Only the requester can cancel this action."
            elif status == "expired":
                msg = "⏳ Confirmation had already expired (60s timeout)."
            else:
                msg = f"❌ Cancelled: {item.summary if item else 'action'}"
            if self.transport and message_id is not None:
                await self.transport.edit_text(cid, message_id, msg)
            else:
                await self._send(cid, msg)
            return msg

        if action == "confirm":
            status, item = self.confirm_store.resolve(confirm_id, uid)
            if status == "expired":
                msg = "⏳ Confirmation expired (60s timeout). Action was NOT executed."
                if self.transport and message_id is not None:
                    await self.transport.edit_text(cid, message_id, msg)
                else:
                    await self._send(cid, msg)
                return msg
            if status == "wrong_user":
                msg = "🚫 Only the requester can confirm this action."
                await self._send(cid, msg)
                return msg
            if status == "not_found" or item is None:
                msg = "ℹ️ Confirmation is invalid, already used, or expired."
                await self._send(cid, msg)
                return msg

            exec_args = dict(item.args or {})
            tool_def = registry.get_tool(item.tool_name)
            if tool_def is not None:
                props = (tool_def.parameters or {}).get("properties") or {}
                if "confirmed" in props:
                    exec_args["confirmed"] = True
                else:
                    try:
                        import inspect

                        sig = inspect.signature(tool_def.handler)
                        if "confirmed" in sig.parameters or any(
                            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                        ):
                            exec_args["confirmed"] = True
                    except Exception:
                        pass

            res = await registry.async_execute(
                item.tool_name,
                exec_args,
                confirmed=True,
                source="telegram",
                voice_verified=False,
            )
            msg = f"✅ Confirmed & Done:\n{res.to_user_message()}" if res.ok else f"❌ Action Failed:\n{res.to_user_message()}"
            if self.transport and message_id is not None:
                await self.transport.edit_text(cid, message_id, msg)
            else:
                await self._send(cid, msg)
            return msg

        return ""

    # ── Laptop Wake Notification ─────────────────────────────────────────────

    async def on_system_wake(self) -> int:
        """Sends 'ROSE back online' to all allowed owner chat IDs on laptop resume from sleep."""
        if security_manager.is_killed or not self.transport or not self.allowed_user_ids:
            return 0

        sent = 0
        for owner_id in sorted(self.allowed_user_ids):
            for attempt in range(3):
                try:
                    await self.transport.send_text(owner_id, "🟢 ROSE back online")
                    sent += 1
                    break
                except Exception as exc:
                    if attempt < 2:
                        await asyncio.sleep(2.0)
                    else:
                        logger.warning(f"Wake notice delivery failed for user {owner_id}: {exc}")
        return sent


# ── Background Thread + Dedicated Asyncio Event Loop Service ─────────────────

class RoseTelegramService:
    """
    Runs python-telegram-bot v20+ long-polling cleanly inside a dedicated daemon thread
    with its own asyncio event loop, plus sleep/wake watchdog & kill-switch hooks.
    """

    _RETRY_DELAYS: Tuple[float, ...] = (2.0, 5.0, 10.0, 20.0, 30.0)

    def __init__(self, config: TelegramConfig, log_cb: Optional[Callable[[str], None]] = None):
        self.config = config
        self.log_cb = log_cb
        self.controller = RoseTelegramController(
            allowed_user_ids=config.allowed_user_ids,
            log_cb=log_cb,
            shutdown_cb=self.stop_async_from_loop,
        )
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_async_event: Optional[asyncio.Event] = None
        self._stop_flag = threading.Event()
        self._app: Any = None
        self._running = False
        self._hooks_registered = False

    def register_hooks(self) -> None:
        if self._hooks_registered:
            return
        self._hooks_registered = True
        security_manager.register_kill_callback(self.stop)
        register_service_stopper("telegram_bot", self.stop)

    @property
    def is_running(self) -> bool:
        return bool(self._running and self._thread and self._thread.is_alive())

    @property
    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop_flag.is_set())

    def start(self) -> bool:
        if not self.config.enabled:
            logger.info(f"Telegram bot disabled: {self.config.reason}")
            return False
        if security_manager.is_killed:
            logger.warning("Cannot start Telegram bot while Kill Switch is active.")
            return False
        if self.is_running or self.is_alive:
            return True

        self.register_hooks()
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._thread_entry,
            name="RoseTelegramPolling",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop_async_from_loop(self) -> None:
        """Triggers clean shutdown safely from inside or outside the bot event loop."""
        self._stop_flag.set()
        if self._loop and self._stop_async_event and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._stop_async_event.set)
            except Exception:
                pass

    def stop(self) -> None:
        """Stops polling cleanly and joins the background thread."""
        self._stop_flag.set()
        if self._loop and self._stop_async_event and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._stop_async_event.set)
            except Exception:
                pass
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=4.0)
        self._running = False

    def trigger_wake_notification(self) -> None:
        if self._loop and not self._loop.is_closed() and self._running:
            try:
                asyncio.run_coroutine_threadsafe(self.controller.on_system_wake(), self._loop)
            except Exception:
                pass

    def _thread_entry(self) -> None:
        retry_delays = self._RETRY_DELAYS or (2.0, 5.0, 10.0, 20.0, 30.0)
        failure_streak = 0
        had_failure = False

        while not self._stop_flag.is_set() and not security_manager.is_killed:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                was_connected = loop.run_until_complete(
                    self._run_bot_async(notify_reconnected=had_failure)
                )
                if was_connected:
                    failure_streak = 0
                    had_failure = False
                if self._stop_flag.is_set() or security_manager.is_killed:
                    break
                delay = retry_delays[0]
            except Exception as exc:
                err_name = type(exc).__name__
                err_msg = redact_secrets(str(exc))
                if err_name == "InvalidToken":
                    logger.error(f"Telegram bot token rejected ({err_name}): {err_msg}")
                    if self.log_cb:
                        self.log_cb("ERR: Telegram bot token rejected — check TELEGRAM_BOT_TOKEN in .env.")
                    break
                had_failure = True
                failure_streak += 1
                delay = retry_delays[min(failure_streak - 1, len(retry_delays) - 1)]
                logger.warning(
                    f"Telegram polling connection error ({err_name}: {err_msg}); retrying in {delay:.0f}s."
                )
                if failure_streak == 1 and self.log_cb:
                    self.log_cb(f"SYS: Telegram offline ({err_name}) — will auto-reconnect.")
            finally:
                self._running = False
                self._stop_async_event = None
                try:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.close()
                except Exception:
                    pass
                self._loop = None

            if self._stop_flag.is_set() or security_manager.is_killed:
                break
            if self._stop_flag.wait(delay):
                break

    async def _wake_watchdog(self, stop_evt: asyncio.Event) -> None:
        """Detects Windows sleep->wake time jumps (>45s wall clock vs monotonic delta) and notifies owner."""
        last_wall = time.time()
        last_mono = time.monotonic()
        while not stop_evt.is_set() and not self._stop_flag.is_set():
            try:
                await asyncio.wait_for(stop_evt.wait(), timeout=5.0)
                break
            except asyncio.TimeoutError:
                pass
            now_wall = time.time()
            now_mono = time.monotonic()
            wall_dt = now_wall - last_wall
            mono_dt = now_mono - last_mono
            last_wall = now_wall
            last_mono = now_mono
            # On sleep->wake Windows wall clock advances while monotonic/sleep stalls or lags significantly
            if (wall_dt - mono_dt) > 45.0 or mono_dt > 60.0:
                logger.info("System resume detected; checking Telegram polling & sending wake notice.")
                await asyncio.sleep(3.0)
                if self._app is not None and getattr(self._app, "updater", None) is not None:
                    try:
                        if not self._app.updater.running and not self._stop_flag.is_set():
                            from telegram import Update
                            await self._app.updater.start_polling(
                                drop_pending_updates=True,
                                allowed_updates=Update.ALL_TYPES,
                            )
                    except Exception as exc:
                        logger.warning(f"Polling reconnect on wake retryable error: {exc}")
                await self.controller.on_system_wake()

    async def _run_bot_async(self, *, notify_reconnected: bool = False) -> bool:
        from telegram import Update
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )

        stop_evt = asyncio.Event()
        self._stop_async_event = stop_evt

        app = Application.builder().token(self.config.token).build()
        self._app = app
        self.controller.transport = _PTBTransport(app.bot)

        async def _on_command(cmd_name: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            user = update.effective_user
            chat = update.effective_chat
            args_txt = " ".join(context.args or []) if context and getattr(context, "args", None) else ""
            if cmd_name in ("say", "speak"):
                await self.controller.handle_say_command(
                    args_txt,
                    user_id=user.id if user else None,
                    chat_id=chat.id if chat else None,
                    username=(user.username or user.full_name or "") if user else "",
                )
                return
            await self.controller.handle_command(
                cmd_name,
                user_id=user.id if user else None,
                chat_id=chat.id if chat else None,
                username=(user.username or user.full_name or "") if user else "",
                args_text=args_txt,
            )

        def _make_command_handler(cmd_name: str):
            async def _handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                await _on_command(cmd_name, update, context)
            return _handler

        for cname in ("start", "help", "status", "screenshot", "jobs", "report", "advise", "advisor", "say", "speak", "sleep", "lock", "kill", "unlock"):
            app.add_handler(CommandHandler(cname, _make_command_handler(cname)))

        async def _on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            msg = update.effective_message
            user = update.effective_user
            chat = update.effective_chat
            if not msg or not msg.text:
                return
            await self.controller.handle_text_message(
                msg.text,
                user_id=user.id if user else None,
                chat_id=chat.id if chat else None,
                username=(user.username or user.full_name or "") if user else "",
            )

        async def _on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            msg = update.effective_message
            user = update.effective_user
            chat = update.effective_chat
            voice_obj = (msg.voice or msg.audio) if msg else None
            if not voice_obj:
                return
            try:
                tg_file = await context.bot.get_file(voice_obj.file_id)
                raw_bytes = bytes(await tg_file.download_as_bytearray())
                mime = getattr(voice_obj, "mime_type", None) or "audio/ogg"
            except Exception as exc:
                logger.error(f"Failed downloading Telegram voice file: {exc}")
                return
            await self.controller.handle_voice_message(
                raw_bytes,
                mime_type=mime,
                user_id=user.id if user else None,
                chat_id=chat.id if chat else None,
                username=(user.username or user.full_name or "") if user else "",
            )

        async def _on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            cq = update.callback_query
            user = update.effective_user
            chat = update.effective_chat
            if not cq:
                return
            msg_id = cq.message.message_id if cq.message else None
            await self.controller.handle_callback_query(
                cq.data or "",
                callback_id=cq.id,
                user_id=user.id if user else None,
                chat_id=chat.id if chat else None,
                message_id=msg_id,
                username=(user.username or user.full_name or "") if user else "",
            )

        last_poll_err_ts = [0.0]
        fatal_poll_exc: list[Optional[Exception]] = [None]

        def _on_polling_error(exc: Exception) -> None:
            err_name = type(exc).__name__
            err_msg = redact_secrets(str(exc))
            now_mono = time.monotonic()
            if (now_mono - last_poll_err_ts[0]) >= 30.0:
                last_poll_err_ts[0] = now_mono
                logger.warning(f"Telegram polling transient error ({err_name}): {err_msg}")
            if err_name in ("Conflict", "InvalidToken"):
                fatal_poll_exc[0] = exc
                stop_evt.set()

        async def _ptb_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
            exc = getattr(context, "error", None)
            if exc is not None:
                logger.warning(f"Telegram update handler error ({type(exc).__name__}): {redact_secrets(str(exc))}")

        app.add_handler(CallbackQueryHandler(_on_callback))
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, _on_voice))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_text))
        app.add_error_handler(_ptb_error_handler)

        initialized = False
        started = False
        connected = False
        watchdog_task: Optional[asyncio.Task[Any]] = None
        try:
            await app.initialize()
            initialized = True
            await app.start()
            started = True
            if app.updater is not None:
                await app.updater.start_polling(
                    drop_pending_updates=True,
                    allowed_updates=Update.ALL_TYPES,
                    error_callback=_on_polling_error,
                )
            self._running = True
            connected = True

            logger.info(f"Telegram bot long-polling started (allowed IDs: {len(self.config.allowed_user_ids)}).")
            if self.log_cb:
                if notify_reconnected:
                    self.log_cb(
                        f"SYS: Telegram remote control reconnected ({len(self.config.allowed_user_ids)} allowed user ID)."
                    )
                else:
                    self.log_cb(
                        f"SYS: Telegram remote control active ({len(self.config.allowed_user_ids)} allowed user ID)."
                    )

            watchdog_task = asyncio.create_task(self._wake_watchdog(stop_evt))
            while not stop_evt.is_set() and not self._stop_flag.is_set():
                try:
                    await asyncio.wait_for(stop_evt.wait(), timeout=15.0)
                    break
                except asyncio.TimeoutError:
                    if fatal_poll_exc[0] is not None:
                        raise fatal_poll_exc[0]
                    if app.updater is not None and not app.updater.running:
                        raise RuntimeError("Telegram updater stopped unexpectedly")
            if fatal_poll_exc[0] is not None:
                raise fatal_poll_exc[0]
            return connected
        finally:
            if watchdog_task is not None:
                watchdog_task.cancel()
            try:
                if app.updater is not None and app.updater.running:
                    await app.updater.stop()
            except Exception:
                pass
            if started:
                try:
                    await app.stop()
                except Exception:
                    pass
            if initialized:
                try:
                    await app.shutdown()
                except Exception:
                    pass
            self._running = False
            if self._app is app:
                self._app = None
            logger.info("Telegram bot polling stopped cleanly.")


_TELEGRAM_SERVICE_SINGLETON: Optional[RoseTelegramService] = None
_SERVICE_LOCK = threading.Lock()


def get_telegram_service() -> Optional[RoseTelegramService]:
    return _TELEGRAM_SERVICE_SINGLETON


def start_telegram_bot_if_configured(
    log_cb: Optional[Callable[[str], None]] = None,
    env_path: Optional[Path] = None,
) -> Optional[RoseTelegramService]:
    """Starts the singleton Telegram long-polling service if configured in `.env`."""
    global _TELEGRAM_SERVICE_SINGLETON
    with _SERVICE_LOCK:
        if _TELEGRAM_SERVICE_SINGLETON is not None and (
            _TELEGRAM_SERVICE_SINGLETON.is_running or _TELEGRAM_SERVICE_SINGLETON.is_alive
        ):
            return _TELEGRAM_SERVICE_SINGLETON

        cfg = load_telegram_config(env_path=env_path)
        if not cfg.enabled:
            logger.info(f"Telegram bot skipped: {cfg.reason}")
            return None

        svc = RoseTelegramService(cfg, log_cb=log_cb)
        if svc.start():
            _TELEGRAM_SERVICE_SINGLETON = svc
            return svc
        return None


def stop_telegram_bot() -> None:
    global _TELEGRAM_SERVICE_SINGLETON
    with _SERVICE_LOCK:
        svc = _TELEGRAM_SERVICE_SINGLETON
        _TELEGRAM_SERVICE_SINGLETON = None
    if svc is not None:
        svc.stop()


def _run_coro_sync(coro_factory: Callable[[], Any], timeout: float = 20.0) -> Any:
    """Runs an async coroutine safely whether or not the caller's thread already has a running loop."""
    try:
        asyncio.get_running_loop()
        in_running_loop = True
    except RuntimeError:
        in_running_loop = False

    if not in_running_loop:
        return asyncio.run(coro_factory())

    box: Dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["result"] = asyncio.run(coro_factory())
        except Exception as exc:
            box["error"] = exc

    t = threading.Thread(target=_worker, daemon=True, name="ROSE-TelegramSend")
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        raise TimeoutError("Timed out sending Telegram message.")
    if "error" in box:
        raise box["error"]
    return box.get("result")


def send_owner_telegram_message(
    message: str,
    *,
    chat_id: Optional[int] = None,
    env_path: Optional[Path] = None,
    sender_fn: Optional[Callable[[int, str], Any]] = None,
) -> Tuple[bool, str]:
    """Sends a text message from R.O.S.E. on the laptop directly to the owner's configured Telegram chat."""
    if security_manager.is_killed:
        return False, "R.O.S.E is locked by Kill Switch. Unlock on laptop before sending Telegram messages."

    clean = str(message or "").strip()
    if not clean:
        return False, "Message text is required to send to Telegram."

    cfg = load_telegram_config(env_path=env_path)
    if not cfg.enabled:
        return False, cfg.reason

    if chat_id is not None:
        cid = int(chat_id)
        if cid not in cfg.allowed_user_ids:
            return False, f"Refused: chat_id {cid} is not in TELEGRAM_ALLOWED_USER_IDS."
        targets = [cid]
    else:
        targets = sorted(cfg.allowed_user_ids)

    chunks = split_telegram_message(clean)
    try:
        if sender_fn is not None:
            for cid in targets:
                for chunk in chunks:
                    sender_fn(cid, chunk)
        else:
            svc = get_telegram_service()
            if (
                svc is not None
                and svc.is_running
                and svc._loop is not None
                and svc.controller.transport is not None
            ):
                for cid in targets:
                    for chunk in chunks:
                        fut = asyncio.run_coroutine_threadsafe(
                            svc.controller.transport.send_text(cid, chunk),
                            svc._loop,
                        )
                        fut.result(timeout=15.0)
            else:
                from telegram import Bot

                async def _send_ephemeral() -> None:
                    async with Bot(token=cfg.token) as bot:
                        for cid in targets:
                            for chunk in chunks:
                                await bot.send_message(chat_id=cid, text=chunk)

                _run_coro_sync(_send_ephemeral, timeout=25.0)

        audit_log(
            tool="send_telegram_message",
            args={"targets": len(targets), "chunks": len(chunks), "chars": len(clean)},
            tier=int(Tier.TIER_1_REVERSIBLE),
            result=Result.success(verified=True, message="Sent to owner Telegram"),
            source="laptop",
        )
        return True, f"Sent message to Telegram ({len(targets)} recipient(s), {len(chunks)} chunk(s))."
    except Exception as exc:
        safe_err = redact_secrets(f"{type(exc).__name__}: {exc}")
        logger.warning(f"Outgoing Telegram send failed: {safe_err}")
        return False, f"Failed sending Telegram message ({safe_err})."
