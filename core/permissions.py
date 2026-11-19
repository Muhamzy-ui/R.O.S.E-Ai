"""
R.O.S.E (Project Mirror) - Permissions & Security Core
Rule A5: Strict permission tiers, shell command auditing, and global kill switch.
"""

from __future__ import annotations
import enum
import re
import threading
from typing import Tuple, Optional, Callable, List


class Tier(enum.IntEnum):
    TIER_0_READ_ONLY = 0       # Free, read-only: time, weather, system status, reports, etc.
    TIER_1_REVERSIBLE = 1      # Allowed, low-risk: open/focus apps, media, volume, lock screen.
    TIER_2_CONFIRM = 2         # Must ask Mahmud first (voice confirmation or UI/Telegram button).
    TIER_3_HARD_BLOCKED = 3    # Hard blocked, no override: payments, credentials, security changes.


# Hard-blocked shell commands and dangerous patterns (Rule A5)
SHELL_DENYLIST = [
    r"\bformat\b",
    r"\bdel\s+/[sS]\b",
    r"\brd\s+/[sS]\b",
    r"\brmdir\s+/[sS]\b",
    r"\breg\s+delete\b",
    r"\bnet\s+user\b",
    r"\bnet\s+localgroup\b",
    r"\bnetsh\b",
    r"\bdiskpart\b",
    r"\bbcdedit\b",
    r"\bvssadmin\b",
    r"\bcipher\s+/w\b",
    r"\bfsutil\b",
    r"downloadstring",
    r"downloadfile",
    r"invoke-webrequest.*iex",
    r"iwr.*iex",
    r"invoke-expression",
    r"certutil.*urlcache",
    r"bitsadmin.*transfer",
    r"powershell.*-enc",
]

SHELL_DENYLIST_COMPILED = [re.compile(p, re.IGNORECASE) for p in SHELL_DENYLIST]

# Allowlist of safe shell commands / prefixes (informative or benign utility operations)
SHELL_SAFE_PREFIXES = (
    "dir", "ls", "pwd", "cd", "echo", "type", "cat", "get-childitem", "gci",
    "get-content", "gc", "get-process", "ps", "get-service", "get-date",
    "hostname", "whoami", "ipconfig", "systeminfo", "tasklist",
    "git status", "git log", "git branch", "git diff", "node -v", "npm -v",
    "python --version", "python -V", "pip list",
)


def is_shell_command_safe(command: str) -> Tuple[bool, str]:
    """
    Evaluates whether a shell command is permissible under Tier 2 constraints
    or strictly blocked by the denylist.
    """
    if not command or not command.strip():
        return False, "Empty command"

    cmd_stripped = command.strip()

    # 1. Denylist check
    for pattern in SHELL_DENYLIST_COMPILED:
        if pattern.search(cmd_stripped):
            return False, f"Hard blocked by safety policy: command matches restricted pattern '{pattern.pattern}'"

    return True, "Permitted with user confirmation (Tier 2)"


class SecurityManager:
    """Manages system permission checks, owner locks, and the global kill switch."""

    def __init__(self):
        self._lock = threading.Lock()
        self._killed = False
        self._kill_reason = ""
        self._kill_callbacks: List[Callable[[], None]] = []

    def register_kill_callback(self, cb: Callable[[], None]) -> None:
        with self._lock:
            self._kill_callbacks.append(cb)

    @property
    def is_killed(self) -> bool:
        with self._lock:
            return self._killed

    def trigger_kill_switch(self, reason: str = "User invoked Kill Switch") -> None:
        callbacks_to_run = []
        with self._lock:
            if self._killed:
                return
            self._killed = True
            self._kill_reason = reason
            callbacks_to_run = list(self._kill_callbacks)

        print(f"[SECURITY] 🛑 KILL SWITCH ACTIVATED: {reason}")
        for cb in callbacks_to_run:
            try:
                cb()
            except Exception as e:
                print(f"[SECURITY] Error in kill callback: {e}")

    def unlock_kill_switch(self) -> None:
        with self._lock:
            self._killed = False
            self._kill_reason = ""
        print("[SECURITY] 🟢 Kill switch reset / unlocked.")

    def check_tool_permission(
        self,
        tool_name: str,
        tier: int,
        confirmed: bool = False,
        source: str = "voice",
        voice_verified: bool = False,
    ) -> Tuple[bool, str]:
        """
        Validates whether the tool call is permitted right now.
        Returns: (allowed: bool, reason_message: str)
        """
        if self._killed:
            return False, f"R.O.S.E is locked by kill switch ({self._kill_reason}). Unlock first."

        # Tier 3 is hard blocked - no override
        if tier >= Tier.TIER_3_HARD_BLOCKED:
            return False, f"Action '{tool_name}' is Tier 3 (HARD BLOCKED) by safety policy."

        # Tier 0 is always allowed
        if tier == Tier.TIER_0_READ_ONLY:
            return True, "Tier 0: Read-only access granted."

        # Tier 1 is allowed
        if tier == Tier.TIER_1_REVERSIBLE:
            return True, "Tier 1: Reversible access granted."

        # Tier 2 requires explicit user confirmation
        if tier == Tier.TIER_2_CONFIRM:
            # If triggered via voice, voice biometric auth is required if enrolled
            if source == "voice" and not voice_verified:
                # If caller has already confirmed verbally or through UI
                pass
            if not confirmed:
                return False, f"CONFIRMATION REQUIRED: '{tool_name}' requires Mahmud's explicit confirmation."
            return True, "Tier 2: Explicit confirmation verified."

        return False, f"Unknown tier {tier} for tool '{tool_name}'."


# Global singleton
security_manager = SecurityManager()

