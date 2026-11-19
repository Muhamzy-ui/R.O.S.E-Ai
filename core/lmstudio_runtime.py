"""
LM Studio (yerel) runtime — Gemini Live yerine OpenAI-uyumlu yerel modeli kullanir.

Akis:
  Mikrofon -> SpeechRecognition (Whisper veya Google) -> metin
        -> LM Studio /v1/chat/completions (tool calling)
        -> Tool sonuclari geri besleme -> nihai metin
        -> Windows SAPI TTS (actions.tts.speak_text)

JarvisLocal sinifinin arayuzu JarvisLive ile uyumludur: ui.on_text_command,
ui.on_pause_toggle vb. callback'leri ayni kullanir, asyncio.run(jarvis.run())
ile baslatilir.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import threading
import time
import traceback
from typing import Any, Callable

import requests

from app_config import get_app_config_value
from memory.memory_manager import (
    load_memory,
    update_memory,
    delete_memory,
    format_memory_for_prompt,
)
from actions.open_app import open_app, open_workspace
from actions.sys_info import sys_info
from actions.calendar import (
    get_calendar_events,
    add_calendar_event,
    delete_calendar_event,
)
from actions.reminders import get_reminders, add_reminder
from actions.browser import browser_control
from actions.shell import shell_run
from actions.whatsapp import send_whatsapp_message, save_whatsapp_contact
from actions.media import play_media
from actions.weather import get_weather_summary, set_location, get_configured_location, get_location_datetime
from actions.screen_vision import analyze_screen
from actions.camera_vision import camera_vision
from actions.youtube_stats import get_youtube_channel_report
from actions.laptop_control import laptop_control
from actions.voice_auth import voice_security_control
from actions.file_intel import file_intelligence
from actions.web_research import web_research
from actions.job_assistant import job_assistant
from actions.pc_optimizer import pc_optimizer
from actions.clipboard_copilot import clipboard_copilot
from actions.doc_creator import document_creator
from actions.content_digest import content_digest
from actions.routines_and_dev import workflow_autopilot
from actions.interview_copilot import init_interview_copilot, interview_mode_control
from actions.tts import speak_text


# ── Tool registry ────────────────────────────────────────────────────────────

def _save_memory_tool(category: str, key: str, value: str) -> str:
    if not key or not value:
        return "Missing parameters."
    update_memory({category or "notes": {key: {"value": value}}})
    return "ok"


TOOL_HANDLERS: dict[str, Callable[..., Any]] = {
    "open_app": lambda app_name="", force_new=False: open_app(app_name, bool(force_new)),
    "open_workspace": lambda apps="", force_new=False: open_workspace(apps or None, bool(force_new)),
    "sys_info": lambda query="all": sys_info(query),
    "get_weather": lambda location=None: get_weather_summary(location or None),
    "set_location": lambda location="": set_location(location),
    "laptop_control": lambda action="", value="", text="", target="", confirmed=False: laptop_control(action, value, text, target, bool(confirmed)),
    "voice_security": lambda action="status", owner_name="Boss": voice_security_control(action, owner_name),
    "get_calendar_events": lambda query="today", limit=6: get_calendar_events(query, int(limit or 6)),
    "add_calendar_event": lambda title="", start_iso="", end_iso="", notes="", location="", calendar_name="", all_day=False: add_calendar_event(title, start_iso, end_iso, notes, location, calendar_name, bool(all_day)),
    "delete_calendar_event": lambda title="", start_iso="", calendar_name="", delete_all_matches=False: delete_calendar_event(title, start_iso, calendar_name, bool(delete_all_matches)),
    "get_reminders": lambda query="upcoming", limit=8, list_name="": get_reminders(query, int(limit or 8), list_name),
    "add_reminder": lambda title="", due_iso="", notes="", list_name="", priority="", all_day=False, scheduled_action="": add_reminder(title, due_iso, notes, list_name, priority, bool(all_day), scheduled_action),
    "browser_control": lambda action="open_url", url=None, query=None: browser_control(action, url, query),
    "web_research": lambda query="", mode="deep_search", url="": web_research(query, mode, url),
    "file_intelligence": lambda action="search", query_or_path="", second_path="", folder="home", confirmed=False: file_intelligence(action, query_or_path, second_path, folder, bool(confirmed)),
    "job_assistant": lambda action="search_jobs", query="", location="", resume_path="", full_name="", email="", phone="", skills="", url="", job_index=1, confirmed=False, batch_size=5: job_assistant(action, query, location, resume_path, full_name, email, phone, skills, url, int(job_index or 1), bool(confirmed), int(batch_size or 5)),
    "pc_optimizer": lambda action="health_report", target="", confirmed=False: pc_optimizer(action, target, bool(confirmed)),
    "clipboard_copilot": lambda action="read", instruction="", copy_back=True: clipboard_copilot(action, instruction, bool(copy_back)),
    "document_creator": lambda action="create_word_doc", title="ROSE Document", content="", folder="desktop", client_name="", items_or_rows="", currency="NGN", project_type="python", open_after=False: document_creator(action, title, content, folder, client_name, items_or_rows, currency, project_type, bool(open_after)),
    "content_digest": lambda action="summarize_youtube", target="", amount=1.0, from_currency="USD", to_currency="NGN", save_note=False, create_reminders=False: content_digest(action, target, float(amount or 1.0), from_currency, to_currency, bool(save_note), bool(create_reminders)),
    "workflow_autopilot": lambda action="deep_work", target="", message="", minutes=45, confirmed=False: workflow_autopilot(action, target, message, int(minutes or 45), bool(confirmed)),
    "interview_mode_control": lambda action="start", role="", company="", question="", also_mic=False: interview_mode_control(action, role, company, question, bool(also_mic)),
    "shell_run": lambda command="": shell_run(command),
    "play_media": lambda query="", provider="auto", autoplay=True: play_media(query, provider, bool(autoplay)),
    "get_youtube_channel_report": lambda query="overview", handle="", video_limit=6: get_youtube_channel_report(query, handle, int(video_limit or 6)),
    "analyze_screen": lambda query="What is on my screen?", target="all_monitors": analyze_screen(query, target),
    "camera_vision": lambda action="identify", query="": camera_vision(action, query),
    "save_memory": lambda category="notes", key="", value="": _save_memory_tool(category, key, value),
    "delete_memory": lambda category="", key="", match_text="": delete_memory(category, key, match_text),
    "send_whatsapp_message": lambda message="", phone_number="", recipient_name="", send_now=False, app_target="auto": send_whatsapp_message(message, phone_number, recipient_name, bool(send_now), app_target),
    "save_whatsapp_contact": lambda display_name="", phone_number="", aliases="": save_whatsapp_contact(display_name, phone_number, aliases),
    "swiftsats_intelligence": lambda query="daily_report", window="", repo_path="": __import__("actions.swiftsats_bi", fromlist=["swiftsats_intelligence"]).swiftsats_intelligence(query, window=window, repo_path=repo_path),
    "business_advisor": lambda query="Review my ventures and flag risks and missing numbers.", venture="", channel="chat": __import__("actions.business_advisor", fromlist=["advise_business"]).advise_business(query=query, venture=venture, channel=channel)["full_analysis"],
}


def _gemini_tool_to_openai(decl: dict) -> dict:
    """Gemini tool deklarasyonunu OpenAI tool spec'ine cevirir."""
    name = decl["name"]
    description = decl.get("description", "")
    params = decl.get("parameters", {"type": "OBJECT", "properties": {}})

    def _to_openai_schema(node: dict) -> dict:
        if not isinstance(node, dict):
            return node
        out = {}
        for k, v in node.items():
            if k == "type" and isinstance(v, str):
                out["type"] = v.lower() if v.upper() in {"OBJECT", "STRING", "NUMBER", "BOOLEAN", "ARRAY", "INTEGER"} else v
            elif k == "properties" and isinstance(v, dict):
                out["properties"] = {pk: _to_openai_schema(pv) for pk, pv in v.items()}
            elif k == "items" and isinstance(v, dict):
                out["items"] = _to_openai_schema(v)
            else:
                out[k] = v
        return out

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": _to_openai_schema(params),
        },
    }


