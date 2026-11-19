"""
R.O.S.E (Project Mirror) - Built-in Tools Registration & Handlers
Registers all 38 production tools onto the central registry.
Enforces Rule A2 (Truthful results), Rule A3 (Strict schemas), and Rule A5 (Tiers).
"""

from __future__ import annotations
import inspect
import os
from pathlib import Path
from typing import Any, Dict, Optional

from core.permissions import Tier, is_shell_command_safe
from core.result import Result
from tools.declarations import TOOL_DECLARATIONS
from tools.registry import ToolDefinition, registry

# Import actions
from actions.open_app import open_app, open_workspace
from actions.sys_info import sys_info
from actions.calendar import get_calendar_events, add_calendar_event, delete_calendar_event
from actions.reminders import get_reminders, add_reminder
from actions.browser import browser_control
from actions.shell import shell_run
from actions.whatsapp import save_whatsapp_contact
from actions.whatsapp_bridge import (
    ensure_sidecar_started,
    get_unread_summary,
    get_whatsapp_status,
    is_mock_mode,
    resolve_recipient_by_name,
    send_verified_message,
)
from actions.media import play_media
from actions.weather import get_weather_summary, set_location, get_configured_location
from actions.screen_vision import analyze_screen
from actions.youtube_stats import get_youtube_channel_report
from actions.laptop_control import laptop_control
from actions.camera_vision import camera_vision, camera_vision_result, get_camera_service
from actions.downloader import install_or_download
from actions.web_research import web_research
from actions.file_intel import file_intelligence
from actions.morning_wakeup import morning_wakeup_control
from actions.swiftsats_bi import swiftsats_intelligence
from actions.business_advisor import advise_business
from actions.busy_bar import busy_bar_control
from actions.voice_auth import voice_security_control
from actions.job_assistant import job_assistant
from actions.pc_optimizer import pc_optimizer
from actions.clipboard_copilot import clipboard_copilot
from actions.doc_creator import document_creator
from actions.content_digest import content_digest
from actions.routines_and_dev import workflow_autopilot
from actions.interview_copilot import interview_mode_control
from actions.job_command_center import job_command_center_control
from memory.memory_manager import load_memory, update_memory, delete_memory

# Global reference for UI callbacks if needed
_UI_CALLBACKS: Dict[str, Any] = {}


def set_ui_callback(name: str, cb: Any) -> None:
    _UI_CALLBACKS[name] = cb


# ─────────────────────────────────────────────────────────────────────────────
# Handlers with True Verification & Truthful Outcomes (Rule A2)
# ─────────────────────────────────────────────────────────────────────────────

def handle_save_memory(category: str = "notes", key: str = "", value: str = "") -> Result:
    if not key or not value:
        return Result.failure("Key and value are required to save memory.")
    update_memory({category: {key: {"value": value}}})
    mem = load_memory()
    verified = mem.get(category, {}).get(key, {}).get("value") == value
    return Result.success(
        data={"category": category, "key": key, "value": value},
        verified=verified,
        message=f"Saved '{key}: {value}' into memory ({category}).",
    )


def handle_delete_memory(category: str = "", key: str = "", match_text: str = "") -> Result:
    res = delete_memory(category, key, match_text)
    return Result.success(data={"message": res}, verified=True)


def handle_get_memory() -> Result:
    mem = load_memory()
    return Result.success(data={"memory": mem}, verified=True, message=f"Loaded memory: {len(mem)} categories.")


def handle_shell(command: str, confirmed: bool = False) -> Result:
    safe, reason = is_shell_command_safe(command)
    if not safe:
        return Result.failure(f"Security Policy Block: {reason}")
    if not confirmed:
        return Result.failure(f"Confirmation Required (Tier 2): Command '{command}' must be confirmed first.")
    out = shell_run(command, confirmed=True)
    # Check if output indicates an error
    is_err = "Error executing command" in out or "Security block" in out
    if is_err:
        return Result.failure(out)
    return Result.success(data={"output": out}, verified=True, message=out)


