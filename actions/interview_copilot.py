"""
R.O.S.E Silent Interview Co-Pilot & Laptop System Audio (WASAPI Loopback) Listener.

When Interview Mode is active:
1. All R.O.S.E spoken audio and notification sound effects are 100% muted/disabled.
2. Captures what the interviewer is saying directly from the Windows laptop speaker output
   via WASAPI Loopback (`pyaudiowpatch`) — works cleanly with Chrome Meet, Zoom, Teams, videos, etc.
3. Detects completed spoken questions via RMS energy + silence VAD and streams concise,
   first-person, CV-grounded written answers onto an always-on-top Teleprompter HUD + chat log.
4. Switching back ("exit interview mode" or clicking Exit on the HUD) restores normal spoken mode.
"""

from __future__ import annotations

import audioop
import ctypes
import io
import os
import queue
import re
import threading
import time
import tkinter as tk
import traceback
import wave
from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np

from app_config import get_app_config_value

try:
    import pyaudiowpatch as pya_loopback  # type: ignore
    HAS_LOOPBACK = True
except Exception:
    pya_loopback = None
    HAS_LOOPBACK = False

TARGET_RATE = 16000
# Tuples: (model_name, thinking_budget, thinking_level)
AUDIO_MODELS = (
    ("gemini-3.5-flash-lite", None, "MINIMAL"),
    ("gemini-3.6-flash", 0, None),
    ("gemini-3.5-flash", 0, None),
    ("gemini-3.1-flash-lite", None, "MINIMAL"),
    ("gemini-3.7-flash", 0, None),
)
TEXT_MODELS = (
    ("gemini-3.5-flash-lite", None, "MINIMAL"),
    ("gemini-3.6-flash", 0, None),
    ("gemini-3.5-flash", 0, None),
    ("gemini-3.1-flash-lite", None, "MINIMAL"),
    ("gemini-3.7-flash", 0, None),
)
SCREEN_MODELS = (
    ("gemini-3.5-flash-lite", None, "MINIMAL"),
    ("gemini-3.6-flash", 0, None),
    ("gemini-3.5-flash", 0, None),
    ("gemini-3.1-flash-lite", None, "MINIMAL"),
    ("gemini-3.7-flash", 0, None),
)

# High-sensitivity, ultra-low-latency VAD tuned for Google Meet / Zoom / YouTube speaker audio
SPEECH_START_RMS = 65
SPEECH_CONT_RMS = 45
SILENCE_END_SEC = 0.45
MIN_SPEECH_SEC = 0.35
MIN_PEAK_RMS = 95
MIN_WHOLE_RMS = 35
MIN_ACTIVE_FRAMES = 2
MAX_UTTERANCE_SEC = 18.0
PRE_ROLL_SEC = 0.40


