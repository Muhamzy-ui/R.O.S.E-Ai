"""
R.O.S.E (Project Mirror) - Power Control & Lifecycle Engine (Phase 1)
Handles system power and application lifecycle:
- "put my laptop to sleep" (Tier 2)
- "shut yourself down" (Tier 2)
- "put the laptop to sleep and shut yourself down" (Tier 2)
- "lock the laptop" (Tier 1)
- "restart" (Tier 2)
- "shut down the laptop" (Tier 2)

Sleep + self-shutdown sequence order:
confirm -> save state -> stop services (WhatsApp, Telegram, camera, audio)
-> speak short goodbye & wait for audio -> launch detached sleep subprocess (~3s delay)
-> exit ROSE cleanly.
"""

from __future__ import annotations
import ctypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.audit import audit_log
from core.logging_setup import get_logger
from core.permissions import Tier
from core.result import Result

logger = get_logger("power_control")

# Registry of callbacks for state saving, service shutdown, and speech
_STATE_SAVERS: List[Callable[[], None]] = []
_SERVICE_STOPPERS: Dict[str, Callable[[], None]] = {}
_SPEECH_HANDLER: Optional[Callable[[str], None]] = None


def register_state_saver(cb: Callable[[], None]) -> None:
    _STATE_SAVERS.append(cb)


def register_service_stopper(name: str, cb: Callable[[], None]) -> None:
    _SERVICE_STOPPERS[name] = cb


def set_speech_handler(cb: Callable[[str], None]) -> None:
    global _SPEECH_HANDLER
    _SPEECH_HANDLER = cb


def save_system_state() -> None:
    """Step 2: Save persistent memory and active state."""
    logger.info("Saving system state...")
    try:
        from memory.memory_manager import load_memory
        load_memory()
    except Exception as e:
        logger.warning(f"Error persisting memory during shutdown: {e}")

    for saver in _STATE_SAVERS:
        try:
            saver()
        except Exception as e:
            logger.warning(f"State saver error: {e}")


def stop_background_services() -> None:
    """Step 3: Stop WhatsApp bridge, Telegram handler, camera, audio streams."""
    logger.info("Stopping all background services...")
    for name, stopper in _SERVICE_STOPPERS.items():
        try:
            logger.info(f"Stopping service: {name}")
            stopper()
        except Exception as e:
            logger.warning(f"Error stopping service {name}: {e}")


def speak_goodbye_message(action: str, speech_cb: Optional[Callable[[str], None]] = None, dry_run: bool = False) -> str:
    """Step 4: Speak short goodbye and wait for audio to complete."""
    phrases = {
        "sleep_laptop": "Goodnight Boss. Putting the laptop to sleep and shutting down.",
        "sleep_and_shutdown_self": "Goodnight Boss. Putting the laptop to sleep and powering down.",
        "shutdown_self": "Shutting down R.O.S.E now. Goodbye Boss.",
        "shutdown_laptop": "Shutting down the laptop now. Goodbye Boss.",
        "restart_laptop": "Restarting the laptop now. See you soon Boss.",
    }
    msg = phrases.get(action, "Power sequence initiated. Goodbye Boss.")
    logger.info(f"Goodbye message: {msg}")

    handler = speech_cb or _SPEECH_HANDLER
    if handler:
        try:
            handler(msg)
            if not dry_run:
                # Allow buffer to drain out to the speaker hardware
                time.sleep(2.0)
        except Exception as e:
            logger.warning(f"Error in speech goodbye: {e}")
    return msg


def launch_detached_power_command(action: str, delay_seconds: int = 3, dry_run: bool = False) -> Optional[subprocess.Popen]:
    """
    Step 5: Launch power action as a detached background subprocess with delay.
    Detachment ensures R.O.S.E's exit does NOT kill the power command,
    and prevents Windows from freezing R.O.S.E in an un-exited zombie state.
    """
    logger.info(f"Preparing detached power command for action '{action}' (delay={delay_seconds}s, dry_run={dry_run})")

    if dry_run:
        return None

    # Windows process creation flags for complete detachment
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )

    if action in ("sleep_laptop", "sleep_and_shutdown_self"):
        # Uses SetSuspendState 0,1,0. Note: if Windows hibernation is enabled,
        # SetSuspendState may enter hibernation instead of modern standby.
        cmd = f"Start-Sleep -Seconds {delay_seconds}; rundll32.exe powrprof.dll,SetSuspendState 0,1,0"
        return subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd],
            creationflags=creation_flags,
            close_fds=True,
        )

    elif action == "restart_laptop":
        cmd = f"shutdown.exe /r /t {delay_seconds} /c \"R.O.S.E initiated system restart\""
        return subprocess.Popen(
            cmd,
            shell=True,
            creationflags=creation_flags,
            close_fds=True,
        )

    elif action == "shutdown_laptop":
        cmd = f"shutdown.exe /s /t {delay_seconds} /c \"R.O.S.E initiated system shutdown\""
        return subprocess.Popen(
            cmd,
            shell=True,
            creationflags=creation_flags,
            close_fds=True,
        )

    return None


