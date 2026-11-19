#!/usr/bin/env python3
"""
R.O.S.E (PROJECT MIRROR) — Real-Time Voice, Vision & Autonomous Desktop AI Assistant for Windows
"""

import asyncio
import datetime
import queue
import threading
import time
import traceback
import os
import re
import sys
import warnings
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _kill_other_instances(dry_run: bool = False) -> list[int]:
    """Ensures no older R.O.S.E main.py process is holding the Intel Smart Sound microphone."""
    my_pid = os.getpid()
    my_ppid = os.getppid()
    base_dir_norm = os.path.normcase(str(BASE_DIR.resolve()))
    matched: list[int] = []
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
            try:
                pid = proc.info["pid"]
                if pid in (my_pid, my_ppid):
                    continue
                name = (proc.info.get("name") or "").lower()
                if "python" not in name:
                    continue
                args = proc.info.get("cmdline") or []
                is_main = any(
                    os.path.basename(str(a).strip("\"'")).lower() == "main.py"
                    for a in args[1:]
                )
                if not is_main:
                    continue
                cwd = os.path.normcase(str(proc.info.get("cwd") or ""))
                joined = " ".join(str(a).lower() for a in args)
                if cwd == base_dir_norm or "jarvis-windows" in joined:
                    matched.append(pid)
                    if not dry_run:
                        proc.kill()
            except Exception:
                continue
    except Exception:
        pass
    return matched


if "--kill-old" in sys.argv:
    _pids = _kill_other_instances(dry_run="--dry-run" in sys.argv)
    if "--dry-run" in sys.argv:
        print(f"MATCH: {_pids}")
    sys.exit(0)

warnings.filterwarnings("ignore", category=DeprecationWarning, module=".*")
import audioop  # noqa: E402
import struct  # noqa: E402
import numpy as np  # noqa: E402
import pyaudio  # type: ignore[reportMissingModuleSource]
from google import genai  # type: ignore[reportMissingImports]
from google.genai import types  # type: ignore[reportMissingImports]

from app_config import get_app_config_value, get_backend
from ui import JarvisUI
from memory.memory_manager import (
    load_memory,
    update_memory,
    delete_memory,
    get_memory_summary,
    format_memory_for_prompt,
)
from actions.open_app import open_app, open_workspace
from actions.sys_info import sys_info
from actions.calendar import get_calendar_events, add_calendar_event, delete_calendar_event
from actions.reminders import get_reminders, add_reminder, pop_due_reminders
from actions.browser import browser_control
from actions.shell import shell_run
from actions.whatsapp import send_whatsapp_message, save_whatsapp_contact
from actions.media import play_media
from actions.weather import (
    get_weather_summary,
    set_location,
    get_configured_location,
    get_location_datetime,
)
from actions.screen_vision import analyze_screen
from actions.youtube_stats import get_youtube_channel_report
from actions.laptop_control import laptop_control
from actions.camera_vision import init_camera_controller, camera_vision
from actions.downloader import install_or_download
from actions.web_research import init_research_board, web_research
from actions.file_intel import file_intelligence
from actions.morning_wakeup import morning_wakeup_control, check_and_pop_morning_wakeup
from actions.swiftsats_bi import swiftsats_intelligence
from actions.busy_bar import init_busy_bar, busy_bar_control
from actions.voice_auth import voice_monitor, voice_security_control
from actions.job_assistant import job_assistant
from actions.pc_optimizer import pc_optimizer
from actions.clipboard_copilot import clipboard_copilot
from actions.doc_creator import document_creator
from actions.content_digest import content_digest
from actions.routines_and_dev import workflow_autopilot
from actions.interview_copilot import init_interview_copilot, interview_mode_control
from actions.job_command_center import init_job_command_center, job_command_center_control

from tools.declarations import PROTECTED_PERSONAL_TOOLS, TOOL_DECLARATIONS
from tools.registry import registry
from tools.builtins import set_ui_callback
from core.result import Result

# ── Paths ───────────────────────────────────────────────────────────────────
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"

CONTROL_TOKEN_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

# ── Model ───────────────────────────────────────────────────────────────────
LIVE_MODEL = "models/gemini-2.5-flash-native-audio-latest"

# ── Audio ───────────────────────────────────────────────────────────────────
FORMAT              = pyaudio.paInt16
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECV_SAMPLE_RATE    = 24000
SPEAKER_OUTPUT_RATE = 48000
CHUNK_SIZE          = 320
pya                 = pyaudio.PyAudio()



def get_api_key() -> str:
    return str(get_app_config_value("gemini_api_key", "") or "")


def load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are ROSE (JARVIS) — a personal AI assistant running on Windows in Lagos, Nigeria. "
            "Always speak ONLY in English. You understand both Standard English and Nigerian Pidgin (Broken English). "
            "Always use your tools to complete tasks on the laptop."
        )