def downsample_to_16k_mono(raw_pcm16: bytes, src_rate: int, src_channels: int) -> bytes:
    """
    Converts raw interleaved PCM16 audio from arbitrary WASAPI rate/channels
    (e.g. 48000Hz Stereo) down to 16000Hz Mono PCM16 cleanly via numpy.
    """
    ch = max(1, int(src_channels or 1))
    frame_bytes = 2 * ch
    usable = len(raw_pcm16) - (len(raw_pcm16) % frame_bytes)
    if usable <= 0:
        return b""

    arr = np.frombuffer(raw_pcm16[:usable], dtype=np.int16).astype(np.float32)
    if ch > 1:
        arr = arr.reshape(-1, ch).mean(axis=1)

    rate = max(8000, int(src_rate or TARGET_RATE))
    if rate != TARGET_RATE and len(arr) >= 2:
        if rate == 48000 and TARGET_RATE == 16000 and len(arr) >= 3:
            trim = (len(arr) // 3) * 3
            arr = arr[:trim].reshape(-1, 3).mean(axis=1)
        else:
            out_len = max(1, int(round(len(arr) * (TARGET_RATE / float(rate)))))
            xp = np.arange(len(arr), dtype=np.float32)
            x_new = np.linspace(0.0, float(len(arr) - 1), out_len, dtype=np.float32)
            arr = np.interp(x_new, xp, arr)

    out_i16 = np.clip(np.rint(arr), -32768.0, 32767.0).astype(np.int16)
    return out_i16.tobytes()


def pcm16_to_wav_bytes(pcm16: bytes, sample_rate: int = TARGET_RATE, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16)
    return buf.getvalue()


def normalize_pcm16_gain(pcm16: bytes, target_rms: float = 2800.0, max_gain: float = 12.0) -> bytes:
    """Boosts quiet laptop speaker audio cleanly without clipping so STT hears even soft voices."""
    if not pcm16:
        return pcm16
    usable = len(pcm16) - (len(pcm16) % 2)
    if usable <= 0:
        return b""
    arr = np.frombuffer(pcm16[:usable], dtype=np.int16).astype(np.float32)
    cur_rms = float(np.sqrt(np.mean(arr * arr))) if len(arr) else 0.0
    if cur_rms < 40.0:
        return pcm16
    peak = float(np.max(np.abs(arr))) if len(arr) else 0.0
    gain = min(max_gain, target_rms / max(cur_rms, 1.0))
    if peak > 0.0:
        gain = min(gain, 30000.0 / peak)
    if gain <= 1.05:
        return pcm16
    return np.clip(np.rint(arr * gain), -32768.0, 32767.0).astype(np.int16).tobytes()


def find_wasapi_loopback_device(pa) -> dict | None:
    """Finds the WASAPI loopback device corresponding to the current default Windows output speaker."""
    try:
        wasapi_info = pa.get_host_api_info_by_type(pya_loopback.paWASAPI)
        def_idx = int(wasapi_info.get("defaultOutputDevice", -1))
        def_name = ""
        if def_idx >= 0:
            default_speakers = pa.get_device_info_by_index(def_idx)
            def_name = str(default_speakers.get("name", "") or "")
            if default_speakers.get("isLoopbackDevice"):
                return default_speakers

        loopbacks = list(pa.get_loopback_device_info_generator())
        if def_name:
            for lb in loopbacks:
                if def_name in str(lb.get("name", "")):
                    return lb
        for lb in loopbacks:
            if int(lb.get("maxInputChannels", 0) or 0) > 0:
                return lb
    except Exception:
        pass
    return None


def _load_candidate_context() -> str:
    """Loads the user's CV text and identity facts from memory so answers sound genuinely like them."""
    chunks: list[str] = []
    try:
        from memory.memory_manager import load_memory

        mem = load_memory()
        identity = mem.get("identity") or {}
        if identity:
            pairs = [
                f"{k}: {v.get('value') if isinstance(v, dict) else v}"
                for k, v in identity.items()
            ]
            chunks.append("Candidate Profile: " + "; ".join(pairs))
    except Exception:
        pass

    try:
        from actions.job_assistant import PROFILE_FILE, _load_json, _get_resume_text

        profile = _load_json(PROFILE_FILE, {})
        if profile:
            prof_bits = [
                f"{k}: {v}"
                for k, v in profile.items()
                if k != "resume_path" and v
            ]
            if prof_bits:
                chunks.append("Saved Job Profile: " + "; ".join(prof_bits))
            cv_text = _get_resume_text(profile)
            if cv_text:
                chunks.append("Candidate CV / Resume Excerpt:\n" + cv_text[:2800])
    except Exception:
        pass

    return "\n\n".join(chunks).strip()


class InterviewCopilot:
    """Silent Live Interview Teleprompter HUD + WASAPI Loopback Question Answerer."""

    def __init__(
        self,
        root: tk.Tk | None = None,
        write_log_cb: Callable[[str], None] | None = None,
        on_mode_change: Callable[[bool], None] | None = None,
    ):
        self.root = root
        self.write_log_cb = write_log_cb
        self.on_mode_change = on_mode_change

        self.active = False
        self.also_mic = False
        self.role = ""
        self.company = ""
        self.device_label = "Not started"
        self.candidate_context = ""
        self.history: deque[tuple[str, str]] = deque(maxlen=4)
        self.last_answer_text = ""

        self._stop_event = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._mic_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._task_queue: queue.Queue[tuple[str, bytes | str | dict]] = queue.Queue(maxsize=8)
        self._lock = threading.Lock()
        self._vu_level = 0.0
        self._genai_clients: dict[int, object] = {}
        self._genai_key = ""
        self._model_cooldowns: dict[str, float] = {}

        # Tkinter HUD references (only touched on Tk main thread)
        self.win: tk.Toplevel | None = None
        self.text_box: tk.Text | None = None
        self.status_lbl: tk.Label | None = None
        self.vu_canvas: tk.Canvas | None = None
        self.mic_btn: tk.Button | None = None
        self.role_entry: tk.Entry | None = None
        self.ask_entry: tk.Entry | None = None
        self._hud_update_job = None

    # ── Logging & UI Thread Helpers ──────────────────────────────────────────

    def _log_main(self, msg: str) -> None:
        if self.write_log_cb:
            try:
                self.write_log_cb(msg)
            except Exception:
                pass

    def _ui_call(self, fn: Callable[[], None]) -> None:
        if not self.root:
            self._log_main("ERR: Interview UI root is not initialized yet.")
            return

        def _safe_run():
            try:
                fn()
            except Exception as exc:
                self._log_main(f"ERR: Interview HUD UI error: {exc}")
                traceback.print_exc()

        try:
            from ui.tk_bridge import ensure_tk_root, is_tk_thread, schedule_tk_call

            tk_root = ensure_tk_root(self.root)
            if tk_root is not None:
                self.root = tk_root
            if is_tk_thread(self.root):
                _safe_run()
            else:
                schedule_tk_call(self.root, _safe_run)
        except RuntimeError:
            pass
        except Exception as exc:
            self._log_main(f"ERR: Interview HUD dispatch failed: {exc}")
            traceback.print_exc()

    def _set_status(self, text: str, color: str = "#00d4ff") -> None:
        def _upd():
            if self.status_lbl and self.status_lbl.winfo_exists():
                self.status_lbl.config(text=text, fg=color)

        self._ui_call(_upd)

    def _append_hud(self, text: str, tag: str = "body") -> None:
        def _ins():
            if self.text_box and self.text_box.winfo_exists():
                self.text_box.config(state="normal")
                self.text_box.insert("end", text, tag)
                self.text_box.see("end")
                self.text_box.config(state="disabled")

        self._ui_call(_ins)

    def _clear_hud_text(self) -> None:
        def _clr():
            if self.text_box and self.text_box.winfo_exists():
                self.text_box.config(state="normal")
                self.text_box.delete("1.0", "end")
                self.text_box.config(state="disabled")

        self._ui_call(_clr)

    # ── Prompt Construction & Gemini Streaming ───────────────────────────────

    def _build_system_rules(self, include_header: bool = True) -> str:
        role_line = f"Target Role: {self.role}" if self.role else "Target Role: Software / Cybersecurity / Tech Role"
        comp_line = f"Company: {self.company}" if self.company else ""
        ctx = self.candidate_context or "(No CV text loaded yet — answer as a strong software & cybersecurity engineer.)"

        hist_block = ""
        if self.history:
            recent = "\n".join(f"- Interviewer: {q}\n  Answer focus: {a[:180]}" for q, a in self.history)
            hist_block = f"\nRecent Interview Exchanges (for follow-up continuity):\n{recent}\n"

        header_rules = (
            "2. First line MUST start with `Q: ` followed by a concise summary/transcription of the question or problem.\n"
            "3. Second line MUST be `---`.\n"
            if include_header
            else "2. Do NOT repeat the question or `Q:` or `---` — start immediately with the answer bullet points.\n"
        )

        return (
            "You are silently coaching the candidate live during an interview.\n"
            f"{role_line}\n{comp_line}\n\n"
            f"{ctx}\n"
            f"{hist_block}\n"
            "STRICT OUTPUT FORMAT:\n"
            "1. Output ONLY clean, ready-to-read English text.\n"
            f"{header_rules}"
            "3. For spoken/behavioral/conceptual questions: write 3 to 4 crisp, high-impact first-person (`I ...`) bullet points (`• ...`) "
            "anchored in the candidate's CV/projects above (~55-100 words, natural and confident).\n"
            "4. EXCEPTION FOR CODING, SQL, DEBUGGING, MATH, OR MULTIPLE-CHOICE TESTS: give a 2-bullet explanation (approach + Time/Space complexity), "
            "followed by the complete, clean, runnable solution code inside a fenced code block (or exact final answer for MCQ/short-answer).\n"
        )

    @staticmethod
    def _parse_q_and_answer(raw: str, fallback_q: str = "") -> tuple[str, str]:
        txt = (raw or "").strip()
        if "---" in txt:
            head, body = txt.split("---", 1)
            q_line = re.sub(r"^Q:\s*", "", head.strip(), flags=re.IGNORECASE).strip()
            return (q_line or fallback_q, body.strip())
        lines = [ln for ln in txt.splitlines() if ln.strip()]
        if lines and lines[0].lower().startswith("q:"):
            q_line = lines[0][2:].strip()
            body = "\n".join(lines[1:]).strip()
            return (q_line or fallback_q, body or txt)
        return (fallback_q or "Interview Question", txt)

    def _get_genai_client(self, timeout_ms: int = 11000):
        api_key = str(get_app_config_value("gemini_api_key", "") or "").strip()
        if not api_key:
            return None
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore

        if self._genai_key != api_key:
            self._genai_key = api_key
            self._genai_clients.clear()

        t_ms = int(timeout_ms)
        client = self._genai_clients.get(t_ms)
        if client is None:
            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=t_ms,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
            self._genai_clients[t_ms] = client
        return client

    def _reset_genai_client(self, timeout_ms: int | None = None) -> None:
        if timeout_ms is None:
            self._genai_clients.clear()
        else:
            self._genai_clients.pop(int(timeout_ms), None)

    @staticmethod
    def _build_gen_config(
        thinking_budget: int | None = None,
        max_output_tokens: int | None = 360,
        thinking_level: str | None = None,
    ):
        from google.genai import types  # type: ignore

        kwargs: dict = {}
        if thinking_budget is not None:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)
        elif thinking_level is not None:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)
        if max_output_tokens is not None:
            kwargs["max_output_tokens"] = max_output_tokens
        return types.GenerateContentConfig(**kwargs) if kwargs else None

    def _mark_model_cooldown(self, model_name: str, exc: Exception) -> None:
        msg = str(exc or "").lower()
        now = time.monotonic()
        if "429" in msg or "resource_exhausted" in msg:
            cooldown = 1800.0 if ("per day" in msg or "daily" in msg) else 55.0
            self._model_cooldowns[model_name] = now + cooldown
        elif "503" in msg or "unavailable" in msg:
            self._model_cooldowns[model_name] = now + 18.0

    def _active_models(self, models: tuple[tuple[str, int | None, str | None], ...]) -> list[tuple[str, int | None, str | None]]:
        now = time.monotonic()
        ready = [m for m in models if self._model_cooldowns.get(m[0], 0.0) <= now]
        return ready if ready else list(models)

    def _warm_genai_client_bg(self) -> None:
        def _warm():
            try:
                import socket
                import ssl

                self._get_genai_client(11000)
                # Zero-quota DNS + TCP + TLS handshake warmup so first real question connects instantly
                ctx = ssl.create_default_context()
                with socket.create_connection(("generativelanguage.googleapis.com", 443), timeout=4.0) as sock:
                    with ctx.wrap_socket(sock, server_hostname="generativelanguage.googleapis.com"):
                        pass
            except Exception:
                pass

        threading.Thread(target=_warm, daemon=True, name="ROSE-InterviewWarm").start()

    def _new_fresh_client(self, timeout_ms: int = 11000):
        api_key = str(get_app_config_value("gemini_api_key", "") or "").strip()
        if not api_key:
            return None
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore

        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_ms),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    def _stream_hedged_text(
        self,
        contents,
        models: tuple[tuple[str, int | None, str | None], ...],
        max_output_tokens: int = 360,
        hedge_delay_sec: float = 2.8,
        pre_stream_header: tuple[str, str] | None = None,
    ) -> tuple[str, Exception | None]:
        """
        Streams from the fastest healthy model with hedged TTFB failover & 429/503 cooldowns:
        - Skips models known to be daily/RPM quota-exhausted in 0ms.
        - Launches primary immediately (1 request on normal calls, zero extra quota).
        - If primary stalls > hedge_delay_sec without a first token OR fails early, launches
          the next healthy model on a fresh socket concurrently; the first token wins cleanly.
        """
        candidates = self._active_models(models)
        winner_lock = threading.Lock()
        winner_idx: list[int | None] = [None]
        first_token_evt = threading.Event()
        errors: dict[int, Exception] = {}
        bodies: dict[int, str] = {}
        threads: list[threading.Thread] = []
        done_evts: list[threading.Event] = []

        def _run_candidate(idx: int, m_name: str, tb: int | None, tl: str | None, use_shared: bool) -> None:
            done_e = done_evts[idx]
            try:
                cli = self._get_genai_client(11000) if use_shared else self._new_fresh_client(11000)
                if cli is None:
                    return
                cfg = self._build_gen_config(tb, max_output_tokens=max_output_tokens, thinking_level=tl)
                chunks: list[str] = []
                i_am_winner = False
                for ch in cli.models.generate_content_stream(model=m_name, contents=contents, config=cfg):
                    delta = getattr(ch, "text", None)
                    if not delta:
                        continue
                    with winner_lock:
                        if winner_idx[0] is None:
                            winner_idx[0] = idx
                            i_am_winner = True
                            first_token_evt.set()
                            if pre_stream_header:
                                self._append_hud(pre_stream_header[0], pre_stream_header[1])
                        elif winner_idx[0] != idx:
                            break
                    chunks.append(delta)
                    self._append_hud(delta, "answer")
                body = "".join(chunks).strip()
                if i_am_winner:
                    if body:
                        self._append_hud("\n", "answer")
                        bodies[idx] = body
                    else:
                        with winner_lock:
                            if winner_idx[0] == idx:
                                winner_idx[0] = None
                                first_token_evt.clear()
            except Exception as exc:
                errors[idx] = exc
                self._mark_model_cooldown(m_name, exc)
                if use_shared:
                    self._reset_genai_client(11000)
            finally:
                done_e.set()

        for idx, (m_name, tb, tl) in enumerate(candidates):
            done_e = threading.Event()
            done_evts.append(done_e)
            t = threading.Thread(
                target=_run_candidate,
                args=(idx, m_name, tb, tl, idx == 0),
                daemon=True,
                name=f"ROSE-Stream-{idx}",
            )
            threads.append(t)
            t.start()

            deadline = time.monotonic() + (hedge_delay_sec if idx == 0 else 2.2)
            while time.monotonic() < deadline:
                if first_token_evt.is_set() or done_e.is_set():
                    break
                time.sleep(0.03)

            if first_token_evt.is_set():
                win = winner_idx[0]
                if win is not None:
                    done_evts[win].wait(timeout=11.0)
                    if bodies.get(win):
                        return bodies[win], None
                first_token_evt.clear()

        if not first_token_evt.is_set():
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not first_token_evt.is_set():
                if all(e.is_set() for e in done_evts):
                    break
                time.sleep(0.04)

        win = winner_idx[0]
        if win is not None:
            done_evts[win].wait(timeout=11.0)
            if bodies.get(win):
                return bodies[win], None

        last_err = errors.get(len(candidates) - 1) or (next(iter(errors.values()), None))
        return "", last_err

    @staticmethod
    def _transcribe_fast_sr(pcm16_mono: bytes) -> str | None:
        """
        Ultra-fast speech-to-text via Google Web Speech (~0.8-1.5s, zero Gemini quota).
        Returns:
          - stripped transcript string on real speech,
          - "" when audio has no recognizable speech (UnknownValueError),
          - None if network/library unavailable (triggers Gemini fallback).
        """
        try:
            import speech_recognition as sr_lib  # type: ignore

            rec = sr_lib.Recognizer()
            rec.operation_timeout = 3.5
            audio = sr_lib.AudioData(pcm16_mono, TARGET_RATE, 2)
            txt = rec.recognize_google(audio, language="en-US")
            return " ".join(str(txt or "").split())
        except Exception as exc:
            if type(exc).__name__ == "UnknownValueError":
                return ""
            return None

    @staticmethod
    def _transcribe_audio_only(client, wav_bytes: bytes) -> str:
        """
        Fallback Transcription Gate: Transcribe ONLY the raw audio clip with NO CV
        and NO conversation history in context, so silence/static/clicks can NEVER invent questions.
        """
        from google.genai import types  # type: ignore

        transcribe_prompt = (
            "Listen strictly to this short audio clip.\n"
            "- If you hear clear, intelligible human speech (someone speaking a question, statement, or prompt), "
            "output ONLY the exact verbatim English words they spoke.\n"
            "- If the audio contains ONLY silence, static, electrical hum, room fan, keyboard clicks, "
            "notification chimes, music without speech, or unintelligible mumbling, output EXACTLY `[SILENCE]` and nothing else.\n"
            "- NEVER invent, guess, or fabricate words or questions that were not clearly spoken aloud."
        )

        for m_name, tb, tl in AUDIO_MODELS:
            try:
                cfg = InterviewCopilot._build_gen_config(tb, max_output_tokens=160, thinking_level=tl)
                resp = client.models.generate_content(
                    model=m_name,
                    contents=[
                        types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav"),
                        transcribe_prompt,
                    ],
                    config=cfg,
                )
                txt = (getattr(resp, "text", None) or "").strip()
                if not txt:
                    continue
                upper = txt.upper()
                if any(tok in upper for tok in ("[SILENCE]", "[NO_SPEECH]", "[NO SPEECH]", "[INAUDIBLE]", "[NO_QUESTION]", "[MUSIC]", "[NOISE]")):
                    return ""
                words = re.findall(r"[A-Za-z]{2,}", txt)
                if len(words) < 2:
                    return ""
                return " ".join(txt.split())
            except Exception:
                continue
        return ""

    def _stream_answer_from_audio(self, payload_audio: bytes, raw_peak_rms: int = 600) -> str:
        client = self._get_genai_client()
        if client is None:
            return "[NO_KEY]"

        # Accept either raw PCM16 mono bytes (preferred fast path) or WAV bytes
        if payload_audio.startswith(b"RIFF"):
            wav_bytes = payload_audio
            try:
                with wave.open(io.BytesIO(payload_audio), "rb") as wf:
                    pcm16_mono = wf.readframes(wf.getnframes())
            except Exception:
                pcm16_mono = payload_audio[44:]
        else:
            pcm16_mono = normalize_pcm16_gain(payload_audio)
            wav_bytes = pcm16_to_wav_bytes(pcm16_mono, TARGET_RATE, 1)

        # Stage 1: Fast Google STT (~1s) with zero hallucination; falls back to Gemini Audio only when needed
        transcript = self._transcribe_fast_sr(pcm16_mono)
        if transcript == "":
            # Only double-check with Gemini if the raw signal actually had strong voice energy
            if raw_peak_rms >= 550:
                transcript = self._transcribe_audio_only(client, wav_bytes)
            else:
                return "[NO_QUESTION]"
        elif transcript is None:
            transcript = self._transcribe_audio_only(client, wav_bytes)

        if not transcript or len(re.findall(r"[A-Za-z]{2,}", transcript)) < 2:
            return "[NO_QUESTION]"

        # Ignore tiny non-question filler utterances
        low_clean = transcript.strip(" .!?,\"'").lower()
        if low_clean in {"thank you", "thanks", "okay", "ok", "hello", "hi", "yeah", "yes", "no", "hmm", "uh", "um", "one second", "hold on", "testing", "test"}:
            return "[NO_QUESTION]"

        # Immediately show verified spoken question on HUD while answer streams (~0.8s TTFB)
        stamp = time.strftime("%H:%M:%S")
        self._append_hud(f"\n[{stamp}] ", "meta")
        self._append_hud(f"Q: {transcript}\n---\n", "answer")

        # Stage 2: Stream tailored CV-backed answer with hedged TTFB failover
        prompt = self._build_system_rules(include_header=False) + f"\nInterviewer asked:\n{transcript}\n"
        body, last_err = self._stream_hedged_text(prompt, TEXT_MODELS, max_output_tokens=360, hedge_delay_sec=3.0)
        if body:
            return f"Q: {transcript}\n---\n{body}"

        if last_err:
            self._append_hud(f"\n[Audio Answer Error: {str(last_err)[:120]}]\n", "err")
        return ""

    def _stream_answer_from_text(self, question_text: str) -> str:
        if self._get_genai_client(11000) is None:
            return "No Gemini API key configured."
        prompt = self._build_system_rules() + f"\nInterviewer asked:\n{question_text.strip()}\n"
        stamp = time.strftime("%H:%M:%S")
        body, last_err = self._stream_hedged_text(
            prompt,
            TEXT_MODELS,
            max_output_tokens=650,
            hedge_delay_sec=3.2,
            pre_stream_header=(f"\n[{stamp}] ", "meta"),
        )
        if body:
            return body
        err_msg = f"Failed to generate answer: {last_err}"
        self._append_hud(f"\n[{err_msg}]\n", "err")
        return err_msg

    def _stream_answer_from_screen(self, frames: list[dict], extra_note: str = "") -> str:
        client = self._get_genai_client(timeout_ms=25000)
        if client is None:
            return "No Gemini API key configured."

        from google.genai import types  # type: ignore
        from PIL import Image  # type: ignore
        from actions.screen_vision import _prepare_image_bytes

        parts: list[types.Part] = []
        labels_info: list[str] = []
        temp_paths: list[Path] = []
        try:
            for idx, frame in enumerate(frames, start=1):
                label = str(frame.get("label") or f"Monitor {idx}")
                img_path_str = frame.get("image_path")
                pil_img = frame.get("image")
                if img_path_str:
                    p = Path(img_path_str)
                    if p.exists():
                        temp_paths.append(p)
                        raw_bytes, mime = _prepare_image_bytes(p)
                        parts.append(types.Part.from_bytes(data=raw_bytes, mime_type=mime))
                        labels_info.append(label)
                elif pil_img is not None:
                    work = pil_img.convert("RGB") if pil_img.mode not in ("RGB", "L") else pil_img.copy()
                    if max(work.size) > 1800:
                        work.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
                    buf = io.BytesIO()
                    work.save(buf, format="PNG", optimize=True)
                    parts.append(types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"))
                    labels_info.append(f"{label} ({work.width}x{work.height})")
        finally:
            for tp in temp_paths:
                try:
                    tp.unlink(missing_ok=True)
                except Exception:
                    pass

        if not parts:
            err = "Could not capture screen frames for Interview HUD."
            self._append_hud(f"\n[{err}]\n", "err")
            return err

        note_line = f"Additional user hint: {extra_note.strip()}\n" if extra_note.strip() else ""
        prompt = (
            self._build_system_rules()
            + "\nANALYZE THE ATTACHED SCREENSHOT(S) OF THE CANDIDATE'S SCREEN(S):\n"
            + f"Visible displays attached in order: {', '.join(labels_info)}.\n"
            + note_line
            + "- Find the active interview prompt, coding problem (LeetCode/CoderPad/HackerRank/IDE), assessment question, MCQ, error/bug, or interviewer chat message on screen.\n"
            + "- Summarize the question after `Q: `, output `---`, then provide the crisp explanation + exact solution code or exact answer.\n"
        )

        last_err = None
        for m_name, tb, tl in self._active_models(SCREEN_MODELS):
            try:
                cur_client = self._get_genai_client(timeout_ms=25000)
                if cur_client is None:
                    break
                cfg = self._build_gen_config(tb, max_output_tokens=900, thinking_level=tl)
                stamp = time.strftime("%H:%M:%S")
                self._append_hud(f"\n[{stamp} · SCREEN] ", "meta")
                full_chunks: list[str] = []
                for ch in cur_client.models.generate_content_stream(
                    model=m_name,
                    contents=[*parts, prompt],
                    config=cfg,
                ):
                    delta = getattr(ch, "text", None)
                    if not delta:
                        continue
                    full_chunks.append(delta)
                    self._append_hud(delta, "answer")
                self._append_hud("\n", "answer")
                body = "".join(full_chunks).strip()
                if body:
                    return body
            except Exception as exc:
                last_err = exc
                self._mark_model_cooldown(m_name, exc)
                self._reset_genai_client(25000)
                continue
        err_msg = f"Failed to solve screen question: {last_err}"
        self._append_hud(f"\n[{err_msg}]\n", "err")
        return err_msg

    # ── Worker Thread ────────────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                kind, payload = self._task_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            if not self.active or self._stop_event.is_set():
                continue

            try:
                self._set_status("THINKING (SILENT)...", "#ffcc00")
                if kind == "audio":
                    if isinstance(payload, dict):
                        pcm_raw = bytes(payload.get("pcm", b""))
                        pk_rms = int(payload.get("peak_rms", 600))
                    else:
                        pcm_raw = bytes(payload)
                        pk_rms = 600
                    raw_out = self._stream_answer_from_audio(pcm_raw, raw_peak_rms=pk_rms)
                    if raw_out and raw_out != "[NO_QUESTION]":
                        q_txt, ans_txt = self._parse_q_and_answer(raw_out)
                        self.last_answer_text = ans_txt or raw_out
                        self.history.append((q_txt, self.last_answer_text))
                        self._log_main(f"INTERVIEW Q: {q_txt}")
                        self._log_main(f"ROSE (SILENT): {self.last_answer_text}")
                elif kind == "text" and isinstance(payload, str):
                    raw_out = self._stream_answer_from_text(payload)
                    if raw_out:
                        q_txt, ans_txt = self._parse_q_and_answer(raw_out, fallback_q=payload)
                        self.last_answer_text = ans_txt or raw_out
                        self.history.append((q_txt, self.last_answer_text))
                        self._log_main(f"INTERVIEW Q: {q_txt}")
                        self._log_main(f"ROSE (SILENT): {self.last_answer_text}")
                elif kind == "screen" and isinstance(payload, dict):
                    frames = payload.get("frames") or []
                    note = str(payload.get("note") or "")
                    raw_out = self._stream_answer_from_screen(frames, extra_note=note)
                    if raw_out:
                        q_txt, ans_txt = self._parse_q_and_answer(raw_out, fallback_q="On-Screen Problem")
                        self.last_answer_text = ans_txt or raw_out
                        self.history.append((q_txt, self.last_answer_text))
                        self._log_main(f"INTERVIEW SCREEN Q: {q_txt}")
                        self._log_main(f"ROSE (SILENT): {self.last_answer_text}")
            except Exception as exc:
                self._append_hud(f"\n[Copilot Error: {exc}]\n", "err")
            finally:
                if self.active:
                    self._set_status(f"LISTENING LOOPBACK · {self.device_label[:34]}", "#00e676")

    def _enqueue_audio(self, pcm16_mono: bytes, peak_rms: int = 600) -> None:
        if self._task_queue.full():
            try:
                self._task_queue.get_nowait()
            except Exception:
                pass
        try:
            self._task_queue.put_nowait(("audio", {"pcm": pcm16_mono, "peak_rms": int(peak_rms)}))
        except Exception:
            pass

    def ask_text(self, question: str) -> str:
        q = (question or "").strip()
        if not q:
            return "Please provide a question."
        if q.lower() in ("clipboard", "from_clipboard"):
            try:
                from actions.clipboard_copilot import read_clipboard

                q = read_clipboard().strip()
            except Exception:
                q = ""
            if not q:
                return "Clipboard is empty."
        if not self.active:
            self.start()
        if self._task_queue.full():
            try:
                self._task_queue.get_nowait()
            except Exception:
                pass
        self._task_queue.put_nowait(("text", q))
        return f"Drafting silent interview answer for: {q[:80]}"

    def ask_screen(self, note: str = "") -> str:
        """Captures all visible monitors (temporarily hiding HUD so it doesn't block text) and solves the on-screen question silently."""
        if not self.active:
            self.start()

        def _capture_frames() -> list[dict]:
            from actions.screen_vision import _capture_targets

            was_visible = False
            try:
                if self.win and self.win.winfo_exists() and self.win.winfo_ismapped():
                    was_visible = True
                    self.win.withdraw()
                    self.win.update_idletasks()
            except Exception:
                pass
            ok_cap, _err_msg, frames = _capture_targets("all_monitors")
            if was_visible:
                try:
                    if self.win and self.win.winfo_exists():
                        self._force_foreground_win32(self.win)
                except Exception:
                    pass
            return frames if ok_cap else []

        frames: list[dict] = []
        from ui.tk_bridge import is_tk_thread

        if is_tk_thread(self.root) or not self.root:
            frames = _capture_frames()
        else:
            done_evt = threading.Event()
            holder: dict[str, list[dict]] = {"frames": []}

            def _ui_cap():
                try:
                    holder["frames"] = _capture_frames()
                finally:
                    done_evt.set()

            self._ui_call(_ui_cap)
            done_evt.wait(timeout=3.0)
            frames = holder["frames"]

        if not frames:
            msg = "Could not capture screen for Interview HUD."
            self._append_hud(f"\n[{msg}]\n", "err")
            return msg

        if self._task_queue.full():
            try:
                self._task_queue.get_nowait()
            except Exception:
                pass
        self._task_queue.put_nowait(("screen", {"frames": frames, "note": (note or "").strip()}))
        return f"Capturing {len(frames)} monitor(s) and solving on-screen interview question silently..."

    # ── WASAPI Loopback Capture Loop (Non-blocking Callback + Timeout VAD) ───

    def _loopback_loop(self) -> None:
        try:
            ctypes.windll.ole32.CoInitialize(0)
        except Exception:
            pass

        if not HAS_LOOPBACK:
            self.device_label = "pyaudiowpatch missing"
            self._set_status("LOOPBACK UNAVAILABLE (install pyaudiowpatch)", "#ff3344")
            return

        pa = None
        stream = None
        pkt_queue: queue.Queue[bytes] = queue.Queue(maxsize=128)
        pre_roll: deque[bytes] = deque(maxlen=8)
        speech_buf = bytearray()
        in_speech = False
        speech_start_ts = 0.0
        last_voice_ts = 0.0
        peak_rms = 0
        active_frames = 0
        noise_floor = 25.0

        try:
            pa = pya_loopback.PyAudio()
            dev = find_wasapi_loopback_device(pa)
            if not dev:
                self.device_label = "No WASAPI Loopback device found"
                self._set_status("NO LOOPBACK AUDIO DEVICE FOUND", "#ff3344")
                return

            dev_idx = int(dev["index"])
            src_rate = int(dev.get("defaultSampleRate", 48000) or 48000)
            src_ch = max(1, int(dev.get("maxInputChannels", 2) or 2))
            self.device_label = str(dev.get("name", "Speakers [Loopback]"))
            self._set_status(f"LISTENING LOOPBACK · {self.device_label[:34]}", "#00e676")

            frames_per_buf = max(320, int(src_rate * 0.05))

            def _cb(in_data, frame_count, time_info, status):
                if in_data and not self._stop_event.is_set():
                    if pkt_queue.full():
                        try:
                            pkt_queue.get_nowait()
                        except Exception:
                            pass
                    try:
                        pkt_queue.put_nowait(in_data)
                    except Exception:
                        pass
                return (None, pya_loopback.paContinue)

            stream = pa.open(
                format=pya_loopback.paInt16,
                channels=src_ch,
                rate=src_rate,
                input=True,
                input_device_index=dev_idx,
                frames_per_buffer=frames_per_buf,
                stream_callback=_cb,
            )
            stream.start_stream()

            def _flush_utterance() -> None:
                nonlocal in_speech, speech_buf, peak_rms, active_frames
                dur = len(speech_buf) / (TARGET_RATE * 2.0)
                whole_rms = audioop.rms(bytes(speech_buf), 2) if speech_buf else 0
                if (
                    dur >= MIN_SPEECH_SEC
                    and active_frames >= MIN_ACTIVE_FRAMES
                    and peak_rms >= max(MIN_PEAK_RMS, min(320, int(noise_floor * 3.0)))
                    and whole_rms >= MIN_WHOLE_RMS
                ):
                    pcm_copy = bytes(speech_buf)
                    self._enqueue_audio(pcm_copy, peak_rms=peak_rms)
                speech_buf.clear()
                in_speech = False
                peak_rms = 0
                active_frames = 0

            while not self._stop_event.is_set():
                now = time.monotonic()
                try:
                    raw = pkt_queue.get(timeout=0.06)
                except queue.Empty:
                    self._vu_level = max(0.0, self._vu_level * 0.65)
                    if in_speech and (now - last_voice_ts) >= SILENCE_END_SEC:
                        _flush_utterance()
                    continue

                mono16 = downsample_to_16k_mono(raw, src_rate, src_ch)
                if not mono16:
                    continue

                rms = audioop.rms(mono16, 2)
                self._vu_level = min(1.0, rms / 900.0)
                dyn_start = max(SPEECH_START_RMS, min(260, int(noise_floor * 2.5)))
                dyn_cont = max(SPEECH_CONT_RMS, min(180, int(noise_floor * 1.8)))

                if not in_speech:
                    # Track slow background noise floor only when below trigger threshold
                    noise_floor = (noise_floor * 0.94) + (min(rms, dyn_start) * 0.06)
                    pre_roll.append(mono16)
                    if rms >= dyn_start:
                        in_speech = True
                        speech_start_ts = now
                        last_voice_ts = now
                        peak_rms = rms
                        active_frames = 1
                        for prev in pre_roll:
                            speech_buf.extend(prev)
                        pre_roll.clear()
                else:
                    speech_buf.extend(mono16)
                    if rms > peak_rms:
                        peak_rms = rms
                    if rms >= dyn_cont:
                        last_voice_ts = now
                        active_frames += 1

                    if (now - last_voice_ts) >= SILENCE_END_SEC or (now - speech_start_ts) >= MAX_UTTERANCE_SEC:
                        _flush_utterance()

        except Exception as exc:
            self.device_label = f"Loopback error: {exc}"
            self._set_status(f"LOOPBACK ERROR: {str(exc)[:40]}", "#ff3344")
        finally:
            self._vu_level = 0.0
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:
                    pass

    def _optional_mic_loop(self) -> None:
        """Optional secondary microphone capture (only when user enables 'Also Mic' for in-room testing)."""
        import pyaudio  # type: ignore

        pa = None
        stream = None
        pre_roll: deque[bytes] = deque(maxlen=6)
        speech_buf = bytearray()
        in_speech = False
        speech_start_ts = 0.0
        last_voice_ts = 0.0
        peak_rms = 0
        active_frames = 0
        noise_floor = 30.0

        try:
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=TARGET_RATE,
                input=True,
                frames_per_buffer=1024,
            )
            while not self._stop_event.is_set():
                if not self.also_mic:
                    if in_speech:
                        speech_buf.clear()
                        in_speech = False
                        peak_rms = 0
                    time.sleep(0.15)
                    continue
                raw = stream.read(1024, exception_on_overflow=False)
                now = time.monotonic()
                rms = audioop.rms(raw, 2) if raw else 0
                if rms / 1800.0 > self._vu_level:
                    self._vu_level = min(1.0, rms / 1800.0)
                if not in_speech:
                    pre_roll.append(raw)
                    if rms >= max(SPEECH_START_RMS, 90):
                        in_speech = True
                        speech_start_ts = now
                        last_voice_ts = now
                        peak_rms = rms
                        for p in pre_roll:
                            speech_buf.extend(p)
                        pre_roll.clear()
                else:
                    speech_buf.extend(raw)
                    peak_rms = max(peak_rms, rms)
                    if rms >= max(SPEECH_CONT_RMS, 60):
                        last_voice_ts = now
                    if (now - last_voice_ts) >= SILENCE_END_SEC or (now - speech_start_ts) >= MAX_UTTERANCE_SEC:
                        dur = len(speech_buf) / (TARGET_RATE * 2.0)
                        if dur >= MIN_SPEECH_SEC and peak_rms >= MIN_PEAK_RMS:
                            self._enqueue_audio(bytes(speech_buf), peak_rms=peak_rms)
                        speech_buf.clear()
                        in_speech = False
                        peak_rms = 0
        except Exception:
            pass
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:
                    pass

    # ── Tkinter Teleprompter HUD ─────────────────────────────────────────────

    @staticmethod
    def _resolve_toplevel_hwnd(tk_win) -> int:
        if not tk_win:
            return 0
        try:
            tk_win.update_idletasks()
            wid = int(tk_win.winfo_id())
            user32 = ctypes.windll.user32
            anc = int(user32.GetAncestor(wid, 2) or 0)  # GA_ROOT = 2
            if anc:
                return anc
            parent = int(user32.GetParent(wid) or 0)
            if parent:
                return parent
            fh = tk_win.wm_frame()
            if fh and fh != "0x0":
                return int(fh, 16)
            return wid
        except Exception:
            return 0

    def _force_foreground_win32(self, win: tk.Toplevel) -> None:
        try:
            win.update_idletasks()
            win.state("normal")
            win.deiconify()
            win.lift()
            win.attributes("-topmost", True)
            win.focus_force()
        except Exception:
            pass
        try:
            hwnd = self._resolve_toplevel_hwnd(win)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        self._apply_hud_stealth()

    def _apply_hud_stealth(self) -> bool:
        if not self.win or not self.win.winfo_exists():
            return False
        ok = set_window_capture_excluded(self.win, True)
        lbl = getattr(self, "shield_lbl", None)
        if lbl and lbl.winfo_exists():
            try:
                if ok:
                    lbl.config(text="🛡 SCREEN-SHARE HIDDEN", fg="#00e676", bg="#0c261c")
                else:
                    lbl.config(text="⚠ SHARE CHECK", fg="#ffcc00", bg="#2a2109")
            except Exception:
                pass
        return ok

    def _cycle_monitor(self) -> None:
        if not self.win or not self.win.winfo_exists():
            return
        try:
            from actions.screen_vision import enumerate_monitors

            mons = enumerate_monitors()
            if not mons:
                return
            cx = self.win.winfo_x() + (self.win.winfo_width() // 2)
            cy = self.win.winfo_y() + (self.win.winfo_height() // 2)
            cur_idx = 0
            for idx, m in enumerate(mons):
                if m["left"] <= cx < m["right"] and m["top"] <= cy < m["bottom"]:
                    cur_idx = idx
                    break
            nxt = mons[(cur_idx + 1) % len(mons)]
            w = max(480, min(self.win.winfo_width() or 640, (nxt["right"] - nxt["left"]) - 36))
            h = max(380, min(self.win.winfo_height() or 500, (nxt["bottom"] - nxt["top"]) - 72))
            x = max(nxt["left"] + 16, nxt["right"] - w - 28)
            y = nxt["top"] + 42
            self.win.geometry(f"{w}x{h}+{x}+{y}")
            self._apply_hud_stealth()
        except Exception:
            pass

    def _pick_hud_geometry(self, win: tk.Toplevel, w: int = 640, h: int = 500) -> str:
        """Choose guaranteed on-screen HUD coordinates across single or multi-monitor desktops."""
        mon_rect = None
        try:
            from actions.screen_vision import enumerate_monitors

            monitors = enumerate_monitors()
            if monitors:
                ref_x = None
                ref_y = None
                try:
                    if self.root and self.root.winfo_ismapped():
                        ref_x = self.root.winfo_rootx() + (self.root.winfo_width() // 2)
                        ref_y = self.root.winfo_rooty() + (self.root.winfo_height() // 2)
                except Exception:
                    pass
                if ref_x is None:
                    try:
                        ref_x = win.winfo_pointerx()
                        ref_y = win.winfo_pointery()
                    except Exception:
                        pass
                if ref_x is not None and ref_y is not None:
                    for m in monitors:
                        l, t, r, b = m["left"], m["top"], m["right"], m["bottom"]
                        if l <= ref_x < r and t <= ref_y < b:
                            mon_rect = (l, t, r, b)
                            break
                if mon_rect is None:
                    primary = next((m for m in monitors if m["left"] == 0 and m["top"] == 0), monitors[0])
                    mon_rect = (primary["left"], primary["top"], primary["right"], primary["bottom"])
        except Exception:
            mon_rect = None

        if mon_rect:
            l, t, r, b = mon_rect
            mw = max(320, r - l)
            mh = max(320, b - t)
            w = min(w, max(460, mw - 40))
            h = min(h, max(380, mh - 80))
            x = max(l + 16, r - w - 28)
            y = t + 42
        else:
            sw = max(640, win.winfo_screenwidth())
            sh = max(480, win.winfo_screenheight())
            w = min(w, sw - 40)
            h = min(h, sh - 80)
            x = max(20, sw - w - 28)
            y = 42
        return f"{w}x{h}+{x}+{y}"

    def _open_hud(self) -> None:
        if not self.root:
            self._log_main("ERR: Cannot open Interview HUD (UI root unavailable).")
            return
        try:
            self._build_hud_window()
        except Exception as exc:
            self._log_main(f"ERR: Interview HUD failed to launch: {exc}")
            traceback.print_exc()

    def _build_hud_window(self) -> None:
        if not self.root:
            return
        if self.win and self.win.winfo_exists():
            self._force_foreground_win32(self.win)
            self.root.after(140, lambda: self.win and self.win.winfo_exists() and self._force_foreground_win32(self.win))
            return

        win = tk.Toplevel(self.root)
        self.win = win
        # Detach transient ownership so Mini Orb withdraw() or main minimize never hides the HUD
        try:
            win.transient("")
        except Exception:
            pass
        win.withdraw()
        win.title("Notes")
        win.geometry(self._pick_hud_geometry(win, 640, 500))
        win.minsize(480, 380)
        win.configure(bg="#05070b")
        win.attributes("-topmost", True)
        try:
            win.attributes("-toolwindow", True)  # Hide from Windows Taskbar & Alt+Tab switcher
        except Exception:
            pass
        try:
            win.attributes("-alpha", 0.95)
        except Exception:
            pass
        win.protocol("WM_DELETE_WINDOW", self.stop)

        # Header
        hdr = tk.Frame(win, bg="#0c121b", height=38, highlightthickness=1, highlightbackground="#123045")
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)

        tk.Label(
            hdr,
            text="  🎧 INTERVIEW CO-PILOT",
            bg="#0c121b",
            fg="#00d4ff",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left", padx=4)

        self.shield_lbl = tk.Label(
            hdr,
            text="🛡 SCREEN-SHARE HIDDEN",
            bg="#0c261c",
            fg="#00e676",
            padx=7,
            pady=2,
            font=("Segoe UI", 8, "bold"),
        )
        self.shield_lbl.pack(side="left", padx=6)

        exit_btn = tk.Button(
            hdr,
            text="⏹ Exit",
            command=self.stop,
            bg="#991b2e",
            fg="#ffffff",
            activebackground="#cc243d",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=9,
            pady=3,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )
        exit_btn.pack(side="right", padx=(4, 8), pady=5)

        move_mon_btn = tk.Button(
            hdr,
            text="⇄ Monitor",
            command=self._cycle_monitor,
            bg="#152638",
            fg="#9dd6ff",
            activebackground="#1d3650",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=8,
            pady=3,
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
        )
        move_mon_btn.pack(side="right", padx=2, pady=5)

        # Status & Live Loopback Audio Meter
        sub = tk.Frame(win, bg="#080c12", height=28)
        sub.pack(fill="x", side="top", padx=10, pady=(6, 2))
        self.status_lbl = tk.Label(
            sub,
            text=f"LISTENING LOOPBACK · {self.device_label[:36]}",
            bg="#080c12",
            fg="#00e676",
            font=("Consolas", 9, "bold"),
            anchor="w",
        )
        self.status_lbl.pack(side="left", fill="x", expand=True)

        self.vu_canvas = tk.Canvas(sub, width=110, height=12, bg="#0d141e", highlightthickness=1, highlightbackground="#1b3147")
        self.vu_canvas.pack(side="right", padx=(6, 0), pady=4)

        # Target Role Bar
        role_bar = tk.Frame(win, bg="#05070b")
        role_bar.pack(fill="x", padx=10, pady=(2, 4))
        tk.Label(role_bar, text="Role:", bg="#05070b", fg="#7aa2be", font=("Segoe UI", 9)).pack(side="left")
        self.role_entry = tk.Entry(
            role_bar,
            bg="#0c121b",
            fg="#e4f6ff",
            insertbackground="#00d4ff",
            relief="flat",
            font=("Segoe UI", 9),
        )
        self.role_entry.insert(0, f"{self.role}{' @ ' + self.company if self.company else ''}".strip())
        self.role_entry.pack(side="left", fill="x", expand=True, padx=6, ipady=2)

        def _apply_role(_evt=None):
            raw_r = (self.role_entry.get() if self.role_entry else "").strip()
            if "@" in raw_r:
                r, c = raw_r.split("@", 1)
                self.role, self.company = r.strip(), c.strip()
            else:
                self.role = raw_r
            self._append_hud(f"\n[Role updated: {self.role or 'General'}]\n", "meta")

        self.role_entry.bind("<Return>", _apply_role)

        self.mic_btn = tk.Button(
            role_bar,
            text=f"🎙 Also Mic: {'ON' if self.also_mic else 'OFF'}",
            command=self.toggle_mic,
            bg="#153d2a" if self.also_mic else "#101a26",
            fg="#00e676" if self.also_mic else "#8eb3cc",
            relief="flat",
            bd=0,
            padx=8,
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
        )
        self.mic_btn.pack(side="right", padx=(4, 0))

        # Main Teleprompter Output Area
        text_wrap = tk.Frame(win, bg="#030508", highlightthickness=1, highlightbackground="#153248")
        text_wrap.pack(fill="both", expand=True, padx=10, pady=4)

        scroll = tk.Scrollbar(text_wrap, bg="#0c121b", troughcolor="#05070b")
        scroll.pack(side="right", fill="y")

        self.text_box = tk.Text(
            text_wrap,
            wrap="word",
            bg="#030508",
            fg="#edf9ff",
            insertbackground="#00d4ff",
            font=("Segoe UI", 11),
            relief="flat",
            padx=12,
            pady=10,
            yscrollcommand=scroll.set,
            state="disabled",
        )
        self.text_box.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.text_box.yview)

        self.text_box.tag_config("meta", foreground="#00d4ff", font=("Consolas", 9, "bold"))
        self.text_box.tag_config("answer", foreground="#f2fbff", font=("Segoe UI", 11))
        self.text_box.tag_config("err", foreground="#ff5566", font=("Consolas", 9))

        # Intro message inside HUD
        self.text_box.config(state="normal")
        self.text_box.insert(
            "end",
            "R.O.S.E SILENT INTERVIEW TELEPROMPTER READY\n"
            "• Audio capture: Windows Laptop Speakers (WASAPI Loopback) -> ZERO voice output.\n"
            "• When the interviewer speaks in Meet / Zoom / Chrome / Teams, the answer streams here automatically.\n"
            "• Say 'Exit interview mode' (chat) or click the red Exit button above when finished.\n"
            "------------------------------------------------------------\n",
            "meta",
        )
        self.text_box.config(state="disabled")

        # Bottom Controls & Manual Ask Bar
        btm = tk.Frame(win, bg="#05070b")
        btm.pack(fill="x", side="bottom", padx=10, pady=(4, 10))

        tk.Label(btm, text="Ask:", bg="#05070b", fg="#00d4ff", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 6))

        self.ask_entry = tk.Entry(
            btm,
            bg="#0c121b",
            fg="#ffffff",
            insertbackground="#00d4ff",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#153248",
            highlightcolor="#00d4ff",
            font=("Segoe UI", 10),
        )
        self.ask_entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))
        try:
            win.after(160, lambda: self.ask_entry and self.ask_entry.winfo_exists() and self.ask_entry.focus_set())
        except Exception:
            pass

        def _send_manual(_evt=None):
            if not self.ask_entry:
                return
            txt = self.ask_entry.get().strip()
            if txt:
                self.ask_entry.delete(0, "end")
                self.ask_text(txt)

        self.ask_entry.bind("<Return>", _send_manual)

        for lbl, cmd, bg in (
            ("Ask", _send_manual, "#006b8f"),
            ("📷 Screen", lambda: self.ask_screen(), "#0f5132"),
            ("📋 Clipboard Q", lambda: self.ask_text("clipboard"), "#18334a"),
            ("Copy Ans", self._copy_last_answer, "#18334a"),
            ("Clear", self._clear_hud_text, "#1b2430"),
        ):
            tk.Button(
                btm,
                text=lbl,
                command=cmd,
                bg=bg,
                fg="#e6f7ff",
                relief="flat",
                bd=0,
                padx=8,
                pady=4,
                font=("Segoe UI", 8, "bold"),
                cursor="hand2",
            ).pack(side="left", padx=2)

        win.bind("<F7>", lambda _e: self.ask_screen())
        self._schedule_vu_tick()
        self._force_foreground_win32(win)
        if self.root:
            self.root.after(140, lambda: self.win and self.win.winfo_exists() and self._force_foreground_win32(self.win))

    def _copy_last_answer(self) -> None:
        if not self.last_answer_text:
            return
        try:
            from actions.clipboard_copilot import write_clipboard

            write_clipboard(self.last_answer_text)
            self._set_status("LAST ANSWER COPIED TO CLIPBOARD", "#00d4ff")
        except Exception:
            pass

    def _schedule_vu_tick(self) -> None:
        if not self.active or not self.win or not self.win.winfo_exists():
            return
        try:
            if self.vu_canvas and self.vu_canvas.winfo_exists():
                self.vu_canvas.delete("all")
                w = int(self.vu_level_px())
                color = "#00e676" if w > 12 else "#1d4b63"
                self.vu_canvas.create_rectangle(0, 0, max(2, w), 12, fill=color, outline="")
        except Exception:
            pass
        if self.root:
            self._hud_update_job = self.root.after(90, self._schedule_vu_tick)

    def vu_level_px(self) -> float:
        return max(0.0, min(110.0, self._vu_level * 110.0))

    # ── Lifecycle Start / Stop / Toggle ──────────────────────────────────────

    def start(self, role: str = "", company: str = "", also_mic: bool = False) -> str:
        with self._lock:
            if role:
                self.role = role.strip()
            if company:
                self.company = company.strip()
            self.also_mic = bool(also_mic)

            if not self.candidate_context:
                self.candidate_context = _load_candidate_context()

            if self.active:
                self._ui_call(self._open_hud)
                return f"Interview Mode is already active (silent, listening to {self.device_label})."

            self.active = True
            self._stop_event.clear()

        if self.on_mode_change:
            try:
                self.on_mode_change(True)
            except Exception:
                pass

        self._ui_call(self._open_hud)
        self._warm_genai_client_bg()

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="ROSE-InterviewWorker")
        self._capture_thread = threading.Thread(target=self._loopback_loop, daemon=True, name="ROSE-LoopbackInterview")
        self._mic_thread = threading.Thread(target=self._optional_mic_loop, daemon=True, name="ROSE-InterviewMicOpt")
        self._worker_thread.start()
        self._capture_thread.start()
        self._mic_thread.start()

        has_cv = "CV loaded" if self.candidate_context else "General profile"
        return (
            f"Interview Mode is now ON (100% silent — all speech & sound effects muted). "
            f"Listening to laptop speaker audio via WASAPI Loopback ({has_cv}) and writing live answers to the Interview Teleprompter HUD."
        )

    def stop(self) -> str:
        with self._lock:
            was_active = self.active
            self.active = False
            self._stop_event.set()
            while not self._task_queue.empty():
                try:
                    self._task_queue.get_nowait()
                except Exception:
                    break

        if self.on_mode_change:
            try:
                self.on_mode_change(False)
            except Exception:
                pass

        def _destroy():
            if self.win and self.win.winfo_exists():
                try:
                    self.win.destroy()
                except Exception:
                    pass
            self.win = None

        self._ui_call(_destroy)
        if not was_active:
            return "Interview Mode is already off; normal voice mode is active."
        return "Interview Mode turned OFF. Switched back to normal voice mode."

    def toggle_mic(self, state: bool | None = None) -> str:
        self.also_mic = (not self.also_mic) if state is None else bool(state)

        def _upd():
            if self.mic_btn and self.mic_btn.winfo_exists():
                self.mic_btn.config(
                    text=f"🎙 Also Mic: {'ON' if self.also_mic else 'OFF'}",
                    bg="#153d2a" if self.also_mic else "#101a26",
                    fg="#00e676" if self.also_mic else "#8eb3cc",
                )

        self._ui_call(_upd)
        return f"Interview Mode secondary microphone capture is now {'ON' if self.also_mic else 'OFF'}."

    def status(self) -> str:
        cv_flag = "loaded" if (self.candidate_context or _load_candidate_context()) else "not found"
        return (
            f"Interview Mode: {'ACTIVE (100% SILENT)' if self.active else 'OFF'} | "
            f"Loopback Driver: {'WASAPI Ready' if HAS_LOOPBACK else 'Unavailable'} | "
            f"Device: {self.device_label} | Also Mic: {'ON' if self.also_mic else 'OFF'} | CV/Profile: {cv_flag}"
        )

    def toggle(self, role: str = "", company: str = "", also_mic: bool = False) -> str:
        if self.active:
            return self.stop()
        return self.start(role=role, company=company, also_mic=also_mic)