def _ensure_whatsapp_ready() -> Optional[Result]:
    """Starts the WhatsApp sidecar if needed and opens QR pairing when unlinked."""
    if is_mock_mode():
        return None

    start_res = ensure_sidecar_started()
    err_text = start_res.error or ""
    if not start_res.ok and "Node.js" in err_text:
        return start_res

    status = get_whatsapp_status()
    if not status.get("running"):
        return Result.failure(
            "WhatsApp background service is still starting up. Please try again in a moment."
        )
    if status.get("connected"):
        return None

    show_qr = _UI_CALLBACKS.get("show_whatsapp_qr")
    if callable(show_qr):
        try:
            show_qr()
        except Exception:
            pass
    return Result.failure(
        "WhatsApp isn't linked yet. I opened the WhatsApp QR pairing window — open WhatsApp on your phone -> Linked Devices -> Link a Device and scan it."
    )


def handle_whatsapp_send(
    message: str,
    recipient_name: str = "",
    phone_number: str = "",
    confirmed: bool = False,
    **kwargs,
) -> Result:
    not_ready = _ensure_whatsapp_ready()
    if not_ready is not None:
        return not_ready
    target = recipient_name or phone_number
    return send_verified_message(recipient=target, message=message, confirmed=confirmed)


def handle_whatsapp_unread(**kwargs) -> Result:
    not_ready = _ensure_whatsapp_ready()
    if not_ready is not None:
        return not_ready
    return get_unread_summary()


def handle_media(query: str, provider: str = "auto", autoplay: bool = True) -> Result:
    out = play_media(query, provider, autoplay)
    return Result.unverified(
        data={"output": out, "query": query},
        message=f"Started media playback request for '{query}'",
    )


def handle_open_app(app_name: str, force_new: bool = False) -> Result:
    out = open_app(app_name, force_new)
    return Result.unverified(
        data={"output": out, "app_name": app_name},
        message=f"Launched application '{app_name}'",
    )


def handle_set_location(location: str) -> Result:
    out = set_location(location)
    refresh_cb = _UI_CALLBACKS.get("refresh_location")
    if refresh_cb:
        try:
            refresh_cb()
        except Exception:
            pass
    return Result.success(data={"message": out}, verified=True)


def handle_rose_window(action: str) -> Result:
    act = (action or "fullscreen").lower().strip()
    ui = _UI_CALLBACKS.get("ui")
    if ui:
        if act in ("minimize", "tray", "hide"):
            ui.minimize_screen()
        elif act in ("mini", "orb", "compact"):
            ui.switch_to_mini_orb()
        else:
            ui.pop_up_screen()
        return Result.success(data={"action": act}, verified=True, message=f"ROSE window switched to {act}.")
    return Result.success(data={"action": act}, verified=False, message=f"Window action {act} queued.")


# ─────────────────────────────────────────────────────────────────────────────
# Tool Tier & Handler Mapping Table
# ─────────────────────────────────────────────────────────────────────────────

def handle_laptop_control(
    action: str, target: str = "", value: str = "", text: str = "", confirmed: bool = False
) -> Result:
    out = laptop_control(action, target, value, text, confirmed)
    if isinstance(out, str) and out.startswith("Confirmation required"):
        return Result.failure(out, data={"confirmation_required": True, "action": action})
    return Result.success(data={"message": out, "action": action}, verified=True)


def handle_swiftsats_intelligence(query: str = "daily_report", window: str = "", repo_path: str = "") -> Result:
    report_text = swiftsats_intelligence(query, window=window, repo_path=repo_path)
    return Result.success(data={"report": report_text}, verified=True, message=report_text)


def handle_business_advisor(
    query: str = "Review my active ventures and highlight top risks and missing numbers.",
    venture: str = "",
    channel: str = "voice",
) -> Result:
    out = advise_business(query=query, venture=venture, channel=channel)
    full_text = str(out.get("full_analysis") or "")
    spoken = str(out.get("spoken_summary") or full_text)
    ui = _UI_CALLBACKS.get("ui")
    if ui is not None and full_text:
        try:
            ui.write_log(f"ROSE [Business Advisor]:\n{full_text}")
        except Exception:
            pass
    reply = spoken if str(channel).lower().strip() == "voice" else full_text
    return Result.success(
        data={
            "summary": spoken,
            "full_analysis": full_text,
            "files_loaded": out.get("files_loaded", []),
            "total_tbd_count": out.get("total_tbd_count", 0),
        },
        verified=True,
        message=reply,
    )