class JarvisLive:
    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = queue.Queue()
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self._audio_threads_started = False
        self._last_text_input_ts = 0.0
        self._ring_buf = bytearray()
        self._ring_lock = threading.Lock()
        self._ring_prebuffering = True
        self._ring_recovering = False
        self._ring_need_fade_in = True
        self._ring_first_ts = 0.0
        self._ring_last_ts = 0.0
        self._ring_last_played_ts = 0.0
        self._ring_active = False
        self._mic_echo_until = 0.0
        self._gemini_turn_active = False
        self._gemini_turn_last_audio_ts = 0.0
        self._last_popup_ts = 0.0

        self.ui.on_text_command  = self._on_text_command
        self.ui.on_pause_toggle  = self._on_pause_toggle
        self.ui.on_effects_state_change = self._on_effects_state_change
        self.ui.on_interview_toggle = self._on_interview_toggle
        self.ui.on_jobs_toggle = self._on_jobs_toggle
        self._paused             = False
        self._interview_mode     = False
        self._business_advisor_mode = False
        self._last_voice_interview_trigger_ts = 0.0
        self._executing_speak_tool = False
        self._shutdown_event     = threading.Event()
        self.ui.on_shutdown      = self._stop_audio_threads
        set_ui_callback("speak_on_laptop", self.speak_on_laptop)
        init_interview_copilot(self.ui.root, self.ui.write_log, self._set_interview_mode)
        init_job_command_center(self.ui.root, self.ui.write_log)
        # Pre-warm hardware audio streams immediately in background so launch has ZERO lag or crackling
        self._ensure_audio_threads_started()

    def _stop_audio_threads(self):
        self._shutdown_event.set()
        self._gemini_turn_active = False
        self._clear_speaker_buffer()
        with self._speaking_lock:
            self._is_speaking = False
        try:
            from integrations.telegram_bot import stop_telegram_bot
            stop_telegram_bot()
        except Exception:
            pass
        try:
            from actions.whatsapp_bridge import stop_sidecar
            stop_sidecar()
        except Exception:
            pass

    def speak_on_laptop(self, text: str) -> dict:
        """Speaks text/answer out loud on the laptop via active Gemini Live session or Windows TTS fallback."""
        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return {"ok": False, "message": "Empty text cannot be spoken."}
        if self._interview_mode:
            self.ui.write_log(f"SYS: 📱 [Interview SILENT] Telegram speech: {clean}")
            return {
                "ok": False,
                "silent_blocked": True,
                "message": "Interview Mode (100% SILENT) is active on the laptop — displayed on HUD without speaking aloud.",
            }
        if self._executing_speak_tool:
            return {"ok": True, "backend": "live_voice_turn", "message": f'Spoken on laptop: "{clean}"'}

        # Wait briefly if Gemini Live is in the middle of a short idle refresh
        if not self._paused and not self._shutdown_event.is_set() and (not self.session or not self._loop):
            for _ in range(8):
                if (self.session and self._loop) or self._shutdown_event.is_set() or self._paused:
                    break
                time.sleep(0.15)

        if self.session and self._loop and not self._paused and not self._shutdown_event.is_set():
            try:
                self.ui.pop_up_screen()
                self.ui.write_log("SYS: 📱 Telegram -> Speaking answer on laptop...")
                prompt_msg = (
                    "[REMOTE TELEGRAM REQUEST — SPEAK OUT LOUD ON LAPTOP NOW]\n"
                    "Speak the following statement/answer clearly and warmly out loud in English to the user right now, "
                    "keeping all facts accurate without adding unnecessary meta-commentary:\n\n"
                    f"{clean}"
                )
                fut = asyncio.run_coroutine_threadsafe(
                    self.session.send_client_content(
                        turns={"parts": [{"text": prompt_msg}]},
                        turn_complete=True,
                    ),
                    self._loop,
                )
                fut.result(timeout=4.0)
                return {
                    "ok": True,
                    "backend": "gemini_live",
                    "message": f'Spoken on laptop: "{clean}"',
                }
            except Exception as exc:
                print(f"[ROSE] Telegram->Live speech fallback to Windows TTS: {exc}")

        try:
            from actions.tts import speak_text

            self.ui.pop_up_screen()
            self.ui.write_log(f"ROSE [Spoken Aloud]: {clean}")
            est_dur = min(25.0, max(2.0, len(clean) * 0.08))
            self._mic_echo_until = max(self._mic_echo_until, time.perf_counter() + est_dur)
            self.set_speaking(True)

            def _on_tts_done():
                self._mic_echo_until = time.perf_counter() + 0.6
                self.set_speaking(False)

            speak_text(clean, on_done=_on_tts_done, blocking=False)
            return {
                "ok": True,
                "backend": "windows_tts",
                "message": f'Spoken on laptop: "{clean}"',
            }
        except Exception as exc:
            self.set_speaking(False)
            return {"ok": False, "message": f"Windows TTS failed: {exc}"}

    def _on_interview_toggle(self):
        msg = interview_mode_control("toggle")
        self.ui.write_log(f"SYS: {msg}")

    def _on_jobs_toggle(self):
        msg = job_command_center_control("open")
        self.ui.write_log(f"SYS: {msg}")

    def _set_interview_mode(self, active: bool):
        self._interview_mode = bool(active)
        self.ui.set_interview_mode_active(self._interview_mode)
        if self._interview_mode:
            self._clear_speaker_buffer()
            self.set_speaking(False)
            self.ui.write_log("SYS: 🎧 INTERVIEW MODE ON (100% SILENT — Listening to Laptop Audio)")
        else:
            self.ui.write_log("SYS: 🎙 INTERVIEW MODE OFF — Normal voice mode restored.")

    def _on_pause_toggle(self, paused: bool):
        self._paused = paused

    def _on_effects_state_change(self, enabled: bool):
        pass

    def _focus_ui_section_for_tool(self, tool_name: str, args: dict):
        if tool_name == "sys_info":
            query = str(args.get("query", "")).strip().lower()
            if query in {"time", "date"}:
                self.ui.focus_panel("time", duration_ms=5200)
            else:
                self.ui.focus_panel("system", duration_ms=5200)
        elif tool_name == "get_weather":
            self.ui.focus_panel("weather", duration_ms=5600)
        elif tool_name == "camera_vision":
            self.ui.focus_panel("camera", duration_ms=7500)

    @staticmethod
    def _extract_interview_start_role(text: str) -> tuple[bool, str]:
        low = " ".join(str(text or "").lower().split())
        if not low:
            return False, ""
        stop_markers = ("exit interview", "stop interview", "end interview", "leave interview", "turn off interview", "interview mode off", "disable interview")
        if any(m in low for m in stop_markers):
            return False, ""
        triggers = (
            "interview mode",
            "intaview mode",
            "inter view mode",
            "in the view mode",
            "into view mode",
            "switch to interview",
            "start interview",
            "open interview",
            "launch interview",
            "enable interview",
            "turn on interview",
            "enter interview",
        )
        matched = next((t for t in triggers if t in low), "")
        if not matched:
            return False, ""
        idx = low.find(matched) + len(matched)
        trailing = text.strip()[idx:].strip(" :-,.")
        for prefix in ("for ", "as ", "role "):
            if trailing.lower().startswith(prefix):
                trailing = trailing[len(prefix):].strip()
        return True, trailing

    def _on_text_command(self, text: str):
        if self._paused:
            return
        self._last_text_input_ts = time.time()
        self.ui.write_log(f"You: {text}")
        low = " ".join(text.strip().lower().split())
        if low in ("interview status", "check interview mode"):
            self.ui.write_log(f"ROSE: {interview_mode_control('status')}")
            return
        if self._interview_mode:
            if any(p in low for p in ("exit interview", "stop interview", "end interview", "normal mode", "interview mode off", "leave interview", "turn off interview", "disable interview")):
                msg = interview_mode_control("stop")
                self.ui.write_log(f"ROSE: {msg}")
                return
            if low in ("status", "interview status"):
                self.ui.write_log(f"ROSE: {interview_mode_control('status')}")
                return
            if low in ("screen", "read screen", "solve screen", "read my screen"):
                res = interview_mode_control("screen")
                self.ui.write_log(f"SYS: {res}")
                return
            start_hit, role_hint = self._extract_interview_start_role(text)
            if start_hit:
                msg = interview_mode_control("start", role=role_hint)
                self.ui.write_log(f"SYS: {msg}")
                return
            # In interview mode, treat typed questions as silent Teleprompter prompts immediately
            res = interview_mode_control("ask", question=text)
            self.ui.write_log(f"SYS: {res}")
            return
        start_hit, role_hint = self._extract_interview_start_role(text)
        if start_hit:
            msg = interview_mode_control("start", role=role_hint)
            self.ui.write_log(f"SYS: {msg}")
            return
        # Phase 7: Business Advisor Mode & SwiftSats on-demand reports in text chat panel
        if any(p in low for p in ("exit business advisor", "stop business advisor", "leave business advisor", "business advisor off")):
            self._business_advisor_mode = False
            self.ui.write_log("SYS: Business Advisor mode exited.")
            return
        if low in ("business advisor", "business advisor mode", "advisor mode", "/advisor"):
            self._business_advisor_mode = True
            self.ui.write_log("SYS: Business Advisor mode ACTIVE (candid & analytical). Ask about any venture or strategy.")
            return
        if (
            self._business_advisor_mode
            or low.startswith("/advise")
            or low.startswith("business advisor:")
            or low.startswith("advisor:")
            or "ask business advisor" in low
        ):
            from actions.business_advisor import advise_business

            q = re.sub(r"^(?:/advise|business\s+advisor\s*:|advisor\s*:|ask\s+business\s+advisor\s*)", "", text.strip(), flags=re.IGNORECASE).strip()
            res = advise_business(query=q or "Review my ventures, weak assumptions, risks, and missing numbers.", channel="chat")
            self.ui.write_log(f"ROSE [Business Advisor]:\n{res['full_analysis']}")
            return
        if low.startswith("/report") or any(p in low for p in ("swiftsats report", "how many logins today", "swiftsats security check")):
            from actions.swiftsats_bi import swiftsats_intelligence

            q = text.strip()[len("/report"):].strip() if low.startswith("/report") else text.strip()
            self.ui.write_log(f"ROSE [SwiftSats]:\n{swiftsats_intelligence(q or 'daily_report')}")
            return
        if any(p in low for p in ("job radar", "job tracker", "job command center", "open jobs", "show jobs", "daily jobs", "track jobs", "search international jobs", "search jobs", "open job radar", "open job tracker", "find jobs", "find gigs")):
            res = job_command_center_control("open")
            self.ui.write_log(f"SYS: {res}")
            return
        if any(p in low for p in ("autofill", "install autofill", "autofill form", "autofill application")):
            from actions.autofill_copilot import open_installer_in_chrome
            res = open_installer_in_chrome()
            self.ui.write_log(f"SYS: {res}")
            return
        if "twitter" in low and ("job" in low or "gig" in low or "hiring" in low):
            from actions.job_command_center import launch_live_chrome_search
            res = launch_live_chrome_search("twitter", text)
            self.ui.write_log(f"SYS: {res}")
            return
        if "upwork" in low and ("job" in low or "gig" in low or "search" in low):
            from actions.job_command_center import launch_live_chrome_search
            res = launch_live_chrome_search("upwork", text)
            self.ui.write_log(f"SYS: {res}")
            return
        if "google" in low and ("job" in low or "ats" in low or "search" in low):
            from actions.job_command_center import launch_live_chrome_search
            res = launch_live_chrome_search("google", text)
            self.ui.write_log(f"SYS: {res}")
            return
        if "reddit" in low and ("job" in low or "gig" in low or "hiring" in low):
            from actions.job_command_center import launch_live_chrome_search
            res = launch_live_chrome_search("reddit", text)
            self.ui.write_log(f"SYS: {res}")
            return
        if not self._loop or not self.session:
            self.ui.write_log("ERR: ROSE connection is not ready yet.")
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    async def _interrupt_audio(self):
        try:
            self._clear_speaker_buffer()
            if self.session:
                await self.session.send_realtime_input(audio_stream_end=True)
            self.set_speaking(False)
        except Exception:
            pass

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            if self._is_speaking == value:
                return
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        else:
            self.ui.set_state("LISTENING")

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.ui.write_debug(f"{tool_name}: {short}", level="ERROR")
        self.ui.set_state("ERROR")

    @staticmethod
    def _result_looks_like_error(result) -> bool:
        text = str(result or "").strip().lower()
        if not text:
            return False
        error_markers = (
            "error:",
            "failed to",
            "could not",
            "security block:",
            "access denied",
        )
        return any(marker in text for marker in error_markers)

    @staticmethod
    def _should_play_success_sfx(tool_name: str, args: dict, result) -> bool:
        action_tools = {
            "open_app",
            "open_workspace",
            "install_or_download",
            "add_calendar_event",
            "add_reminder",
            "delete_calendar_event",
            "morning_wakeup_control",
            "document_creator",
            "workflow_autopilot",
            "pc_optimizer",
        }
        if tool_name in action_tools:
            return True

        if tool_name == "send_whatsapp_message":
            text = str(result or "").lower()
            if bool(args.get("send_now", False)):
                return "sent" in text
            return False

        return False

    @staticmethod
    def _clean_transcript_text(text: str) -> tuple[str, bool]:
        raw = str(text or "")
        had_noise = False
        if CONTROL_TOKEN_RE.search(raw):
            had_noise = True
            raw = CONTROL_TOKEN_RE.sub(" ", raw)
        cleaned = []
        for ch in raw:
            if ch in "\n\r\t" or ord(ch) >= 32:
                cleaned.append(ch)
            else:
                had_noise = True
        normalized = " ".join("".join(cleaned).split())
        return normalized.strip(), had_noise

    def _build_config(self) -> types.LiveConnectConfig:
        memory  = load_memory()
        mem_str = format_memory_for_prompt(memory)
        sys_p   = load_system_prompt()
        loc     = get_configured_location()
        now     = get_location_datetime(loc)
        time_ctx = (
            f"[CURRENT LOCATION & TIME — {loc['display'].upper()} ({loc['timezone']})]\n"
            f"{now.strftime('%A, %d %B %Y — %I:%M %p (%H:%M)')}\n\n"
        )

        parts = [time_ctx]
        if voice_monitor.is_enrolled():
            parts.append(
                "[VOICE BIOMETRIC SECURITY: ACTIVE]\n"
                "The boss's voiceprint is stored and locked. Do NOT reveal any saved personal notes, passwords, "
                "identity info, schedule, reminders, files, WhatsApp messages, or business stats without first verifying "
                "the speaker's live voice via `voice_security(action='verify')` or the protected tool.\n"
                "If voice verification fails or is denied, refuse immediately and say: 'You are not the boss here.'\n\n"
            )
        elif mem_str:
            parts.append(mem_str + "\n\n")
        parts.append(sys_p)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                    prefix_padding_ms=240,
                    silence_duration_ms=850,
                )
            ),
            speech_config=types.SpeechConfig(
                language_code="en-US",
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=str(get_app_config_value("voice", "Aoede") or "Aoede")
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})
        print(f"[ROSE] 🔧 {name} {args}")
        self.ui.set_state("THINKING")

        loop = asyncio.get_event_loop()
        voice_cmd = (time.time() - self._last_text_input_ts) > 15.0

        # Section highlight on HUD
        self._focus_ui_section_for_tool(name, args)

        # Acoustic owner voiceprint gate for personal/private tools
        if name in PROTECTED_PERSONAL_TOOLS and voice_cmd and voice_monitor.is_enrolled():
            vres = await loop.run_in_executor(None, voice_monitor.verify_recent_speaker)
            if not vres.get("verified", False):
                self.ui.write_log(
                    f"ERR: Voice security rejected '{name}' (score={vres.get('score', 0):.2f})."
                )
                self.ui.set_state("ERROR")
                return types.FunctionResponse(
                    id=fc.id,
                    name=name,
                    response={
                        "result": (
                            "ACCESS DENIED (SECURITY BLOCK: unrecognized speaker voice — "
                            f"score={vres.get('score', 0):.2f}). Do NOT reveal any personal information. "
                            "Reject them firmly right now and say: 'You are not the boss here.'"
                        )
                    },
                )

        # Connect UI hooks for tools requiring direct window control
        set_ui_callback("ui", self.ui)
        set_ui_callback("refresh_location", self.ui.refresh_location)
        set_ui_callback("show_whatsapp_qr", self.ui.show_whatsapp_qr)
        set_ui_callback("speak_on_laptop", self.speak_on_laptop)

        # Execute through Central Registry (Rule A2, A3, A5)
        self._executing_speak_tool = (name == "speak_on_laptop")
        try:
            tool_res: Result = await registry.async_execute(
                name=name,
                args=args,
                confirmed=bool(args.get("confirmed", False)),
                source="voice" if voice_cmd else "ui",
                voice_verified=True,
            )
        finally:
            self._executing_speak_tool = False

        result_str = tool_res.to_user_message()
        tool_failed = not tool_res.ok

        if tool_failed:
            self.ui.set_state("ERROR")
        elif not self._interview_mode and self._should_play_success_sfx(name, args, result_str):
            self.ui.play_success_sfx()

        if not tool_failed and not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[ROSE] 📤 {name} → {str(result_str)[:80]}")
        return types.FunctionResponse(
            id=fc.id,
            name=name,
            response={"result": f"{result_str} (Always reply to the user in polished, professional English)"},
        )

    def _drain_audio_in_queue(self):
        if self.audio_in_queue is None:
            return
        while not self.audio_in_queue.empty():
            try:
                self.audio_in_queue.get_nowait()
            except Exception:
                break

    def _clear_speaker_buffer(self):
        self._drain_audio_in_queue()
        with self._ring_lock:
            self._ring_buf.clear()
            self._ring_prebuffering = True
            self._ring_recovering = False
            self._ring_need_fade_in = True
            self._ring_first_ts = 0.0
            self._ring_last_played_ts = 0.0
            self._ring_active = False
        self._mic_echo_until = 0.0

    def _pop_speaker_callback_frame(
        self,
        need: int,
        frame_count: int,
        out_ch: int,
        prebuf_bytes: int,
        now_cb: float,
    ) -> bytes:
        """
        Pulls up to `need` PCM bytes for the speaker callback without dropping buffered speech.
        Re-arms a recovery cushion after network stalls so delayed chunks merge instead of
        stuttering, ignores 1-2 frame resampler rounding shortfalls, and extends `_mic_echo_until`.
        """
        frame_bytes = 2 * max(1, out_ch)
        with self._ring_lock:
            avail = len(self._ring_buf)
            if avail < frame_bytes:
                if self._ring_active and not self._ring_prebuffering:
                    self._ring_prebuffering = True
                    self._ring_recovering = True
                    self._ring_need_fade_in = True
                return b"\x00" * need

            if self._ring_prebuffering:
                target_bytes = (
                    max(prebuf_bytes, int(prebuf_bytes * 1.5))
                    if self._ring_recovering
                    else prebuf_bytes
                )
                max_wait = 0.44 if self._ring_recovering else 0.24
                elapsed = max(0.0, now_cb - self._ring_first_ts) if self._ring_first_ts > 0 else 0.0
                tail_idle = max(0.0, now_cb - self._ring_last_ts) if self._ring_last_ts > 0 else 0.0
                ready = (
                    avail >= target_bytes
                    or (avail >= need and elapsed >= max_wait)
                    or (not self._gemini_turn_active and tail_idle >= 0.04)
                )
                if not ready:
                    return b"\x00" * need
                self._ring_prebuffering = False
                self._ring_recovering = False

            take_len = min(avail, need)
            take_len -= take_len % frame_bytes
            if take_len <= 0:
                return b"\x00" * need
            take = bytes(self._ring_buf[:take_len])
            del self._ring_buf[:take_len]
            need_fi = self._ring_need_fade_in
            self._ring_need_fade_in = False

            shortfall_frames = (need - take_len) // frame_bytes
            true_underrun = take_len < need and shortfall_frames > 2
            if true_underrun:
                if self._ring_active:
                    self._ring_prebuffering = True
                    self._ring_recovering = True
                self._ring_need_fade_in = True

            self._ring_last_played_ts = now_cb
            if now_cb + 0.45 > self._mic_echo_until:
                self._mic_echo_until = now_cb + 0.45

        if need_fi:
            take = self._fade_in_pcm16(
                take,
                samples=min(72, len(take) // frame_bytes),
                channels=out_ch,
            )
        if len(take) < need:
            if not true_underrun and len(take) >= frame_bytes:
                # Hold last sample frame across 1-2 frame ratecv rounding offsets without fade clicks.
                take = take + (take[-frame_bytes:] * shortfall_frames)
            else:
                tail_frames = len(take) // frame_bytes
                if tail_frames > 0:
                    take = self._fade_out_pcm16(
                        take,
                        samples=min(72, tail_frames),
                        channels=out_ch,
                    )
                take = take + (b"\x00" * (need - len(take)))
        return take

    @staticmethod
    def _fade_pcm16_edge(pcm: bytes, samples: int, channels: int, fade_in: bool) -> bytes:
        """Apply a zero-NumPy fixed-point linear ramp to the start or end of int16 PCM."""
        ch = max(1, int(channels))
        frame_bytes = 2 * ch
        n_frames = len(pcm) // frame_bytes
        if n_frames <= 0 or samples <= 0:
            return pcm
        k = min(samples, n_frames)
        buf = bytearray(pcm[: n_frames * frame_bytes])
        mv = memoryview(buf).cast("h")
        start_frame = 0 if fade_in else (n_frames - k)
        denom = k + 1
        for f in range(k):
            g_q15 = ((f + 1 if fade_in else (k - f)) << 15) // denom
            base = (start_frame + f) * ch
            for c in range(ch):
                mv[base + c] = (mv[base + c] * g_q15) >> 15
        mv.release()
        return bytes(buf)

    @staticmethod
    def _fade_in_pcm16(pcm: bytes, samples: int = 72, channels: int = 1) -> bytes:
        return JarvisLive._fade_pcm16_edge(pcm, samples, channels, fade_in=True)

    @staticmethod
    def _fade_out_pcm16(pcm: bytes, samples: int = 64, channels: int = 1) -> bytes:
        return JarvisLive._fade_pcm16_edge(pcm, samples, channels, fade_in=False)

    @staticmethod
    def _resample_to_output(pcm24: bytes, out_rate: int, out_ch: int, ratecv_state: any) -> tuple[bytes, any]:
        """
        Continuous zero-click PCM16 resampler using Python's C-level audioop.ratecv.
        Maintains fractional phase and digital filter history across chunk boundaries,
        completely eliminating phase discontinuity clicks, crackles, and buzzing.
        """
        n_bytes = len(pcm24) - (len(pcm24) % 2)
        if n_bytes <= 0:
            return b"", ratecv_state

        if out_rate == RECV_SAMPLE_RATE:
            res_mono = pcm24[:n_bytes]
        else:
            try:
                res_mono, ratecv_state = audioop.ratecv(
                    pcm24[:n_bytes], 2, 1, RECV_SAMPLE_RATE, out_rate, ratecv_state
                )
            except Exception:
                res_mono = pcm24[:n_bytes]

        if out_ch == 2:
            try:
                res_out = audioop.tostereo(res_mono, 2, 1, 1)
            except Exception:
                res_out = res_mono
        else:
            res_out = res_mono

        return res_out, ratecv_state

    @staticmethod
    def _open_speaker_stream(stream_callback=None):
        """
        Opens a steady low-crackle output stream on Windows:
        1. Windows MME WaveOut @ 48000Hz (exact 2x of 24kHz Gemini audio, 1920 fpb = 40.00ms, +/-0.5ms jitter)
        2. MME native rate fallback (e.g. 44100Hz, 1764 fpb)
        3. Windows WASAPI fallback if MME is unavailable
        Returns (stream, out_rate, out_channels, host_label).
        """
        try:
            import ctypes
            ctypes.windll.ole32.CoInitialize(0)
        except Exception:
            pass

        candidates = []
        try:
            mme_host = pya.get_host_api_info_by_type(getattr(pyaudio, "paMME", 2))
            mme_idx = int(mme_host.get("defaultOutputDevice", -1))
            if mme_idx >= 0:
                mme_info = pya.get_device_info_by_index(mme_idx)
                mme_rate = int(mme_info.get("defaultSampleRate", SPEAKER_OUTPUT_RATE) or SPEAKER_OUTPUT_RATE)
                mme_ch = int(mme_info.get("maxOutputChannels", 2) or 2)
                if mme_ch >= 2:
                    candidates.append((mme_idx, SPEAKER_OUTPUT_RATE, 2, "MME-48k", 1920))
                candidates.append((mme_idx, SPEAKER_OUTPUT_RATE, 1, "MME-48k-Mono", 1920))
                if mme_rate != SPEAKER_OUTPUT_RATE:
                    native_fpb = max(512, int(round(mme_rate * 0.04)))
                    if mme_ch >= 2:
                        candidates.append((mme_idx, mme_rate, 2, "MME-Native", native_fpb))
                    candidates.append((mme_idx, mme_rate, 1, "MME-Native-Mono", native_fpb))
        except Exception:
            pass

        candidates.append((None, SPEAKER_OUTPUT_RATE, 2, "Default-48k", 1920))
        candidates.append((None, SPEAKER_OUTPUT_RATE, 1, "Default-48k-Mono", 1920))
        candidates.append((None, RECV_SAMPLE_RATE, 1, "Default-24k", 960))

        try:
            host = pya.get_host_api_info_by_type(getattr(pyaudio, "paWASAPI", 13))
            dev_idx = int(host.get("defaultOutputDevice", -1))
            if dev_idx >= 0:
                dev_info = pya.get_device_info_by_index(dev_idx)
                native_rate = int(dev_info.get("defaultSampleRate", SPEAKER_OUTPUT_RATE) or SPEAKER_OUTPUT_RATE)
                max_ch = int(dev_info.get("maxOutputChannels", 2) or 2)
                if max_ch >= 2:
                    candidates.append((dev_idx, native_rate, 2, "WASAPI", 1920))
                candidates.append((dev_idx, native_rate, 1, "WASAPI", 1920))
        except Exception:
            pass

        last_err = None
        for dev_idx, rate, ch, label, fpb in candidates:
            try:
                kwargs = dict(
                    format=FORMAT,
                    channels=ch,
                    rate=rate,
                    output=True,
                    frames_per_buffer=fpb,
                )
                if dev_idx is not None:
                    kwargs["output_device_index"] = dev_idx
                if stream_callback is not None:
                    kwargs["stream_callback"] = stream_callback
                stream = pya.open(**kwargs)
                return stream, rate, ch, label
            except Exception as exc:
                last_err = exc
        raise last_err or RuntimeError("No audio output device available")

    def _ensure_audio_threads_started(self):
        """
        Starts one persistent Microphone thread and one persistent Speaker thread for the entire lifetime
        of the application. Keeping hardware streams open across WebSocket idle refreshes prevents
        Intel Smart Sound mic lockups and eliminates Windows PortAudio crackling.
        """
        if self._audio_threads_started:
            return
        self._audio_threads_started = True

        def _mic_thread_loop():
            print("[ROSE] 🎤 Persistent Microphone stream started (20ms low-latency capture)")
            from core.mic_monitor import get_default_mic_state

            last_flatline_ts = 0.0
            last_mute_probe_ts = 0.0
            last_sys_muted: bool | None = None

            def _sync_sys_mute_state(force: bool = False):
                nonlocal last_mute_probe_ts, last_sys_muted
                if self._shutdown_event.is_set() or getattr(self.ui, "_closing", False):
                    return last_sys_muted
                now_probe = time.perf_counter()
                if not force and (now_probe - last_mute_probe_ts) < 1.8:
                    return last_sys_muted
                last_mute_probe_ts = now_probe
                st = get_default_mic_state()
                muted_val = st.get("muted") if st.get("available") else None
                if muted_val is not None and muted_val != last_sys_muted:
                    last_sys_muted = bool(muted_val)
                    if hasattr(self.ui, "set_sys_mic_muted"):
                        self.ui.set_sys_mic_muted(last_sys_muted)
                    if last_sys_muted:
                        self.ui.write_log(
                            "ERR: ⚠️ Windows microphone is MUTED — click UNMUTE MIC in HUD or press your laptop mic mute key."
                        )
                    elif time.monotonic() >= getattr(self.ui, "_suppress_unmute_log_until", 0.0):
                        self.ui.write_log("SYS: 🎙 Windows microphone unmuted — listening.")
                return last_sys_muted

            _sync_sys_mute_state(force=True)
            while not self._shutdown_event.is_set() and not getattr(self.ui, "_closing", False):
                stream = None
                dev_rate = SEND_SAMPLE_RATE
                dev_ch = CHANNELS
                dev_fpb = CHUNK_SIZE
                rate_state = None
                zero_streak = 0
                try:
                    try:
                        stream = pya.open(
                            format=FORMAT,
                            channels=CHANNELS,
                            rate=SEND_SAMPLE_RATE,
                            input=True,
                            frames_per_buffer=CHUNK_SIZE,
                        )
                    except Exception:
                        info = pya.get_default_input_device_info()
                        dev_rate = int(info.get("defaultSampleRate", 44100))
                        dev_ch = max(1, min(2, int(info.get("maxInputChannels", 1))))
                        dev_fpb = max(320, int(round(dev_rate * 0.02)))
                        stream = pya.open(
                            format=FORMAT,
                            channels=dev_ch,
                            rate=dev_rate,
                            input=True,
                            frames_per_buffer=dev_fpb,
                        )

                    while not self._shutdown_event.is_set() and not getattr(self.ui, "_closing", False):
                        raw = stream.read(dev_fpb, exception_on_overflow=False)
                        sys_muted_now = _sync_sys_mute_state(force=(zero_streak == 45))
                        if raw and raw.count(b"\x00") == len(raw):
                            zero_streak += 1
                            if sys_muted_now:
                                # OS/hardware mute intentionally zeros input; don't churn PortAudio stream
                                zero_streak = min(zero_streak, 46)
                            else:
                                now_flat = time.perf_counter()
                                if zero_streak > 300 and (now_flat - last_flatline_ts) > 15.0:
                                    last_flatline_ts = now_flat
                                    print("[ROSE] 🔄 Microphone flatline detected — reopening input stream...")
                                    break
                        else:
                            zero_streak = 0
                            if last_sys_muted:
                                _sync_sys_mute_state(force=True)

                        mono = raw if dev_ch == 1 else audioop.tomono(raw, 2, 0.5, 0.5)
                        if dev_rate != SEND_SAMPLE_RATE:
                            pcm16, rate_state = audioop.ratecv(
                                mono, 2, 1, dev_rate, SEND_SAMPLE_RATE, rate_state
                            )
                        else:
                            pcm16 = mono

                        now_mic = time.perf_counter()
                        with self._speaking_lock:
                            jarvis_speaking = self._is_speaking
                        turn_tail_active = (
                            self._gemini_turn_active
                            and (now_mic - self._gemini_turn_last_audio_ts) < 2.8
                        )
                        echo_active = (
                            jarvis_speaking
                            or turn_tail_active
                            or (now_mic < self._mic_echo_until)
                        )

                        if not self.ui.muted and not self._paused and not self._interview_mode:
                            if echo_active:
                                self.ui.set_mic_level(0.0)
                                out_pcm = b"\x00" * len(pcm16) if pcm16 else b""
                            else:
                                rms = audioop.rms(pcm16, 2) if pcm16 else 0
                                self.ui.set_mic_level(min(1.0, rms / 2400.0))
                                voice_monitor.feed_pcm(pcm16, rms)
                                if rms > 75:
                                    self.ui.mark_user_activity(True)
                                out_pcm = pcm16

                            loop = self._loop
                            out_q = self.out_queue
                            if loop and out_q and out_pcm:
                                def _enqueue(pcm: bytes = out_pcm, q_ref=out_q):
                                    if q_ref.full():
                                        try:
                                            q_ref.get_nowait()
                                        except Exception:
                                            pass
                                    try:
                                        q_ref.put_nowait(pcm)
                                    except Exception:
                                        pass
                                try:
                                    loop.call_soon_threadsafe(_enqueue)
                                except Exception:
                                    pass
                except RuntimeError as e:
                    if self._shutdown_event.is_set() or getattr(self.ui, "_closing", False) or "deleted" in str(e).lower():
                        break
                    print(f"[ROSE] ❌ Mic thread error: {e}")
                except Exception as e:
                    if self._shutdown_event.is_set() or getattr(self.ui, "_closing", False):
                        break
                    print(f"[ROSE] ❌ Mic thread error: {e}")
                finally:
                    if stream is not None:
                        try:
                            stream.stop_stream()
                            stream.close()
                        except Exception:
                            pass
                if self._shutdown_event.is_set() or getattr(self.ui, "_closing", False):
                    break
                time.sleep(0.08)

        def _speaker_thread_loop():
            print("[ROSE] 🔊 Persistent Audio playback stream started (low-jitter callback ring-buffer)")
            q = self.audio_in_queue
            while not self._shutdown_event.is_set() and not getattr(self.ui, "_closing", False):
                stream = None
                out_rate = SPEAKER_OUTPUT_RATE
                out_ch = 2
                host_label = "WASAPI"
                ratecv_state = None
                rem = b""
                in_utterance = False
                self._clear_speaker_buffer()

                try:
                    prebuf_bytes = int(out_rate * 0.16) * 2 * out_ch

                    def _audio_cb(in_data, frame_count, time_info, status):
                        need = frame_count * 2 * out_ch
                        return (
                            self._pop_speaker_callback_frame(
                                need,
                                frame_count,
                                out_ch,
                                prebuf_bytes,
                                time.perf_counter(),
                            ),
                            pyaudio.paContinue,
                        )

                    stream, out_rate, out_ch, host_label = self._open_speaker_stream(stream_callback=_audio_cb)
                    prebuf_bytes = int(out_rate * 0.16) * 2 * out_ch
                    stream.start_stream()
                    print(f"[ROSE] 🔊 Speaker callback active: backend={host_label}, rate={out_rate}Hz, ch={out_ch}")

                    while stream.is_active() and not self._shutdown_event.is_set() and not getattr(self.ui, "_closing", False):
                        try:
                            first_chunk = q.get(timeout=0.05)
                        except queue.Empty:
                            now_sp = time.perf_counter()
                            if in_utterance or self._is_speaking:
                                with self._ring_lock:
                                    rem_len = len(self._ring_buf)
                                    idle_elapsed = now_sp - self._ring_last_ts if self._ring_last_ts > 0 else 1.0
                                    played_elapsed = (
                                        now_sp - self._ring_last_played_ts
                                        if self._ring_last_played_ts > 0
                                        else idle_elapsed
                                    )
                                turn_still_streaming = (
                                    self._gemini_turn_active
                                    and (now_sp - self._gemini_turn_last_audio_ts) < 2.8
                                )
                                echo_clear = now_sp >= self._mic_echo_until
                                failsafe_done = (
                                    idle_elapsed >= (3.5 if self._gemini_turn_active else 0.9)
                                    and rem_len == 0
                                )
                                if rem_len == 0 and q.empty() and (
                                    (
                                        not turn_still_streaming
                                        and idle_elapsed > 0.55
                                        and played_elapsed >= 0.35
                                        and echo_clear
                                    )
                                    or failsafe_done
                                ):
                                    self.set_speaking(False)
                                    self.ui.set_audio_level(0.0)
                                    in_utterance = False
                                    ratecv_state = None
                                    rem = b""
                                    with self._ring_lock:
                                        self._ring_prebuffering = True
                                        self._ring_recovering = False
                                        self._ring_need_fade_in = True
                                        self._ring_active = False
                            continue

                        buf = bytearray(rem)
                        rem = b""
                        buf.extend(first_chunk)
                        while not q.empty() and len(buf) < 48000:
                            try:
                                buf.extend(q.get_nowait())
                            except queue.Empty:
                                break

                        if len(buf) % 2 != 0:
                            rem = bytes(buf[-1:])
                            del buf[-1:]

                        if buf:
                            raw_pcm = bytes(buf)
                            fresh = not in_utterance
                            if fresh:
                                in_utterance = True
                                ratecv_state = None
                                raw_pcm = self._fade_in_pcm16(raw_pcm, samples=72, channels=1)

                            pcm_out, ratecv_state = self._resample_to_output(
                                raw_pcm, out_rate, out_ch, ratecv_state
                            )
                            if pcm_out:
                                now_ts = time.perf_counter()
                                frame_bytes = 2 * max(1, out_ch)
                                with self._ring_lock:
                                    was_empty = len(self._ring_buf) < frame_bytes
                                    if fresh or not self._ring_active:
                                        self._ring_prebuffering = True
                                        self._ring_recovering = False
                                        self._ring_need_fade_in = True
                                        self._ring_first_ts = now_ts
                                        self._ring_active = True
                                    elif self._ring_prebuffering and was_empty:
                                        self._ring_first_ts = now_ts
                                    self._ring_last_ts = now_ts
                                    self._ring_buf.extend(pcm_out)
                                if fresh or not self._is_speaking:
                                    self.set_speaking(True)
                            if len(raw_pcm) >= 2:
                                try:
                                    spk_rms = audioop.rms(raw_pcm, 2)
                                    self.ui.set_audio_level(min(1.0, spk_rms / 3200.0))
                                except Exception:
                                    pass
                except RuntimeError as e:
                    if self._shutdown_event.is_set() or getattr(self.ui, "_closing", False) or "deleted" in str(e).lower():
                        break
                    print(f"[ROSE] ❌ Speaker thread error: {e}")
                    self.set_speaking(False)
                except Exception as e:
                    if self._shutdown_event.is_set() or getattr(self.ui, "_closing", False):
                        break
                    print(f"[ROSE] ❌ Speaker thread error: {e}")
                    self.set_speaking(False)
                finally:
                    with self._speaking_lock:
                        self._is_speaking = False
                    self._clear_speaker_buffer()
                    if stream is not None:
                        try:
                            stream.stop_stream()
                            stream.close()
                        except Exception:
                            pass
                if self._shutdown_event.is_set() or getattr(self.ui, "_closing", False):
                    break
                time.sleep(0.4)

        threading.Thread(target=_mic_thread_loop, daemon=True, name="ROSE-Mic").start()
        threading.Thread(target=_speaker_thread_loop, daemon=True, name="ROSE-Speaker").start()

    async def _send_realtime(self):
        while True:
            item = await self.out_queue.get()
            parts = []
            first = item.get("data", b"") if isinstance(item, dict) else item
            if first:
                parts.append(first)
            # Drain a small burst (up to ~80ms) to avoid WebSocket backlog lag
            for _ in range(3):
                if self.out_queue.empty():
                    break
                try:
                    nxt = self.out_queue.get_nowait()
                    val = nxt.get("data", b"") if isinstance(nxt, dict) else nxt
                    if val:
                        parts.append(val)
                except Exception:
                    break
            pcm = b"".join(parts)
            if pcm:
                await self.session.send_realtime_input(
                    audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")
                )

    async def _receive_audio(self):
        print("[ROSE] 👂 Receive started")
        out_buf, in_buf = [], []
        output_noise = False
        output_noise_samples = []
        try:
            while True:
                async for response in self.session.receive():
                    if response.data and self.audio_in_queue is not None and not self._interview_mode:
                        self._gemini_turn_active = True
                        self._gemini_turn_last_audio_ts = time.perf_counter()
                        self.audio_in_queue.put(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if getattr(sc, "interrupted", False):
                            self._gemini_turn_active = False
                            self._clear_speaker_buffer()
                            voice_monitor.reset_turn_buffer()
                            self.set_speaking(False)

                        if sc.output_transcription and sc.output_transcription.text:
                            raw_txt = sc.output_transcription.text.strip()
                            if raw_txt:
                                txt, had_noise = self._clean_transcript_text(raw_txt)
                                if had_noise:
                                    output_noise = True
                                    if len(output_noise_samples) < 4:
                                        output_noise_samples.append(raw_txt)
                                if txt:
                                    out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                in_buf.append(txt)
                                self.ui.mark_user_activity(True)
                                joined_lower = " ".join(in_buf).lower()
                                explicit_show = any(w in joined_lower for w in ("open your screen", "show yourself", "wake up"))
                                if explicit_show or (any(w in joined_lower for w in ("rose", "jarvis", "javis")) and (time.time() - self._last_popup_ts) > 15.0):
                                    self._last_popup_ts = time.time()
                                    self.ui.pop_up_screen()
                                now_iv = time.time()
                                if (now_iv - self._last_voice_interview_trigger_ts) > 4.0:
                                    if not self._interview_mode:
                                        start_hit, role_hint = self._extract_interview_start_role(joined_lower)
                                        if start_hit:
                                            self._last_voice_interview_trigger_ts = now_iv
                                            asyncio.create_task(asyncio.to_thread(interview_mode_control, "start", role_hint))
                                    elif any(p in joined_lower for p in ("exit interview mode", "stop interview mode", "end interview mode", "turn off interview mode", "leave interview mode")):
                                        self._last_voice_interview_trigger_ts = now_iv
                                        asyncio.create_task(asyncio.to_thread(interview_mode_control, "stop"))
                                    if any(p in joined_lower for p in ("open job radar", "open job tracker", "show jobs", "daily jobs", "job command center", "search international jobs", "find jobs", "find gigs")):
                                        asyncio.create_task(asyncio.to_thread(job_command_center_control, "open"))
                                    elif any(p in joined_lower for p in ("autofill form", "autofill this", "autofill application", "install autofill")):
                                        from actions.autofill_copilot import open_installer_in_chrome
                                        asyncio.create_task(asyncio.to_thread(open_installer_in_chrome))
                                    elif "search twitter" in joined_lower and ("job" in joined_lower or "gig" in joined_lower or "hiring" in joined_lower):
                                        from actions.job_command_center import launch_live_chrome_search
                                        asyncio.create_task(asyncio.to_thread(launch_live_chrome_search, "twitter", "python django react"))
                                    elif "search upwork" in joined_lower:
                                        from actions.job_command_center import launch_live_chrome_search
                                        asyncio.create_task(asyncio.to_thread(launch_live_chrome_search, "upwork", "python django react"))

                        if sc.turn_complete:
                            self._gemini_turn_active = False
                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                if any(w in full_in.lower() for w in ("rose", "jarvis", "javis")):
                                    self.ui.pop_up_screen()
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"ROSE: {full_out}")
                                if output_noise_samples:
                                    self.ui.write_debug(
                                        "Partially filtered audio transcript: " + " | ".join(output_noise_samples),
                                        level="WARN",
                                    )
                            elif output_noise:
                                self.ui.write_log("ERR: Failed to decode audio response.")
                                if output_noise_samples:
                                    self.ui.write_debug(
                                        "Filtered raw transcript: " + " | ".join(output_noise_samples),
                                        level="WARN",
                                    )
                                self.ui.set_state("ERROR")
                            out_buf = []
                            output_noise = False
                            output_noise_samples = []
                            voice_monitor.reset_turn_buffer()

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[ROSE] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses)

        except Exception as e:
            print(f"[ROSE] ❌ Receive: {e}")
            traceback.print_exc()
            raise

    @staticmethod
    def _execute_scheduled_reminder_action(action_spec: str) -> str:
        spec = (action_spec or "").strip()
        if not spec:
            return ""
        if ":" in spec:
            act_name, arg = spec.split(":", 1)
        else:
            act_name, arg = spec, ""
        act_name = act_name.strip().lower()
        arg = arg.strip()

        try:
            if act_name == "open_app" and arg:
                return open_app(arg)
            if act_name == "open_workspace":
                return open_workspace(arg or None)
            if act_name in ("play_media", "play_music") and arg:
                return play_media(arg, "auto", True)
            if act_name in ("web_research", "vr_search") and arg:
                return web_research(arg, "deep_search")
            if act_name in ("minimize_app", "maximize_app", "restore_app", "close_app") and arg:
                return laptop_control(act_name, target=arg)
            if act_name in ("set_volume", "set_brightness") and arg:
                return laptop_control(act_name, value=arg)
            if act_name == "lock_screen":
                return laptop_control("lock_screen")
            if act_name == "sleep_laptop":
                return laptop_control("sleep_laptop", confirmed=True)
            if act_name in ("deep_work", "morning_startup", "end_of_day", "meeting_mode"):
                return workflow_autopilot(act_name, target=arg)
            if act_name in ("trim_ram", "free_memory"):
                return pc_optimizer("trim_ram")
            if act_name == "browser_control" and arg:
                return browser_control("open_url" if arg.startswith("http") else "search", url=arg, query=arg)
        except Exception as exc:
            return f"Scheduled action '{spec}' error: {exc}"
        return ""

    async def _watch_reminders(self):
        """Checks every 2 seconds for due reminders (executing scheduled actions) AND the morning wake-up briefing."""
        while True:
            await asyncio.sleep(2.0)
            try:
                due_list = await asyncio.to_thread(pop_due_reminders)
                for rem in due_list:
                    title = rem.get("title", "Reminder")
                    notes = rem.get("notes", "")
                    sched_act = rem.get("scheduled_action", "")
                    action_result = ""
                    if sched_act:
                        action_result = await asyncio.to_thread(
                            self._execute_scheduled_reminder_action, sched_act
                        )

                    self.ui.pop_up_screen()
                    if not self._interview_mode:
                        self.ui.play_success_sfx()
                    self.ui.write_log(f"SYS: ⏰ REMINDER DUE — {title}")
                    if action_result:
                        self.ui.write_log(f"SYS: ⚡ Executed scheduled action — {action_result}")
                    if self.session:
                        msg = (
                            f"[ALARM / REMINDER DUE RIGHT NOW] Speak out loud to the user immediately in polished English: "
                            f"their reminder '{title}' is due right now."
                        )
                        if notes:
                            msg += f" (Notes: {notes})"
                        if action_result:
                            msg += f" I already executed the scheduled action: {action_result}. Tell the user clearly."
                        else:
                            msg += (
                                " If this reminder asks you to perform a computer action right now (such as opening an app, "
                                "playing music, checking something, or running a tool), IMMEDIATELY call that tool now as well!"
                            )
                        await self.session.send_client_content(
                            turns={"parts": [{"text": msg}]},
                            turn_complete=True,
                        )

                # Also check 07:00 AM Morning Wake-Up Assistant
                wakeup_briefing = await asyncio.to_thread(check_and_pop_morning_wakeup)
                if wakeup_briefing:
                    loc = get_configured_location()
                    self.ui.pop_up_screen()
                    if not self._interview_mode:
                        self.ui.play_success_sfx()
                    self.ui.write_log("SYS: ☀️ MORNING WAKE-UP BRIEFING")
                    if self.session:
                        msg = (
                            f"{wakeup_briefing}\n"
                            "Speak warmly and casually to wake the Boss up right now! Call them 'Boss', say good morning, "
                            "and ask how their night was first, with a quick friendly glance at the time and weather in "
                            f"{loc['city']} — then follow their lead on today's calendar and plans."
                        )
                        await self.session.send_client_content(
                            turns={"parts": [{"text": msg}]},
                            turn_complete=True,
                        )
            except Exception:
                pass

    @staticmethod
    def _extract_leaf_errors(exc: BaseException) -> list[BaseException]:
        if isinstance(exc, BaseExceptionGroup):
            leaves: list[BaseException] = []
            for sub in exc.exceptions:
                leaves.extend(JarvisLive._extract_leaf_errors(sub))
            return leaves
        return [exc]

    async def run(self):
        client = genai.Client(
            api_key=get_api_key(),
            http_options={"api_version": "v1alpha"}
        )

        first_connect = True
        while True:
            if self._paused:
                await asyncio.sleep(1)
                continue

            try:
                print("[ROSE] 🔌 Connecting...")
                if first_connect:
                    self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session   = session
                    self._loop     = asyncio.get_running_loop()
                    self.out_queue = asyncio.Queue(maxsize=100)
                    self._ensure_audio_threads_started()

                    print("[ROSE] ✅ Connected.")
                    self.ui.set_state("LISTENING")
                    if first_connect:
                        self.ui.write_log("SYS: ROSE ready. Listening...")
                        first_connect = False

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._watch_reminders())

            except BaseException as e:
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                leaves = self._extract_leaf_errors(e)
                combined_err = " | ".join(f"{type(x).__name__}: {x}" for x in leaves)
                err_lower = combined_err.lower()
                idle_markers = ("inactivity", "1008", "1000", "1001", "1006", "keepalive", "connectionclosed")
                if any(m in err_lower for m in idle_markers):
                    print("[ROSE] 🔄 Idle session refresh...")
                    await asyncio.sleep(0.4)
                else:
                    print(f"[ROSE] ⚠️ {combined_err}")
                    traceback.print_exc()
                    self.ui.write_log(f"ERR: Connection lost — {leaves[0] if leaves else e}")
                    self.ui.set_state("ERROR")
                    await asyncio.sleep(2.5)
            finally:
                self._gemini_turn_active = False
                self.session = None
                self.out_queue = None
                self._clear_speaker_buffer()
                self.set_speaking(False)


