"""
Browser control — Windows version.
Opens default browser via os.startfile / webbrowser.
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import os
import re
import subprocess
import time
import urllib.parse
import webbrowser

import requests

_VIDEO_ID_RE = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')


def _open(url: str) -> None:
    try:
        os.startfile(url)
    except OSError:
        webbrowser.open(url, new=2)


def _find_first_youtube_video(query: str) -> str | None:
    encoded = urllib.parse.quote_plus(query)
    response = requests.get(
        f"https://www.youtube.com/results?search_query={encoded}",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    response.raise_for_status()

    seen: set[str] = set()
    for video_id in _VIDEO_ID_RE.findall(response.text):
        if video_id not in seen:
            seen.add(video_id)
            return video_id
    return None


def _chrome_winrt_play_status(try_play: bool = True) -> str:
    """
    Uses Windows WinRT MediaSession API to inspect Chrome playback status and optionally
    invoke TryPlayAsync() (which ONLY starts/resumes playback and never pauses an active track).
    Returns: 'PLAYING' | 'RESUMED' | 'PAUSED' | 'NONE'
    """
    ps = (
        "[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,"
        "Windows.Media.Control,ContentType=WindowsRuntime] | Out-Null; "
        "$m = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]"
        "::RequestAsync().GetAwaiter().GetResult(); "
        "$sessions = @($m.GetSessions() | Where-Object { $_.SourceAppUserModelId -match 'chrome|msedge' }); "
        "if (-not $sessions -or $sessions.Count -eq 0) { Write-Output 'NONE'; exit 0 } "
        "$playing = $sessions | Where-Object { $_.GetPlaybackInfo().PlaybackStatus.ToString() -eq 'Playing' }; "
        "if ($playing) { Write-Output 'PLAYING'; exit 0 } "
    )
    if try_play:
        ps += (
            "foreach ($s in $sessions) { "
            "  try { $null = $s.TryPlayAsync().GetAwaiter().GetResult(); Write-Output 'RESUMED'; exit 0 } catch {} "
            "} "
        )
    ps += "Write-Output 'PAUSED'"

    try:
        enc = base64.b64encode(ps.encode("utf-16le")).decode("ascii")
        res = subprocess.run(
            ["powershell", "-NoProfile", "-EncodedCommand", enc],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        out = (res.stdout or "").strip().splitlines()
        return out[-1].strip() if out else "NONE"
    except Exception:
        return "NONE"


def _focus_chrome_window() -> tuple[int, tuple[int, int, int, int] | None]:
    """Brings an open Chrome (or Edge) window to foreground and returns (hwnd, (l,t,r,b))."""
    try:
        from actions.laptop_control import _find_matching_windows, SW_RESTORE, SW_SHOW
        wins = _find_matching_windows("chrome") or _find_matching_windows("edge")
        if not wins:
            return 0, None
        hwnd = wins[0]["hwnd"]
        u32 = ctypes.windll.user32
        u32.ShowWindow(hwnd, SW_RESTORE if wins[0].get("minimized") else SW_SHOW)
        try:
            u32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        rect = ctypes.wintypes.RECT()
        if u32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return hwnd, (rect.left, rect.top, rect.right, rect.bottom)
        return hwnd, None
    except Exception:
        return 0, None


def ensure_chrome_media_playing(wait_initial: float = 2.2) -> str:
    """
    Waits briefly for a newly opened Chrome media tab to load (if needed), checks WinRT status
    so already-playing audio is never paused, calls TryPlayAsync, and if blocked by Chrome
    autoplay policy sends a user gesture ('k' shortcut or player click) to start playback.
    """
    if wait_initial > 0:
        time.sleep(wait_initial)

    status = _chrome_winrt_play_status(try_play=True)
    if status in ("PLAYING", "RESUMED"):
        return "Chrome playback active."

    # Chrome tab may be waiting for an explicit user gesture (click / keypress)
    hwnd, rect = _focus_chrome_window()
    if hwnd:
        time.sleep(0.35)
        try:
            import pyautogui  # type: ignore
            # YouTube / YMusic respond immediately to 'k' for play/pause without scrolling
            pyautogui.press("k")
            time.sleep(0.8)
            if _chrome_winrt_play_status(try_play=True) in ("PLAYING", "RESUMED"):
                return "Started Chrome playback via media key."
            # Click inside the center-upper player canvas to satisfy Chrome gesture policy
            if rect:
                l, t, r, b = rect
                w, h = max(200, r - l), max(200, b - t)
                pyautogui.click(l + int(w * 0.38), t + int(h * 0.42))
                time.sleep(0.5)
        except Exception:
            pass
    return "Focused Chrome to start playback."


def browser_control(action: str, url: str = None, query: str = None) -> str:
    act = (action or "").strip().lower()
    if act == "open_url":
        if not url:
            return "No URL specified."
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        _open(url)
        return f"Opened in browser: {url}"

    elif act == "search":
        if not query:
            return "No search query specified."
        encoded = urllib.parse.quote(query)
        search_url = f"https://www.google.com/search?q={encoded}"
        _open(search_url)
        return f"Opened Google search for '{query}'."

    elif act in ("play_youtube", "youtube_play", "play_music"):
        if not query:
            return "No search query specified for YouTube."

        try:
            video_id = _find_first_youtube_video(query)
        except Exception as exc:
            encoded = urllib.parse.quote(query)
            fallback_url = f"https://www.youtube.com/results?search_query={encoded}"
            _open(fallback_url)
            return f"Opened YouTube search results for '{query}' ({exc})."

        if not video_id:
            encoded = urllib.parse.quote(query)
            fallback_url = f"https://www.youtube.com/results?search_query={encoded}"
            _open(fallback_url)
            return f"Opened YouTube search results for '{query}'."

        watch_url = f"https://www.youtube.com/watch?v={video_id}&autoplay=1"
        _open(watch_url)
        ensure_chrome_media_playing(wait_initial=2.3)
        return f"Playing on YouTube in Chrome: {query}"

    elif act in ("click_play", "play", "resume", "chrome_play"):
        return ensure_chrome_media_playing(wait_initial=0.1)

    return f"Unknown browser action: {action}"
