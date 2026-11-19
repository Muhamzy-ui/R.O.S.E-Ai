# R.O.S.E (Project Mirror) — System & Architecture Audit (Phase 0)

Date: November 13, 2026  
Target: Windows Desktop AI Assistant (Mahmud Bashir Olasunkanmi)  
Python: 3.12 (venv)  
Audit Status: Complete  

---

## 1. Module Inventory & Structure

The repository is organized into five functional layers:

| Directory / Module | Role & Scope | Key Files |
| :--- | :--- | :--- |
| **Root Entry Points** | Application initialization, CLI flags, lifecycle management | `main.py`, `ui.py`, `app_config.py`, `start.bat`, `setup.bat` |
| **`core/`** | Foundation primitives, result modeling, permissions, logging, audit trail | `core/result.py`, `core/logging_setup.py`, `core/permissions.py`, `core/audit.py`, `core/lmstudio_runtime.py` |
| **`tools/`** | Central tool registry, argument schema validation, timeout enforcement | `tools/registry.py` |
| **`actions/`** (32 modules) | OS automation, media, browser, vision, system intelligence, workflows | `actions/open_app.py`, `actions/whatsapp.py`, `actions/shell.py`, `actions/laptop_control.py`, `actions/job_command_center.py`, `actions/camera_vision.py`, `actions/reminders.py`, etc. |
| **`memory/`** | Persistent JSON stores for user profile, calendar, reminders, and notes | `memory/memory_manager.py`, `memory/job_profile.json`, `memory/memory.json`, `memory/calendar.json`, `memory/reminders.json` |
| **`legacy/`** | Preserved backward-compatible Tkinter HUD | `legacy/ui_legacy.py` |
| **`logs/`** | Rotating operational logs and JSONL audit trail | `logs/rose.log` (5MB x 3), `logs/audit.jsonl` |
| **Assets & Visuals** | Fonts, icons, and low-latency UI audio sound effects | `Fonts/`, `Icon/`, `SFX/` |

---

## 2. Entry Point & Lifecycle Architecture

### Application Startup Flow:
1. **Script Launcher (`start.bat` / `Start ROSE.bat`)**:
   - Enforces execution from project root using `venv\Scripts\python.exe`.
   - Runs `main.py --kill-old` to terminate any orphaned ROSE instances before startup.
2. **Process Management (`main._kill_other_instances`)**:
   - Scans system processes via `psutil` matching command-line arguments to prevent port conflicts or double microphone binding.
3. **UI Initialization (`main.main()`)**:
   - Instantiates `JarvisUI` (`ui.py`), constructing the Tkinter root window and HUD canvas.
   - Initializes child HUD controllers (`init_camera_controller`, `init_research_board`, `init_busy_bar`, `init_interview_copilot`, `init_job_command_center`).
4. **Asynchronous Runner Thread (`main.runner()`)**:
   - Spawns a background daemon thread running `asyncio.run(jarvis.run())`.
   - Checks backend configuration (`gemini` live or `lmstudio` local fallback).
   - Pre-warms WASAPI audio device streams.
   - Opens WebSocket session with Gemini Multimodal Live API (`v1alpha`).
   - Launches concurrent async tasks:
     - `_send_realtime()`: Captures and sends microphone audio & vision frames.
     - `_receive_audio()`: Receives audio chunks, decodes PCM16, routes to jitter buffer.
     - `_watch_reminders()`: Ticks scheduled alarms and morning wake-up briefings.
5. **Main Event Loop**:
   - UI thread executes `ui.root.mainloop()` on the main thread.

---

## 3. Tool Registration & Gemini Live API Dispatch

### How Tools Are Declared:
- Declared in `main.TOOL_DECLARATIONS` (38 tool objects), formatted to Gemini Live API specifications:
  ```python
  {
      "name": "open_app",
      "description": "Opens or brings into focus ANY installed application on Windows...",
      "parameters": {
          "type": "object",
          "properties": {"app_name": {"type": "string"}, "force_new": {"type": "boolean"}},
          "required": ["app_name"]
      }
  }
  ```
- Bundled into `types.LiveConnectConfig`:
  ```python
  tools=[{"function_declarations": TOOL_DECLARATIONS}]
  ```

### Current Tool Execution Dispatch:
- In `main.JarvisLive._execute_tool(fc)`:
  - Extracts `name = fc.name` and `args = dict(fc.args or {})`.
  - Runs acoustic voiceprint verification for `PROTECTED_PERSONAL_TOOLS` if speaker was voice-initiated.
  - Matches `name` in a 38-branch `if/elif` chain.
  - Dispatches calls via `loop.run_in_executor(None, lambda: action_fn(...))`.
  - Returns `types.FunctionResponse(id=fc.id, name=name, response={"result": result_str})`.

---

## 4. Truthful Results Audit: Unverified Success Reporting (Rule A2)

In the current legacy code, multiple tools report unconditional success without inspecting actual OS or remote state:

