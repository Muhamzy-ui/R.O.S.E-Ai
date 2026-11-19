"""
Full Windows Laptop Control for R.O.S.E
Supports volume, brightness, keyboard typing, hotkeys, mouse scrolling,
screenshots, single-app window management (minimize/maximize/restore/close),
and system power actions.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import datetime
import os
import subprocess
import threading
from pathlib import Path

import pyautogui
import psutil

pyautogui.FAILSAFE = False

_USER32 = ctypes.windll.user32
try:
    _DWMAPI = ctypes.windll.dwmapi
except Exception:
    _DWMAPI = None

SW_SHOW = 5
SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_RESTORE = 9
WM_CLOSE = 0x0010
DWMWA_CLOAKED = 14

_SHELL_SKIP_CLASSES = {
    "progman",
    "workerw",
    "shell_traywnd",
    "shell_secondarytraywnd",
    "notifyiconoverflowwindow",
    "toplevellwindowforoverflowxamlisland",
    "toplevelwindowforoverflowxamlisland",
    "windows.ui.core.corewindow",
}

_APP_WINDOW_ALIASES: dict[str, tuple[str, ...]] = {
    "notepad": ("notepad", "notepad.exe"),
    "note pad": ("notepad", "notepad.exe"),
    "chrome": ("chrome", "google chrome", "chrome.exe"),
    "google chrome": ("chrome", "google chrome", "chrome.exe"),
    "edge": ("msedge", "microsoft edge", "msedge.exe"),
    "microsoft edge": ("msedge", "microsoft edge", "msedge.exe"),
    "vscode": ("code.exe", "visual studio code"),
    "vs code": ("code.exe", "visual studio code"),
    "visual studio code": ("code.exe", "visual studio code"),
    "explorer": ("file explorer", "cabinetwclass", "explorer.exe"),
    "file explorer": ("file explorer", "cabinetwclass", "explorer.exe"),
    "terminal": ("windowsterminal", "terminal", "cmd.exe", "command prompt"),
    "cmd": ("cmd.exe", "command prompt"),
    "command prompt": ("cmd.exe", "command prompt"),
    "powershell": ("powershell", "pwsh", "windows powershell"),
    "spotify": ("spotify", "spotify.exe"),
    "whatsapp": ("whatsapp", "whatsapp.exe", "whatsapp.root", "whatsapp.root.exe"),
    "antigravity": ("antigravity", "antigravity ide", "antigravity ide.exe", "antigravity.exe"),
    "antigravity ide": ("antigravity", "antigravity ide", "antigravity ide.exe", "antigravity.exe"),
    "anti gravity": ("antigravity", "antigravity ide", "antigravity ide.exe", "antigravity.exe"),
    "antygravity": ("antigravity", "antigravity ide", "antigravity ide.exe", "antigravity.exe"),
    "anty gravity": ("antigravity", "antigravity ide", "antigravity ide.exe", "antigravity.exe"),
    "word": ("winword", "microsoft word", "word"),
    "microsoft word": ("winword", "microsoft word"),
    "excel": ("excel", "microsoft excel"),
    "microsoft excel": ("excel", "microsoft excel"),
    "powerpoint": ("powerpnt", "powerpoint"),
    "vlc": ("vlc", "vlc media player"),
    "calculator": ("calculator", "calc"),
    "calc": ("calculator", "calc"),
    "settings": ("settings", "systemsettings"),
    "paint": ("mspaint", "paint"),
    "store": ("microsoft store", "winstore"),
    "microsoft store": ("microsoft store", "winstore"),
    "youtube music": ("youtube music", "ymusic"),
    "ymusic": ("ymusic", "youtube music"),
    "discord": ("discord", "discord.exe"),
    "telegram": ("telegram", "telegram.exe"),
    "rose": ("j.a.r.v.i.s", "r.o.s.e"),
    "jarvis": ("j.a.r.v.i.s", "r.o.s.e"),
}


def _is_window_cloaked(hwnd: int) -> bool:
    if not _DWMAPI:
        return False
    try:
        cloaked = ctypes.c_int(0)
        hr = _DWMAPI.DwmGetWindowAttribute(
            hwnd,
            DWMWA_CLOAKED,
            ctypes.byref(cloaked),
            ctypes.sizeof(cloaked),
        )
        return hr == 0 and cloaked.value != 0
    except Exception:
        return False


def _enumerate_app_windows() -> list[dict]:
    windows: list[dict] = []
    enum_proc_t = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )

    def _callback(hwnd, _lparam):
        try:
            if not _USER32.IsWindowVisible(hwnd):
                return True
            if _is_window_cloaked(hwnd):
                return True

            tlen = _USER32.GetWindowTextLengthW(hwnd)
            if tlen <= 0:
                return True
            tbuf = ctypes.create_unicode_buffer(tlen + 1)
            _USER32.GetWindowTextW(hwnd, tbuf, tlen + 1)
            title = (tbuf.value or "").strip()
            if not title:
                return True

            cbuf = ctypes.create_unicode_buffer(128)
            _USER32.GetClassNameW(hwnd, cbuf, 128)
            cls_name = (cbuf.value or "").strip()
            if cls_name.lower() in _SHELL_SKIP_CLASSES:
                return True

            rect = ctypes.wintypes.RECT()
            _USER32.GetWindowRect(hwnd, ctypes.byref(rect))
            # Skip invisible 0x0 helper windows when not minimized
            if not _USER32.IsIconic(hwnd) and (rect.right - rect.left < 40 or rect.bottom - rect.top < 30):
                return True

            pid = ctypes.wintypes.DWORD(0)
            _USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe_name = ""
            if pid.value:
                try:
                    exe_name = psutil.Process(pid.value).name()
                except Exception:
                    exe_name = ""

            windows.append({
                "hwnd": hwnd,
                "title": title,
                "class_name": cls_name,
                "exe": exe_name,
                "pid": pid.value,
                "minimized": bool(_USER32.IsIconic(hwnd)),
            })
        except Exception:
            pass
        return True

    _USER32.EnumWindows(enum_proc_t(_callback), 0)
    return windows


def _find_matching_windows(app_query: str) -> list[dict]:
    q = " ".join((app_query or "").strip().lower().split())
    if not q:
        return []
    # Strip trailing generic word like "window" or "app"
    for suffix in (" window", " app", " application"):
        if q.endswith(suffix):
            q = q[: -len(suffix)].strip()

    tokens = list(_APP_WINDOW_ALIASES.get(q, (q,)))
    if q not in tokens:
        tokens.insert(0, q)

    matches: list[tuple[int, dict]] = []
    for win in _enumerate_app_windows():
        title_l = win["title"].lower()
        exe_l = win["exe"].lower()
        exe_stem = exe_l[:-4] if exe_l.endswith(".exe") else exe_l
        cls_l = win["class_name"].lower()

        # Never treat desktop/explorer shell internals as File Explorer unless CabinetWClass or Explorer title
        if exe_stem == "explorer" and cls_l != "cabinetwclass" and "explorer" not in title_l:
            continue

        score = 0
        for tok in tokens:
            t_stem = tok[:-4] if tok.endswith(".exe") else tok
            if exe_stem and (exe_stem == t_stem or exe_l == tok):
                score = max(score, 100)
            elif exe_stem and t_stem in exe_stem:
                score = max(score, 85)
            elif title_l == tok or title_l.endswith(f"- {tok}") or title_l.endswith(f"— {tok}"):
                score = max(score, 90)
            elif tok in title_l:
                score = max(score, 75)
            elif cls_l and tok == cls_l:
                score = max(score, 70)

        if score > 0:
            matches.append((score, win))

    matches.sort(key=lambda item: item[0], reverse=True)
    return [m[1] for m in matches]


def _control_single_app_window(act: str, app_query: str) -> str:
    if act in ("list_open_windows", "list_windows", "list_apps"):
        wins = _enumerate_app_windows()
        if not wins:
            return "No open application windows detected."
        summary = []
        seen = set()
        for w in wins:
            tag = f"{w['title']}" + (" [minimized]" if w["minimized"] else "")
            if tag not in seen:
                seen.add(tag)
                summary.append(tag)
        return "Open windows: " + "; ".join(summary[:12]) + "."

    target_name = (app_query or "").strip()
    if not target_name:
        if act in ("minimize_app", "minimize", "minimise", "minimise_app", "hide_app"):
            fg = _USER32.GetForegroundWindow()
            if fg:
                _USER32.ShowWindow(fg, SW_MINIMIZE)
                return "Minimized the active window."
            return "Please specify which application window to minimize (for example, 'Notepad')."
        return "Please specify which application window to control (for example, 'Notepad' or 'Chrome')."

    matched = _find_matching_windows(target_name)
    if not matched:
        wins = _enumerate_app_windows()
        open_titles = ", ".join(w["title"][:35] for w in wins[:5]) or "none"
        return f"No open window matching '{target_name}' was found. Currently open: {open_titles}."

    if act in ("minimize_app", "minimize", "minimise", "minimise_app", "hide_app"):
        non_min = [w for w in matched if not w["minimized"]]
        if not non_min:
            return f"{matched[0]['title']} is already minimized."
        count = 0
        for w in non_min:
            _USER32.ShowWindow(w["hwnd"], SW_MINIMIZE)
            count += 1
        label = matched[0]["title"]
        if count == 1:
            return f"Minimized {label}."
        return f"Minimized {count} {target_name} windows (including {label})."

    if act in ("maximize_app", "maximize", "maximise", "maximize_window"):
        w = matched[0]
        _USER32.ShowWindow(w["hwnd"], SW_MAXIMIZE)
        try:
            _USER32.SetForegroundWindow(w["hwnd"])
        except Exception:
            pass
        return f"Maximized {w['title']}."

    if act in ("restore_app", "restore", "focus_app", "focus", "bring_to_front", "switch_to"):
        w = matched[0]
        _USER32.ShowWindow(w["hwnd"], SW_RESTORE if w["minimized"] else SW_SHOW)
        try:
            _USER32.SetForegroundWindow(w["hwnd"])
        except Exception:
            pass
        return f"Restored and focused {w['title']}."

    if act in ("close_app", "close_window"):
        count = 0
        for w in matched:
            _USER32.PostMessageW(w["hwnd"], WM_CLOSE, 0, 0)
            count += 1
        label = matched[0]["title"]
        return f"Closed {label}." if count == 1 else f"Sent close request to {count} {target_name} windows."

    return f"Unknown window action: {act}"


def restore_or_focus_app(app_query: str) -> tuple[bool, str]:
    """
    Finds an already-open window matching app_query and restores/brings it to the front.
    Returns (True, window_title) if found and restored, otherwise (False, '').
    """
    matched = _find_matching_windows(app_query)
    if not matched:
        return False, ""
    w = matched[0]
    hwnd = w["hwnd"]
    try:
        if w["minimized"]:
            _USER32.ShowWindow(hwnd, SW_RESTORE)
        else:
            _USER32.ShowWindow(hwnd, SW_SHOW)
        try:
            _USER32.BringWindowToTop(hwnd)
        except Exception:
            pass
        try:
            _USER32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        return True, w["title"]
    except Exception:
        return False, ""


SPI_GETWORKAREA = 0x0030
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040


def _get_work_area() -> tuple[int, int, int, int]:
    """Returns usable desktop work area (left, top, right, bottom) excluding Windows taskbar."""
    rect = ctypes.wintypes.RECT()
    ok = _USER32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    if ok and rect.right > rect.left and rect.bottom > rect.top:
        return rect.left, rect.top, rect.right, rect.bottom
    w = _USER32.GetSystemMetrics(0) or 1920
    h = (_USER32.GetSystemMetrics(1) or 1080) - 48
    return 0, 0, w, h


def _move_hwnd_to_slot(hwnd: int, position: str) -> str:
    l, t, r, b = _get_work_area()
    w = r - l
    h = b - t
    pos = (position or "left").strip().lower().replace("-", "_").replace(" ", "_")

    slots = {
        "left": (l, t, w // 2, h),
        "right": (l + w // 2, t, w - w // 2, h),
        "top": (l, t, w, h // 2),
        "bottom": (l, t + h // 2, w, h - h // 2),
        "top_left": (l, t, w // 2, h // 2),
        "top_right": (l + w // 2, t, w - w // 2, h // 2),
        "bottom_left": (l, t + h // 2, w // 2, h - h // 2),
        "bottom_right": (l + w // 2, t + h // 2, w - w // 2, h - h // 2),
        "center": (l + int(w * 0.12), t + int(h * 0.08), int(w * 0.76), int(h * 0.84)),
    }
    x, y, sw, sh = slots.get(pos, slots["left"])
    _USER32.ShowWindow(hwnd, SW_RESTORE)
    _USER32.SetWindowPos(hwnd, 0, x, y, sw, sh, SWP_NOZORDER | SWP_SHOWWINDOW)
    try:
        _USER32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    return pos


def snap_window(app_query: str = "", position: str = "left") -> str:
    """Snaps an open app window (or active window if app_query omitted) to left/right/corners/center."""
    q = (app_query or "").strip()
    if q:
        matched = _find_matching_windows(q)
        if not matched:
            return f"Could not find an open window matching '{q}' to snap."
        hwnd = matched[0]["hwnd"]
        label = matched[0]["title"]
    else:
        hwnd = _USER32.GetForegroundWindow()
        if not hwnd:
            return "No active window found to snap."
        label = "Active window"

    applied = _move_hwnd_to_slot(hwnd, position)
    return f"Snapped {label} to the {applied.replace('_', ' ')}."


def split_screen(left_app: str, right_app: str) -> str:
    """Arranges two applications side by side (50% left / 50% right)."""
    left_res = snap_window(left_app, "left") if left_app else "No left app specified."
    right_res = snap_window(right_app, "right") if right_app else "No right app specified."
    return f"{left_res} | {right_res}"


def laptop_control(
    action: str,
    value: str = "",
    text: str = "",
    target: str = "",
    confirmed: bool = False,
) -> str:
    """
    Executes a laptop control action on Windows.
    action:
      - volume_up | volume_down | volume_mute | set_volume (value='0'-'100')
      - set_brightness (value='0'-'100')
      - type_text (text='...')
      - press_key / hotkey (value='ctrl+c', 'alt+tab', 'enter', 'win+d', 'space', etc.)
      - scroll_up | scroll_down
      - media_play_pause | media_next | media_prev
      - screenshot
      - minimize_app | maximize_app | restore_app | close_app | list_open_windows (target/value='<app name>')
      - close_active_window | minimize_all | show_desktop
      - lock_screen | sleep_laptop | hibernate | shutdown_laptop (power actions require confirmed=True)
    """
    act = (action or "").strip().lower()
    val = (value or "").strip()
    txt = (text or "").strip()
    tgt = (target or "").strip() or val or txt
    is_confirmed = bool(confirmed) or val.lower() in ("confirmed", "confirm", "yes", "true")

    try:
        # Handle single-app window actions cleanly
        if act in (
            "minimize_app", "minimize", "minimise", "minimise_app", "hide_app",
            "maximize_app", "maximize", "maximise", "maximize_window",
            "restore_app", "restore", "focus_app", "focus", "bring_to_front", "switch_to",
            "close_app", "close_window",
            "list_open_windows", "list_windows", "list_apps",
        ):
            return _control_single_app_window(act, tgt)

        if act == "minimize_all" and tgt:
            # Guard: if caller passed a specific app name to minimize_all, minimize only that app
            return _control_single_app_window("minimize_app", tgt)

        if act in ("snap_window", "snap", "tile_window", "move_window"):
            pos = val or "left"
            app_name = (target or "").strip()
            if not app_name and val.lower() not in {"left", "right", "top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right", "center"}:
                app_name = val
                pos = "left"
            return snap_window(app_name, pos)

        if act in ("snap_left", "snap_right", "snap_center"):
            return snap_window((target or val or "").strip(), act.replace("snap_", ""))

        if act in ("split_screen", "tile_pair", "side_by_side"):
            left_app = (target or "").strip()
            right_app = (val or txt or "").strip()
            if "|" in left_app and not right_app:
                left_app, right_app = [x.strip() for x in left_app.split("|", 1)]
            elif "," in left_app and not right_app:
                left_app, right_app = [x.strip() for x in left_app.split(",", 1)]
            return split_screen(left_app or "Antigravity", right_app or "Chrome")

        if act == "volume_up":
            steps = max(1, int(val) // 2) if val.isdigit() else 5
            for _ in range(steps):
                pyautogui.press("volumeup")
            return "Turned the laptop volume up."

        if act == "volume_down":
            steps = max(1, int(val) // 2) if val.isdigit() else 5
            for _ in range(steps):
                pyautogui.press("volumedown")
            return "Turned the laptop volume down."

        if act in ("volume_mute", "mute"):
            pyautogui.press("volumemute")
            return "Toggled laptop speaker mute."

        if act == "set_volume":
            target_lvl = max(0, min(100, int(float(val or "50"))))
            for _ in range(50):
                pyautogui.press("volumedown")
            for _ in range(target_lvl // 2):
                pyautogui.press("volumeup")
            return f"Set laptop volume to approximately {target_lvl}%."

        if act in ("set_brightness", "brightness"):
            target_lvl = max(0, min(100, int(float(val or "70"))))
            cmd = (
                f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
                f".WmiSetBrightness(1, {target_lvl})"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True,
                timeout=6,
            )
            return f"Set laptop screen brightness to {target_lvl}%."

        if act == "type_text":
            to_type = txt or val
            if not to_type:
                return "No text provided to type."
            pyautogui.write(to_type, interval=0.01)
            return f"Typed: {to_type[:60]}"

        if act in ("press_key", "hotkey"):
            keys = [k.strip().lower() for k in (val or txt).replace("-", "+").split("+") if k.strip()]
            if not keys:
                return "No key specified."
            if len(keys) == 1:
                pyautogui.press(keys[0])
            else:
                pyautogui.hotkey(*keys)
            return f"Pressed {'+'.join(keys)}."

        if act == "scroll_up":
            amount = int(val) if val.lstrip("-").isdigit() else 600
            pyautogui.scroll(abs(amount))
            return "Scrolled up."

        if act == "scroll_down":
            amount = int(val) if val.lstrip("-").isdigit() else 600
            pyautogui.scroll(-abs(amount))
            return "Scrolled down."

        if act == "media_play_pause":
            pyautogui.press("playpause")
            return "Toggled media play/pause."

        if act == "media_next":
            pyautogui.press("nexttrack")
            return "Skipped to next track."

        if act == "media_prev":
            pyautogui.press("prevtrack")
            return "Went to previous track."

        if act == "screenshot":
            desktop = Path(os.path.expanduser("~/Desktop"))
            desktop.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = desktop / f"ROSE_Screenshot_{stamp}.png"
            img = pyautogui.screenshot()
            img.save(out_path)
            return f"Saved screenshot to Desktop as {out_path.name}."

        if act == "close_active_window":
            if tgt:
                return _control_single_app_window("close_app", tgt)
            pyautogui.hotkey("alt", "f4")
            return "Closed the active window."

        if act in ("minimize_all", "show_desktop"):
            pyautogui.hotkey("win", "d")
            return "Toggled desktop view."

        # ── Power & Lifecycle Actions (Phase 1) ──────────────────────────────────
        power_aliases = {
            "lock_screen": "lock_laptop",
            "lock_laptop": "lock_laptop",
            "lock": "lock_laptop",
            "sleep_laptop": "sleep_laptop",
            "sleep": "sleep_laptop",
            "sleep_pc": "sleep_laptop",
            "standby": "sleep_laptop",
            "suspend": "sleep_laptop",
            "put my laptop to sleep": "sleep_laptop",
            "shut_yourself_down": "shutdown_self",
            "shutdown_self": "shutdown_self",
            "shutdown_rose": "shutdown_self",
            "close_rose": "shutdown_self",
            "exit_rose": "shutdown_self",
            "shut yourself down": "shutdown_self",
            "sleep_and_shutdown_self": "sleep_and_shutdown_self",
            "sleep_and_exit": "sleep_and_shutdown_self",
            "put the laptop to sleep and shut yourself down": "sleep_and_shutdown_self",
            "restart_laptop": "restart_laptop",
            "restart": "restart_laptop",
            "restart_pc": "restart_laptop",
            "reboot": "restart_laptop",
            "shutdown_laptop": "shutdown_laptop",
            "shutdown": "shutdown_laptop",
            "shutdown_pc": "shutdown_laptop",
            "power_off": "shutdown_laptop",
            "shut down the laptop": "shutdown_laptop",
        }
        if act in power_aliases:
            from core.power_control import execute_power_sequence
            res = execute_power_sequence(power_aliases[act], confirmed=is_confirmed)
            return res.to_user_message()

        return f"Unknown laptop_control action: {act}"
    except Exception as exc:
        return f"Laptop control error ({act}): {exc}"