def lock_laptop() -> Result:
    """Lock Windows screen immediately (Tier 1 - no confirmation needed)."""
    try:
        res = ctypes.windll.user32.LockWorkStation()
        logger.info("Windows workstation locked successfully.")
        return Result.success(
            data={"action": "lock_laptop", "result": bool(res)},
            verified=True,
            message="Locked the Windows workstation.",
        )
    except Exception as e:
        logger.error(f"Failed to lock workstation: {e}")
        return Result.failure(f"Could not lock workstation: {e}")


def execute_power_sequence(
    action: str,
    confirmed: bool = False,
    speech_cb: Optional[Callable[[str], None]] = None,
    dry_run: bool = False,
) -> Result:
    """
    Executes the strict Power Control sequence:
    1. Lock -> Tier 1, immediate.
    2. Power actions -> Tier 2:
       confirm -> save state -> stop services -> speak goodbye -> detached power command -> exit.
    """
    act = (action or "").strip().lower()

    # Normalization of aliases
    if act in ("lock", "lock_screen", "lock_laptop"):
        audit_log("power_lock", {"action": act}, Tier.TIER_1_REVERSIBLE, Result.success(verified=True), source="power")
        return lock_laptop()

    alias_map = {
        "sleep": "sleep_laptop",
        "sleep_pc": "sleep_laptop",
        "standby": "sleep_laptop",
        "put my laptop to sleep": "sleep_laptop",
        "shut yourself down": "shutdown_self",
        "shutdown_rose": "shutdown_self",
        "close_rose": "shutdown_self",
        "exit_rose": "shutdown_self",
        "put the laptop to sleep and shut yourself down": "sleep_and_shutdown_self",
        "sleep_and_exit": "sleep_and_shutdown_self",
        "restart": "restart_laptop",
        "restart_pc": "restart_laptop",
        "reboot": "restart_laptop",
        "shut down the laptop": "shutdown_laptop",
        "shutdown": "shutdown_laptop",
        "shutdown_pc": "shutdown_laptop",
        "power_off": "shutdown_laptop",
    }
    canonical_act = alias_map.get(act, act)

    valid_power_actions = {
        "sleep_laptop",
        "shutdown_self",
        "sleep_and_shutdown_self",
        "restart_laptop",
        "shutdown_laptop",
    }

    if canonical_act not in valid_power_actions:
        return Result.failure(f"Unknown power action '{action}'")

    # Step 1: Confirmation enforcement (Tier 2)
    if not confirmed:
        prompt_desc = {
            "sleep_laptop": "put your laptop to sleep and shut down R.O.S.E",
            "shutdown_self": "shut down R.O.S.E",
            "sleep_and_shutdown_self": "put the laptop to sleep and shut down R.O.S.E",
            "restart_laptop": "restart your laptop",
            "shutdown_laptop": "shut down your laptop",
        }.get(canonical_act, canonical_act)

        msg = f"Confirmation required (Tier 2): Are you sure you want to {prompt_desc}?"
        logger.info(f"Prompting user confirmation for '{canonical_act}'")
        return Result.failure(
            error=msg,
            data={"confirmation_required": True, "action": canonical_act},
        )

    # Step 2: Save state
    save_system_state()

    # Step 3: Stop background services
    stop_background_services()

    # Step 4: Speak goodbye
    goodbye_msg = speak_goodbye_message(canonical_act, speech_cb=speech_cb, dry_run=dry_run)

    # Step 5: Launch detached power command (if laptop action)
    launch_detached_power_command(canonical_act, delay_seconds=3, dry_run=dry_run)

    # Log to audit trail
    res = Result.success(
        data={"action": canonical_act, "goodbye": goodbye_msg, "dry_run": dry_run},
        verified=True,
        message=goodbye_msg,
    )
    audit_log(
        f"power_{canonical_act}",
        {"action": canonical_act, "confirmed": True, "dry_run": dry_run},
        Tier.TIER_2_CONFIRM,
        res,
        source="power",
        confirmed_by_user=True,
    )

    # Step 6: Exit R.O.S.E cleanly
    if not dry_run:
        logger.info("Exiting R.O.S.E process cleanly...")
        # Brief pause to let response flush to Gemini/UI before hard exit
        threading.Thread(
            target=lambda: (time.sleep(0.8), os._exit(0)),
            daemon=True,
        ).start()

    return res