_COPILOT: InterviewCopilot | None = None

WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def set_window_capture_excluded(tk_window, excluded: bool = True) -> bool:
    """
    Excludes a Tkinter Tk/Toplevel window from ALL Windows screen-sharing and screen-capture
    APIs (Google Meet, Zoom, Teams, Chrome entire-screen share, OBS, BitBlt, PrintScreen),
    while keeping the window 100% readable to the user locally on screen.
    """
    if not tk_window:
        return False
    try:
        import ctypes.wintypes as wt

        hwnd = InterviewCopilot._resolve_toplevel_hwnd(tk_window)
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        user32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
        user32.SetWindowDisplayAffinity.restype = wt.BOOL
        user32.GetWindowDisplayAffinity.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        user32.GetWindowDisplayAffinity.restype = wt.BOOL

        flag = WDA_EXCLUDEFROMCAPTURE if excluded else WDA_NONE
        ok = bool(user32.SetWindowDisplayAffinity(wt.HWND(hwnd), wt.DWORD(flag)))
        aff = wt.DWORD(0)
        got = bool(user32.GetWindowDisplayAffinity(wt.HWND(hwnd), ctypes.byref(aff)))
        if excluded:
            return bool(ok and got and int(aff.value) == WDA_EXCLUDEFROMCAPTURE)
        return bool(ok)
    except Exception:
        return False