def main():
    _kill_other_instances()
    ui = JarvisUI()
    set_ui_callback("ui", ui)
    set_ui_callback("refresh_location", ui.refresh_location)
    set_ui_callback("show_whatsapp_qr", ui.show_whatsapp_qr)

    def _shutdown_bridges():
        try:
            from integrations.telegram_bot import stop_telegram_bot

            stop_telegram_bot()
        except Exception:
            pass
        try:
            from actions.whatsapp_bridge import stop_sidecar

            stop_sidecar()
        except Exception:
            pass

    ui.on_shutdown = _shutdown_bridges
    init_camera_controller(ui.root, ui_ref=ui)
    init_research_board(ui.root)
    init_busy_bar(ui.root)
    init_interview_copilot(
        ui.root,
        ui.write_log,
        lambda active: ui.set_interview_mode_active(active),
    )
    ui.on_interview_toggle = lambda: ui.write_log(f"SYS: {interview_mode_control('toggle')}")
    init_job_command_center(ui.root, ui.write_log)
    ui.on_jobs_toggle = lambda: ui.write_log(f"SYS: {job_command_center_control('open')}")

    try:
        from integrations.telegram_bot import start_telegram_bot_if_configured

        start_telegram_bot_if_configured(log_cb=ui.write_log)
    except Exception as exc:
        ui.write_log(f"SYS: Telegram bridge skipped ({exc})")

    def _boot_whatsapp_sidecar() -> None:
        try:
            from actions.whatsapp_bridge import ensure_sidecar_started, get_whatsapp_status

            res = ensure_sidecar_started()
            if not res.ok:
                detail = res.error or res.data.get("message") or "unavailable"
                ui.write_log(f"SYS: WhatsApp service offline ({detail})")
                return
            st = get_whatsapp_status()
            if st.get("connected"):
                who = (st.get("user") or {}).get("id", "linked device")
                ui.write_log(f"SYS: WhatsApp connected (+{str(who).split(':')[0]})")
            else:
                ui.write_log(
                    "SYS: WhatsApp ready — link phone via Settings -> Link WhatsApp (QR) or ask R.O.S.E for WhatsApp QR"
                )
        except Exception as exc:
            ui.write_log(f"SYS: WhatsApp bridge skipped ({exc})")

    threading.Thread(target=_boot_whatsapp_sidecar, daemon=True, name="ROSE-WA-Boot").start()

    def runner():
        ui.wait_for_api_key()
        backend = get_backend()
        if backend == "lmstudio":
            from core.lmstudio_runtime import JarvisLocal, ping_lmstudio
            ok, detail = ping_lmstudio()
            if not ok:
                ui.write_log(f"ERR: Could not reach LM Studio server ({detail}). "
                             "Start 'Local Server' in LM Studio.")
            else:
                ui.write_log(f"SYS: LM Studio ready - models: {detail}")
            jarvis = JarvisLocal(ui, TOOL_DECLARATIONS, load_system_prompt)
        else:
            jarvis = JarvisLive(ui)

        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\nShutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()