# ── STT ──────────────────────────────────────────────────────────────────────

class _SttEngine:
    def __init__(self, language: str = "tr-TR", engine: str = "whisper"):
        self.language = language
        self.engine = (engine or "whisper").lower()
        self._sr = None
        self._mic = None
        self._recognizer = None
        self._init_ok = False
        self._init_error = ""
        self._init()

    def _init(self):
        try:
            import speech_recognition as sr  # type: ignore
        except Exception as exc:
            self._init_error = f"SpeechRecognition kurulu degil: {exc}"
            return
        self._sr = sr
        try:
            self._recognizer = sr.Recognizer()
            self._recognizer.pause_threshold = 0.9
            self._recognizer.dynamic_energy_threshold = True
            self._mic = sr.Microphone()
            with self._mic as source:
                self._recognizer.adjust_for_ambient_noise(source, duration=0.6)
            self._init_ok = True
        except Exception as exc:
            self._init_error = f"Mikrofon hazirlanamadi: {exc}"

    def listen_once(self, phrase_time_limit: float = 12.0, timeout: float = 6.0) -> tuple[bool, str]:
        if not self._init_ok:
            return False, self._init_error or "STT hazir degil."
        sr = self._sr
        try:
            with self._mic as source:
                audio = self._recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
        except sr.WaitTimeoutError:
            return False, "timeout"
        except Exception as exc:
            return False, f"Dinleme hatasi: {exc}"

        try:
            if self.engine == "whisper":
                lang = (self.language or "tr").split("-")[0].lower()
                text = self._recognizer.recognize_whisper(audio, language=lang)
            else:
                text = self._recognizer.recognize_google(audio, language=self.language)
            return True, (text or "").strip()
        except sr.UnknownValueError:
            return False, ""
        except Exception as exc:
            # Whisper kurulu degilse Google'a dus
            if self.engine == "whisper":
                try:
                    text = self._recognizer.recognize_google(audio, language=self.language)
                    return True, (text or "").strip()
                except Exception as exc2:
                    return False, f"Transkripsiyon hatasi: {exc2}"
            return False, f"Transkripsiyon hatasi: {exc}"