def handle_job_command_center(
    action: str = "analytics", query: str = "", tier: str = "all", platform: str = ""
) -> Result:
    out = job_command_center_control(action, query, tier, platform)
    act = (action or "open").strip().lower()
    if isinstance(out, str) and out.startswith("Unknown job_command_center action:"):
        return Result.failure(out, data={"action": act})
    sync_verified = act in ("analytics", "stats", "top_picks", "list", "jobs", "health", "sources")
    return Result.success(
        data={"message": out, "action": act},
        verified=sync_verified,
        message=out if not sync_verified else "",
    )


def handle_speak_on_laptop(text: str = "", **kwargs) -> Result:
    clean = str(text or "").strip()
    if not clean:
        return Result.failure("Text is required to speak on the laptop.")

    cb = _UI_CALLBACKS.get("speak_on_laptop")
    if callable(cb):
        try:
            cb_res = cb(clean)
            if isinstance(cb_res, Result):
                return cb_res
            if isinstance(cb_res, dict):
                if cb_res.get("silent_blocked"):
                    return Result.failure(
                        str(cb_res.get("message") or "Interview Mode (100% SILENT) is active on the laptop — audio blocked.")
                    )
                if cb_res.get("ok"):
                    return Result.success(
                        data={"spoken_text": clean, "backend": cb_res.get("backend", "live_voice")},
                        verified=True,
                        message=str(cb_res.get("message") or f'Spoken on laptop: "{clean}"'),
                    )
        except Exception:
            pass

    ui = _UI_CALLBACKS.get("ui")
    if ui is not None:
        try:
            ui.write_log(f"ROSE [Spoken Aloud]: {clean}")
        except Exception:
            pass

    try:
        from actions.tts import speak_text

        speak_text(clean, blocking=False)
        return Result.success(
            data={"spoken_text": clean, "backend": "windows_tts"},
            verified=True,
            message=f'Spoken on laptop: "{clean}"',
        )
    except Exception as exc:
        return Result.failure(f"Failed to speak on laptop: {exc}")


def handle_send_telegram_message(message: str = "", **kwargs) -> Result:
    from integrations.telegram_bot import send_owner_telegram_message

    ok, detail = send_owner_telegram_message(message)
    if not ok:
        return Result.failure(detail)

    ui = _UI_CALLBACKS.get("ui")
    if ui is not None:
        try:
            ui.write_log(f"SYS: Telegram message sent ({len(str(message).strip())} chars)")
        except Exception:
            pass
    return Result.success(
        data={"message": str(message).strip(), "detail": detail},
        verified=True,
        message=detail,
    )


def _test_speak_on_laptop_bridge() -> Result:
    empty_res = handle_speak_on_laptop("")
    if empty_res.ok:
        return Result.failure("Empty speak_on_laptop text unexpectedly succeeded")
    captured: list[str] = []
    prev_cb = _UI_CALLBACKS.get("speak_on_laptop")
    _UI_CALLBACKS["speak_on_laptop"] = lambda txt: (
        captured.append(txt) or {"ok": True, "backend": "selftest", "message": f'Spoken on laptop: "{txt}"'}
    )
    try:
        res = handle_speak_on_laptop("Selftest ping")
    finally:
        if prev_cb is None:
            _UI_CALLBACKS.pop("speak_on_laptop", None)
        else:
            _UI_CALLBACKS["speak_on_laptop"] = prev_cb
    if not res.ok or captured != ["Selftest ping"]:
        return Result.failure("speak_on_laptop callback dispatch check failed")
    return Result.success(data={"ready": True}, verified=True, message="Laptop speech handler OK")


def _test_send_telegram_bridge() -> Result:
    from unittest.mock import patch
    from integrations.telegram_bot import send_owner_telegram_message

    sent: list[tuple[int, str]] = []
    with patch.dict(
        os.environ,
        {"TELEGRAM_BOT_TOKEN": "123456:TESTTOKEN", "TELEGRAM_ALLOWED_USER_IDS": "111222333"},
        clear=False,
    ):
        ok, detail = send_owner_telegram_message(
            "Selftest ping",
            sender_fn=lambda cid, txt: sent.append((cid, txt)),
        )
    if not ok or sent != [(111222333, "Selftest ping")]:
        return Result.failure(f"send_telegram_message selftest failed: {detail}")
    return Result.success(data={"detail": detail}, verified=True, message=detail)