def init_interview_copilot(
    root: tk.Tk | None = None,
    write_log_cb: Callable[[str], None] | None = None,
    on_mode_change: Callable[[bool], None] | None = None,
) -> InterviewCopilot:
    global _COPILOT
    if _COPILOT is None:
        _COPILOT = InterviewCopilot(root=root, write_log_cb=write_log_cb, on_mode_change=on_mode_change)
    else:
        if root is not None:
            _COPILOT.root = root
        if write_log_cb is not None:
            _COPILOT.write_log_cb = write_log_cb
        if on_mode_change is not None:
            _COPILOT.on_mode_change = on_mode_change
    return _COPILOT


def get_interview_copilot() -> InterviewCopilot:
    global _COPILOT
    if _COPILOT is None:
        _COPILOT = InterviewCopilot()
    return _COPILOT


def interview_mode_control(
    action: str = "start",
    role: str = "",
    company: str = "",
    question: str = "",
    also_mic: bool = False,
) -> str:
    """Tool entrypoint for switching R.O.S.E into/out of Silent Interview Co-Pilot Mode."""
    ctrl = get_interview_copilot()
    act = (action or "start").strip().lower()

    if act in ("start", "on", "enable", "enter", "switch_on", "interview_mode"):
        return ctrl.start(role=role, company=company, also_mic=bool(also_mic))
    if act in ("toggle", "flip"):
        return ctrl.toggle(role=role, company=company, also_mic=bool(also_mic))
    if act in ("stop", "off", "exit", "disable", "normal", "normal_mode"):
        return ctrl.stop()
    if act in ("ask", "answer", "question"):
        return ctrl.ask_text(question)
    if act in ("screen", "read_screen", "solve_screen"):
        return ctrl.ask_screen(note=question)
    if act in ("mic_on", "enable_mic"):
        return ctrl.toggle_mic(True)
    if act in ("mic_off", "disable_mic"):
        return ctrl.toggle_mic(False)
    if act in ("status", "check"):
        return ctrl.status()
    return f"Unknown interview_mode_control action: {action}"