# ── JarvisLocal ──────────────────────────────────────────────────────────────

class JarvisLocal:
    def __init__(self, ui, tool_declarations: list[dict], system_prompt_loader):
        self.ui = ui
        self._loop: asyncio.AbstractEventLoop | None = None
        self._paused = False
        self._is_speaking = False
        self._speaking_lock = threading.Lock()
        self._tool_declarations = tool_declarations
        self._openai_tools = [_gemini_tool_to_openai(d) for d in tool_declarations]
        self._load_system_prompt = system_prompt_loader
        self._pending_text_queue: asyncio.Queue | None = None
        self._messages: list[dict] = []
        self._max_history = 24

        self.ui.on_text_command = self._on_text_command
        self.ui.on_pause_toggle = self._on_pause_toggle
        self.ui.on_effects_state_change = lambda _e: None
        self._interview_mode = False
        init_interview_copilot(getattr(self.ui, "root", None), self.ui.write_log, self._set_interview_mode)
        try:
            from tools.builtins import set_ui_callback

            set_ui_callback("speak_on_laptop", self.speak_on_laptop)
        except Exception:
            pass

    def _set_interview_mode(self, active: bool):
        self._interview_mode = bool(active)

    def speak_on_laptop(self, text: str) -> dict:
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
        self.ui.write_log(f"ROSE [Spoken Aloud]: {clean}")
        self.set_speaking(True)

        def _on_done():
            self.set_speaking(False)

        try:
            speak_text(clean, on_done=_on_done, blocking=False)
            return {"ok": True, "backend": "windows_tts", "message": f'Spoken on laptop: "{clean}"'}
        except Exception as exc:
            self.set_speaking(False)
            return {"ok": False, "message": f"Windows TTS failed: {exc}"}

    # ── UI callbacks ─────────────────────────────────────────────────────────
    def _on_pause_toggle(self, paused: bool):
        self._paused = paused

    def _on_text_command(self, text: str):
        if self._paused or not text.strip():
            return
        self.ui.write_log(f"Siz: {text}")
        if self._loop and self._pending_text_queue is not None:
            asyncio.run_coroutine_threadsafe(self._pending_text_queue.put(text.strip()), self._loop)

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        self.ui.set_state("SPEAKING" if value else "LISTENING")

    # ── System prompt ────────────────────────────────────────────────────────
    def _build_system_message(self) -> str:
        memory = load_memory()
        mem_str = format_memory_for_prompt(memory)
        sys_p = self._load_system_prompt()
        loc = get_configured_location()
        now = get_location_datetime(loc)
        time_ctx = (
            f"[CURRENT LOCATION & TIME — {loc['display'].upper()} ({loc['timezone']})]\n"
            f"{now.strftime('%A, %d %B %Y - %H:%M')}\n\n"
        )
        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str + "\n\n")
        parts.append(sys_p)
        parts.append(
            "\n\nNot: Yerel modda calisiyorsun (LM Studio). Kullaniciya kisa, net "
            "Turkce yanitlar ver. Gerektiginde tool cagirisi yap."
        )
        return "\n".join(parts)

    def _ensure_system_message(self):
        sys_msg = {"role": "system", "content": self._build_system_message()}
        if self._messages and self._messages[0].get("role") == "system":
            self._messages[0] = sys_msg
        else:
            self._messages.insert(0, sys_msg)

    def _trim_history(self):
        if len(self._messages) <= self._max_history + 1:
            return
        head = self._messages[:1] if self._messages and self._messages[0].get("role") == "system" else []
        tail = self._messages[-self._max_history:]
        self._messages = head + tail

    # ── LM Studio cagrisi ────────────────────────────────────────────────────
    def _chat_completion(self) -> dict:
        base = str(get_app_config_value("lmstudio_base_url", "http://127.0.0.1:1234/v1") or "").rstrip("/")
        model = str(get_app_config_value("lmstudio_model", "local-model") or "local-model")
        api_key = str(get_app_config_value("lmstudio_api_key", "lm-studio") or "lm-studio")

        payload = {
            "model": model,
            "messages": self._messages,
            "temperature": 0.4,
            "stream": False,
        }
        if self._openai_tools:
            payload["tools"] = self._openai_tools
            payload["tool_choice"] = "auto"

        resp = requests.post(
            f"{base}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            data=json.dumps(payload),
            timeout=180,
        )
        if not resp.ok:
            raise RuntimeError(f"LM Studio {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def _execute_tool(self, name: str, raw_args: str) -> str:
        try:
            args = json.loads(raw_args) if raw_args else {}
        except Exception:
            args = {}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return f"Bilinmeyen arac: {name}"
        try:
            self.ui.set_state("THINKING")
            result = handler(**args)
            if name == "set_location":
                self.ui.refresh_location()
            return str(result if result is not None else "ok")
        except Exception as exc:
            traceback.print_exc()
            return f"Hata: {exc}"

    async def _handle_turn(self, user_text: str):
        self._ensure_system_message()
        self._messages.append({"role": "user", "content": user_text})
        self.ui.set_state("THINKING")

        for _ in range(6):  # max tool-call zinciri
            loop = asyncio.get_event_loop()
            try:
                data = await loop.run_in_executor(None, self._chat_completion)
            except Exception as exc:
                self.ui.write_log(f"ERR: LM Studio cevap vermedi - {exc}")
                self.ui.set_state("ERROR")
                # Kullaniciyi mesaj listesinden cikar ki tekrar denesin
                self._messages.pop()
                return

            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message", {}) or {}
            tool_calls = msg.get("tool_calls") or []
            content = (msg.get("content") or "").strip()

            if tool_calls:
                # Asistan mesajini ekle (tool_calls ile)
                self._messages.append({
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": tool_calls,
                })
                for call in tool_calls:
                    fn = call.get("function", {}) or {}
                    name = fn.get("name", "")
                    raw_args = fn.get("arguments", "") or ""
                    self.ui.write_debug(f"tool -> {name} {raw_args[:120]}", level="INFO")
                    result = await loop.run_in_executor(None, self._execute_tool, name, raw_args)
                    self._messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "name": name,
                        "content": result,
                    })
                # Tool sonuclariyla modeli tekrar cagir
                continue

            # Tool cagirisi yok — nihai yanit
            if content:
                self._messages.append({"role": "assistant", "content": content})
                self.ui.write_log(f"JARVIS: {content}")
                self._speak_blocking(content)
            else:
                self.ui.write_log("JARVIS: (bos yanit)")
            self._trim_history()
            self.ui.set_state("LISTENING")
            return

        self.ui.write_log("ERR: Tool cagri zinciri uzun, durduruldu.")
        self.ui.set_state("ERROR")

    def _speak_blocking(self, text: str):
        if self.ui.muted or self._interview_mode:
            return
        self.set_speaking(True)
        done = threading.Event()

        def _on_done():
            self.set_speaking(False)
            done.set()

        try:
            speak_text(text, on_done=_on_done, blocking=False)
            done.wait(timeout=60)
        except Exception:
            self.set_speaking(False)

    # ── STT dinleme dongusu ──────────────────────────────────────────────────
    def _stt_loop_thread(self, stt: _SttEngine, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, stop_event: threading.Event):
        while not stop_event.is_set():
            if self._paused or self.ui.muted:
                time.sleep(0.3)
                continue
            with self._speaking_lock:
                if self._is_speaking:
                    time.sleep(0.2)
                    continue
            ok, text = stt.listen_once(phrase_time_limit=14.0, timeout=5.0)
            if not ok:
                if text and text != "timeout":
                    self.ui.write_debug(f"STT: {text}", level="WARN")
                continue
            if not text:
                continue
            self.ui.write_log(f"Siz: {text}")
            asyncio.run_coroutine_threadsafe(queue.put(text), loop)

    # ── Ana dongu ────────────────────────────────────────────────────────────
    async def run(self):
        self._loop = asyncio.get_event_loop()
        self._pending_text_queue = asyncio.Queue()
        self.ui.write_log("SYS: Yerel mod (LM Studio) baslatildi.")
        self.ui.set_state("LISTENING")

        # STT'yi arkaplanda baslat
        language = str(get_app_config_value("stt_language", "tr-TR") or "tr-TR")
        engine = str(get_app_config_value("stt_engine", "whisper") or "whisper")
        stt = _SttEngine(language=language, engine=engine)
        if not stt._init_ok:
            self.ui.write_log(f"ERR: STT baslatilmadi — {stt._init_error}. Yine de yazi kutusunu kullanabilirsin.")

        stop_event = threading.Event()
        if stt._init_ok:
            threading.Thread(
                target=self._stt_loop_thread,
                args=(stt, self._pending_text_queue, self._loop, stop_event),
                daemon=True,
            ).start()

        try:
            while True:
                if self._paused:
                    await asyncio.sleep(0.5)
                    continue
                try:
                    text = await asyncio.wait_for(self._pending_text_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if not text:
                    continue
                try:
                    await self._handle_turn(text)
                except Exception as exc:
                    self.ui.write_log(f"ERR: Yerel mod hatasi — {exc}")
                    self.ui.set_state("ERROR")
                    traceback.print_exc()
        finally:
            stop_event.set()


# ── Connectivity test ────────────────────────────────────────────────────────

def ping_lmstudio(base_url: str | None = None, timeout: float = 5.0) -> tuple[bool, str]:
    """LM Studio sunucusuna bagliligi test eder."""
    base = (base_url or str(get_app_config_value("lmstudio_base_url", "http://127.0.0.1:1234/v1") or "")).rstrip("/")
    try:
        resp = requests.get(f"{base}/models", timeout=timeout)
        if resp.ok:
            data = resp.json()
            models = [m.get("id", "?") for m in (data.get("data") or [])]
            return True, ", ".join(models) if models else "OK"
        return False, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)