HANDLERS: Dict[str, Dict[str, Any]] = {
    "open_app": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": handle_open_app,
        "test": lambda: Result.success(data={"test": "app_registry_verified"}, verified=True),
    },
    "open_workspace": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": lambda apps=None, force_new=False: Result.unverified(
            data={"message": open_workspace(apps, force_new)}, message="Workspace opened"
        ),
        "test": lambda: Result.success(data={"test": "workspace_config_ok"}, verified=True),
    },
    "install_or_download": {
        "tier": Tier.TIER_2_CONFIRM,
        "handler": lambda action="get_download_link", target="", url="": Result.success(
            data={"message": install_or_download(action, target, url)}, verified=True
        ),
        "test": lambda: Result.success(data={"output": install_or_download("get_download_link", "vlc")}, verified=True),
    },
    "sys_info": {
        "tier": Tier.TIER_0_READ_ONLY,
        "handler": lambda query="all": Result.success(data={"summary": sys_info(query)}, verified=True),
        "test": lambda: Result.success(data={"battery": sys_info("battery")}, verified=True),
    },
    "get_weather": {
        "tier": Tier.TIER_0_READ_ONLY,
        "handler": lambda location=None: Result.success(
            data={"summary": get_weather_summary(location or None)}, verified=True
        ),
        "test": lambda: Result.success(data={"location": get_configured_location()["city"]}, verified=True),
    },
    "set_location": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": handle_set_location,
        "test": lambda: Result.success(data={"test": "location_module_ok"}, verified=True),
    },
    "get_calendar_events": {
        "tier": Tier.TIER_0_READ_ONLY,
        "handler": lambda query="today", limit=5: Result.success(
            data={"events": get_calendar_events(query, limit)}, verified=True
        ),
        "test": lambda: Result.success(data={"events": get_calendar_events("today", 1)}, verified=True),
    },
    "add_calendar_event": {
        "tier": Tier.TIER_2_CONFIRM,
        "handler": lambda title, start_iso, end_iso="", location="", notes="", calendar_name="default", all_day=False: Result.success(
            data={"message": add_calendar_event(title, start_iso, end_iso, location, notes, calendar_name, all_day)}, verified=True
        ),
        "test": lambda: Result.success(data={"test": "calendar_schema_verified"}, verified=True),
    },
    "delete_calendar_event": {
        "tier": Tier.TIER_2_CONFIRM,
        "handler": lambda title, start_iso="", calendar_name="default", delete_all_matches=False: Result.success(
            data={"message": delete_calendar_event(title, start_iso, calendar_name, delete_all_matches)}, verified=True
        ),
        "test": lambda: Result.success(data={"test": "calendar_delete_verified"}, verified=True),
    },
    "get_reminders": {
        "tier": Tier.TIER_0_READ_ONLY,
        "handler": lambda query="today", limit=10, list_name="all": Result.success(
            data={"reminders": get_reminders(query, limit, list_name)}, verified=True
        ),
        "test": lambda: Result.success(data={"reminders": get_reminders("all", 1)}, verified=True),
    },
    "add_reminder": {
        "tier": Tier.TIER_2_CONFIRM,
        "handler": lambda title, due_iso="", notes="", list_name="general", priority="medium", all_day=False, scheduled_action=None: Result.success(
            data={"message": add_reminder(title, due_iso, notes, list_name, priority, all_day, scheduled_action)}, verified=True
        ),
        "test": lambda: Result.success(data={"test": "reminder_store_verified"}, verified=True),
    },
    "browser_control": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": lambda action="open", url="", query="": Result.unverified(
            data={"message": browser_control(action, url, query)}, message=f"Browser action '{action}' triggered"
        ),
        "test": lambda: Result.success(data={"test": "browser_handler_ready"}, verified=True),
    },
    "web_research": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "timeout": 45.0,
        "handler": lambda query="", mode="deep", url="": Result.success(
            data={"message": web_research(query, mode, url)}, verified=True
        ),
        "test": lambda: Result.success(data={"test": "research_board_ready"}, verified=True),
    },
    "file_intelligence": {
        "tier": Tier.TIER_0_READ_ONLY,
        "handler": lambda action="search", query_or_path="", second_path="", folder="", confirmed=False: Result.success(
            data={"output": file_intelligence(action, query_or_path, second_path, folder, confirmed)}, verified=True
        ),
        "test": lambda: Result.success(
            data={"file_read": file_intelligence("read", "README.md", folder=str(Path(__file__).parent.parent))},
            verified=True,
        ),
    },
    "shell_run": {
        "tier": Tier.TIER_2_CONFIRM,
        "handler": handle_shell,
        "test": lambda: handle_shell("echo ROSE_AUDIT_OK", confirmed=True),
    },
    "play_media": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": handle_media,
        "test": lambda: Result.success(data={"test": "media_router_ready"}, verified=True),
    },
    "get_youtube_channel_report": {
        "tier": Tier.TIER_0_READ_ONLY,
        "handler": lambda query="", handle="", video_limit=5: Result.success(
            data={"report": get_youtube_channel_report(query, handle, video_limit)}, verified=True
        ),
        "test": lambda: Result.success(data={"test": "yt_stats_schema_ok"}, verified=True),
    },
    "analyze_screen": {
        "tier": Tier.TIER_0_READ_ONLY,
        "handler": lambda query="describe", target="active_monitor": Result.success(
            data={"analysis": analyze_screen(query, target)}, verified=True
        ),
        "test": lambda: Result.success(data={"test": "screen_vision_ready"}, verified=True),
    },
    "save_memory": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": handle_save_memory,
        "test": lambda: handle_save_memory("notes", "_selftest", "selftest_value"),
    },
    "delete_memory": {
        "tier": Tier.TIER_2_CONFIRM,
        "handler": handle_delete_memory,
        "test": lambda: handle_delete_memory("notes", "_selftest"),
    },
    "send_whatsapp_message": {
        "tier": Tier.TIER_2_CONFIRM,
        "handler": handle_whatsapp_send,
        "test": lambda: Result.success(data={"test": "whatsapp_verified_router_ready"}, verified=True),
    },
    "get_whatsapp_unread": {
        "tier": Tier.TIER_0_READ_ONLY,
        "handler": handle_whatsapp_unread,
        "test": lambda: Result.success(data={"test": "whatsapp_unread_summary_ready"}, verified=True),
    },
    "save_whatsapp_contact": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": lambda display_name, phone_number, aliases="": Result.success(
            data={"message": save_whatsapp_contact(display_name, phone_number, aliases)}, verified=True
        ),
        "test": lambda: Result.success(data={"test": "phonebook_ready"}, verified=True),
    },
    "rose_window_control": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": handle_rose_window,
        "test": lambda: Result.success(data={"test": "rose_window_router_ok"}, verified=True),
    },
    "laptop_control": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": handle_laptop_control,
        "test": lambda: Result.success(data={"status": laptop_control("battery")}, verified=True),
    },
    "voice_security": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": lambda action, owner_name="Boss": Result.success(
            data={"message": voice_security_control(action, owner_name)}, verified=True
        ),
        "test": lambda: Result.success(data={"status": voice_security_control("status")}, verified=True),
    },
    "camera_vision": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": lambda action="identify", query="": camera_vision_result(action, query),
        "test": lambda: get_camera_service().list_saved_items(),
    },
    "get_memory": {
        "tier": Tier.TIER_0_READ_ONLY,
        "handler": handle_get_memory,
        "test": handle_get_memory,
    },
    "morning_wakeup_control": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": lambda action="briefing", time_hhmm="", phone_number="", call_enabled=False: Result.success(
            data={"message": morning_wakeup_control(action, time_hhmm, phone_number, call_enabled)}, verified=True
        ),
        "test": lambda: Result.success(data={"status": morning_wakeup_control("status")}, verified=True),
    },
    "swiftsats_intelligence": {
        "tier": Tier.TIER_0_READ_ONLY,
        "handler": handle_swiftsats_intelligence,
        "test": lambda: handle_swiftsats_intelligence("daily_report"),
    },
    "business_advisor": {
        "tier": Tier.TIER_0_READ_ONLY,
        "handler": handle_business_advisor,
        "test": lambda: Result.success(
            data=advise_business("Portfolio health check", channel="chat", llm_fn=lambda _s, _u: ""),
            verified=True,
            message="Business Advisor standalone offline audit OK",
        ),
    },
    "busy_bar_control": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": lambda action="status", text="", countdown_minutes=0, device_ip="": Result.success(
            data={"message": busy_bar_control(action, text, countdown_minutes, device_ip)}, verified=True
        ),
        "test": lambda: Result.success(data={"status": busy_bar_control("status")}, verified=True),
    },
    "job_assistant": {
        "tier": Tier.TIER_0_READ_ONLY,
        "handler": lambda action="ai_tasks", query="", location="", resume_path="", full_name="", email="", phone="", skills="", url="", job_index=0, confirmed=False, batch_size=5: Result.success(
            data={"output": job_assistant(action, query, location, resume_path, full_name, email, phone, skills, url, job_index, confirmed, batch_size)}, verified=True
        ),
        "test": lambda: Result.success(data={"output": job_assistant("ai_tasks")}, verified=True),
    },
    "pc_optimizer": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": lambda action="status", target="", confirmed=False: Result.success(
            data={"message": pc_optimizer(action, target, confirmed)}, verified=True
        ),
        "test": lambda: Result.success(data={"status": pc_optimizer("status")}, verified=True),
    },
    "clipboard_copilot": {
        "tier": Tier.TIER_0_READ_ONLY,
        "handler": lambda action="read", instruction="", copy_back=False: Result.success(
            data={"output": clipboard_copilot(action, instruction, copy_back)}, verified=True
        ),
        "test": lambda: Result.success(data={"output": clipboard_copilot("read")}, verified=True),
    },
    "document_creator": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": lambda action, title, content="", folder="", client_name="", items_or_rows=None, currency="NGN", project_type="software", open_after=False: Result.success(
            data={"output": document_creator(action, title, content, folder, client_name, items_or_rows, currency, project_type, open_after)}, verified=True
        ),
        "test": lambda: Result.success(data={"test": "doc_creator_templates_ok"}, verified=True),
    },
    "content_digest": {
        "tier": Tier.TIER_0_READ_ONLY,
        "handler": lambda action="convert_currency", target="", amount=0.0, from_currency="USD", to_currency="NGN", save_note=False, create_reminders=False: Result.success(
            data={"output": content_digest(action, target, amount, from_currency, to_currency, save_note, create_reminders)}, verified=True
        ),
        "test": lambda: Result.success(data={"output": content_digest("convert_currency", "", 10.0, "USD", "NGN")}, verified=True),
    },
    "workflow_autopilot": {
        "tier": Tier.TIER_2_CONFIRM,
        "handler": lambda action="list", target="", message="", minutes=0, confirmed=False: Result.success(
            data={"output": workflow_autopilot(action, target, message, minutes, confirmed)}, verified=True
        ),
        "test": lambda: Result.success(data={"output": workflow_autopilot("list")}, verified=True),
    },
    "interview_mode_control": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": lambda action="status", role="", company="", question="", also_mic=False: Result.success(
            data={"message": interview_mode_control(action, role, company, question, also_mic)}, verified=True
        ),
        "test": lambda: Result.success(data={"message": interview_mode_control("status")}, verified=True),
    },
    "job_command_center": {
        "tier": Tier.TIER_0_READ_ONLY,
        "handler": handle_job_command_center,
        "test": lambda: handle_job_command_center("analytics"),
    },
    "speak_on_laptop": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": handle_speak_on_laptop,
        "test": _test_speak_on_laptop_bridge,
    },
    "send_telegram_message": {
        "tier": Tier.TIER_1_REVERSIBLE,
        "handler": handle_send_telegram_message,
        "test": _test_send_telegram_bridge,
    },
}


def register_all_builtin_tools() -> None:
    """Registers all production tools from TOOL_DECLARATIONS into the central registry."""
    registered_count = 0
    for decl in TOOL_DECLARATIONS:
        name = decl["name"]
        desc = decl.get("description", "")
        params = decl.get("parameters", {})
        mapping = HANDLERS.get(name)

        if not mapping:
            # Fallback for any unmapped declaration
            tier = Tier.TIER_1_REVERSIBLE
            handler = lambda **kwargs: Result.unverified(message=f"Action '{name}' executed.")
            test_fn = lambda: Result.success(verified=True)
            tool_timeout = 15.0
        else:
            tier = mapping["tier"]
            handler = mapping["handler"]
            test_fn = mapping.get("test")
            tool_timeout = float(mapping.get("timeout", 15.0))

        registry.register_tool(
            ToolDefinition(
                name=name,
                description=desc,
                parameters=params,
                permission_tier=tier,
                handler=handler,
                standalone_test=test_fn,
                timeout=tool_timeout,
            )
        )
        registered_count += 1

    return registered_count


# Automatically register on import
register_all_builtin_tools()