| Tool | File | Current Behavior | Unverified Flaw | Required True Verification |
| :--- | :--- | :--- | :--- | :--- |
| **`send_whatsapp_message`** | `actions/whatsapp.py` | Uses `pywhatkit` or opens WhatsApp Desktop, calls `time.sleep()`, presses Enter via `pyautogui`. Returns `"Message sent"`. | No verification whether WhatsApp window opened, phone was connected, or message was accepted. | Baileys WebSocket acknowledgment (Phase 2). Until then, return `verified=False`. |
| **`play_media`** | `actions/media.py` | Calls `os.startfile(url)` or presses `playpause`. Returns `"Playing {query}"`. | Returns success even if browser fails to load, media is paused, or track does not exist. | Check window handle and audio stream playback status. |
| **`open_app`** | `actions/open_app.py` | Calls `subprocess.Popen` or `os.startfile`. Returns `"Opened {app_name}"`. | Returns success even if process crashes immediately or UAC is rejected. | Check `psutil.process_iter()` and verify top-level HWND exists. |
| **`laptop_control`** | `actions/laptop_control.py` | Sends volume keybd_event `0xAF/0xAE` or calls `SetSuspendState`. Returns `"Volume adjusted"`. | Key events can be swallowed by fullscreen apps; sleep cannot confirm execution before suspension. | Read endpoint master volume from CoreAudio/PyCAKE; log suspend state. |
| **`browser_control`** | `actions/browser.py` | Calls `webbrowser.open(url)`. Returns `"Navigated to {url}"`. | Does not check HTTP status or browser launch outcome. | Check browser process existence and URL validity. |
| **`install_or_download`** | `actions/downloader.py` | Opens download URL in default browser. Returns `"Download started"`. | Browser open != download initiated or finished. | Mark `verified=False` until file is found in `~/Downloads`. |
| **`shell_run`** | `actions/shell.py` | Runs command, returns stdout+stderr. If empty, returns `"Command executed successfully"`. | Does not check `result.returncode == 0`. Silently ignores non-zero exit codes. | Check `returncode == 0` and inspect exit status. |
| **`busy_bar_control`** | `actions/busy_bar.py` | Writes to simulated dot matrix frame. Returns `"Updated"`. | Window might be hidden or minimized. | Verify frame buffer state and visibility flag. |

---

## 5. UI Thread Blocking Analysis (Rule A4)

The UI thread (`ui.root.mainloop()`) must remain under 16ms frame time (60 FPS) with zero blocking operations:

| File / Component | Operation | Blocking Risk | Remediation / Current Mitigation |
| :--- | :--- | :--- | :--- |
| **`ui.py` (`_animate`)** | Periodic canvas rendering every 45ms | Moderate: Repaints HUD arcs, circles, and telemetry every frame. | Offloaded stats gathering (`psutil`) to background daemon thread at line 2354. |
| **`ui.py` (`_update_stats`)** | `psutil.cpu_percent()`, `psutil.virtual_memory()` | Minor: `cpu_percent(interval=None)` is non-blocking, but disk usage can stall if an external drive sleeps. | Kept inside background worker thread. |
| **`actions/job_command_center.py`** | `open_dashboard()`, `data/job_tracker.json` read | Low-Moderate: Large JSON parsing on UI thread during window spawn. | Async worker thread for all HTTP scraping and filtering; cache cached leads in memory. |
| **`actions/interview_copilot.py`** | Overlay creation and audio capture setup | Low: Audio device open must never run on main UI loop. | Initialized in dedicated background thread. |
| **Tkinter `root.after` timers** | Global hotkey polling via `GetAsyncKeyState` | Minor: Polled every 80-100ms. | Negligible CPU footprint (<0.1%). |

---

## 6. Dependency Analysis

### Directly Imported Packages:
- **Audio & Streaming:** `pyaudio`, `pyaudiowpatch`, `google-genai`, `websockets`, `audioop`
- **Vision & Screen:** `cv2` (opencv-python), `PIL` (pillow), `pyautogui`
- **System & Automation:** `psutil`, `pywin32` (`win32com`, `winreg`), `comtypes`, `ctypes`, `wmi`
- **Network & Scraping:** `requests`, `duckduckgo_search`, `bs4` (beautifulsoup4), `urllib`
- **Documents & Data:** `pypdf`, `docx` (python-docx), `openpyxl`, `json`
- **Local Fallback:** `lmstudio_runtime` (HTTP REST client)

### Heavy / Redundant Dependencies to Monitor:
- `duckduckgo_search` / `primp`: Lightweight scraping; no Chromium or Puppeteer required.
- `yt-dlp`: Used for audio stream extraction; runs in worker thread.
- **Rule A4 Guard:** Total idle memory budget < 500 MB. Zero Chromium/Puppeteer processes permitted.

---

## 7. Foundation Architecture Deployed in Phase 0

1. **`core/result.py`**:
   - `Result(ok: bool, data: dict, error: str | None, verified: bool)`
   - Factory methods: `Result.success()`, `Result.unverified()`, `Result.failure()`.
   - `to_user_message()` converts outcomes to truthful speech/text for Gemini.
2. **`core/logging_setup.py`**:
   - Rotating file logger (`logs/rose.log`, 5MB x 3 backups).
   - Standardized format: `[timestamp] [level] [logger] message`.
3. **`core/audit.py`**:
   - Append-only `logs/audit.jsonl` recording every tool call.
   - Automatic secret redaction for API keys, passwords, tokens, and authorization headers.
4. **`core/permissions.py`**:
   - Strictly enforces Tiers 0, 1, 2, 3.
   - Shell denylist pattern matching (blocking destructive formatting, registry tampering, remote execution).
   - Global emergency Kill Switch (`Ctrl+Alt+Shift+K` / `/kill`).
5. **`tools/registry.py`**:
   - Central registry for all 38 tools with JSON schema argument validation, tier authorization, timeout guards, and execution logging.
6. **`legacy/ui_legacy.py`**:
   - Preserved working backup of legacy Tkinter UI until Phase 8 PySide6 migration is complete.

