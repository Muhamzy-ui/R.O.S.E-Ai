"""
Universal Windows Application & Workspace Launcher for R.O.S.E.
- Restores/focuses already-open application windows by default unless force_new=True.
- Indexes & fuzzy-matches ALL installed Windows applications across:
  1. URI schemes (`ms-windows-store:`, `ms-settings:`, `microsoft.windows.camera:`, etc.)
  2. Common local app paths & PATH executables
  3. HKLM / HKCU Registry `App Paths` (cleanly unquoted + env-expanded)
  4. Windows Start Menu `Get-StartApps` (160+ Store/UWP/PWA/Win32 apps launched via `explorer.exe shell:AppsFolder\\<AppID>`)
  5. Start Menu `.lnk` shortcuts
- Supports `list_installed_apps(filter_text)` (and `open_app("list")` / `open_app("list:<query>")`).
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import winreg
from pathlib import Path

from actions.laptop_control import restore_or_focus_app

_NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

APP_ALIASES = {
    # Microsoft Store & System Apps
    "microsoft store": "ms-windows-store:",
    "ms store":        "ms-windows-store:",
    "windows store":   "ms-windows-store:",
    "store":           "ms-windows-store:",
    "app store":       "ms-windows-store:",
    "camera":          "microsoft.windows.camera:",
    "webcam":          "microsoft.windows.camera:",
    "clock":           "ms-clock:",
    "alarm":           "ms-clock:",
    "alarms":          "ms-clock:",
    "timer":           "ms-clock:",
    "photos":          "ms-photos:",
    "maps":            "bingmaps:",
    "weather":         "bingweather:",
    "xbox":            "xbox:",
    "snipping tool":   "ms-screenclip:",
    "screen snip":     "ms-screenclip:",
    "windows security": "windowsdefender:",
    "defender":        "windowsdefender:",
    "settings":        "ms-settings:",
    "system settings": "ms-settings:",
    "bluetooth":       "ms-settings:bluetooth",
    "wifi":            "ms-settings:network-wifi",
    "network settings": "ms-settings:network",
    "sound settings":  "ms-settings:sound",
    "display settings": "ms-settings:display",
    "windows update":  "ms-settings:windowsupdate",
    # Browsers
    "edge":            "msedge",
    "microsoft edge":  "msedge",
    "chrome":          "chrome",
    "google chrome":   "chrome",
    "firefox":         "firefox",
    "mozilla":         "firefox",
    "mozilla firefox": "firefox",
    "fire fox":        "firefox",
    "tor":             "Tor Browser",
    "tor browser":     "Tor Browser",
    "brave":           "brave",
    "opera":           "opera",
    "safari":          "msedge",
    # Terminals & System Tools
    "terminal":        "wt",
    "windows terminal": "wt",
    "powershell":      "powershell",
    "cmd":             "cmd",
    "command prompt":  "cmd",
    "explorer":        "explorer",
    "file explorer":   "explorer",
    "files":           "explorer",
    "finder":          "explorer",
    "calculator":      "calc",
    "calc":            "calc",
    "task manager":    "taskmgr",
    "control panel":   "control",
    "paint":           "mspaint",
    "preview":         "mspaint",
    "notepad":         "notepad",
    "notes":           "notepad",
    "textedit":        "notepad",
    "wordpad":         "write",
    # Workspace & Installed PC Apps
    "antigravity":     "Antigravity",
    "antigravity ide": "Antigravity IDE",
    "anti gravity":    "Antigravity",
    "antygravity":     "Antigravity",
    "anty gravity":    "Antigravity",
    "spotify":         "spotify",
    "vscode":          "code",
    "vs code":         "code",
    "code":            "code",
    "visual studio code": "code",
    "notion":          "notion",
    "slack":           "slack",
    "discord":         "discord",
    "whatsapp":        "WhatsApp",
    "telegram":        "Telegram",
    "teams":           "Microsoft Teams",
    "ms teams":        "Microsoft Teams",
    "microsoft teams": "Microsoft Teams",
    "zoom":            "zoom",
    "vlc":             "vlc",
    "vlc player":      "vlc",
    "vlc media player": "vlc",
    "clipchamp":       "Microsoft Clipchamp",
    "tiktok studio":   "TikTok LIVE Studio",
    "tiktok live":     "TikTok LIVE Studio",
    "tiktok live studio": "TikTok LIVE Studio",
    "7zip":            "7-Zip File Manager",
    "7 zip":           "7-Zip File Manager",
    "7-zip":           "7-Zip File Manager",
    "edex":            "eDEX-UI",
    "edex ui":         "eDEX-UI",
    "edex-ui":         "eDEX-UI",
    "epic games":      "Epic Games Launcher",
    "epic":            "Epic Games Launcher",
    "steam":           "Steam",
    "ea":              "EA",
    "ea app":          "EA",
    "rockstar":        "Rockstar Games Launcher",
    "pcsx2":           "PCSX2",
    "nmap":            "Nmap - Zenmap GUI",
    "zenmap":          "Nmap - Zenmap GUI",
    "kali":            "kali-linux",
    "kali linux":      "kali-linux",
    "git bash":        "Git Bash",
    "responsively":    "ResponsivelyApp",
    "lively":          "Lively Wallpaper",
    "smadav":          "SMADAV",
    "9jacash":         "9jaCash",
    "moviebox":        "MovieBox",
    "uhrs":            "UHRS",
    "windhawk":        "Windhawk",
    "safe exam browser": "Safe Exam Browser",
    "seb":             "Safe Exam Browser",
    "lg switch":       "LG Switch",
    "mail":            "outlook",
    "outlook":         "outlook",
    "calendar":        "outlookcal:",
    "media player":    "Media Player",
    "word":            "winword",
    "microsoft word":  "winword",
    "excel":           "excel",
    "microsoft excel": "excel",
    "powerpoint":      "powerpnt",
    "figma":           "figma",
    "postman":         "postman",
    "docker":          "Docker Desktop",
}

_URI_PREFIXES = (
    "ms-settings:",
    "ms-photos:",
    "bingmaps:",
    "bingweather:",
    "outlookcal:",
    "ms-windows-store:",
    "microsoft.windows.camera:",
    "ms-clock:",
    "ms-screenclip:",
    "windowsdefender:",
    "xbox:",
    "shell:",
    "spotify:",
)

DEFAULT_WORKSPACE_APPS = ["Chrome", "Antigravity", "WhatsApp"]

# In-memory cache for Get-StartApps + Registry + Shortcuts (fast fuzzy lookup)
_CATALOG_CACHE: dict = {"ts": 0.0, "start_apps": [], "reg_paths": {}, "lnk_paths": {}}
_JUNK_PREFIXES = ("uninstall ", "install additional ")
_JUNK_SUBSTRS = ("error reporter", "frequently asked questions", "release notes", "user manual", "help", "support center")


def _norm_alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _clean_unquoted_path(val: str) -> str:
    s = os.path.expandvars((val or "").strip())
    if s.startswith('"') and '"' in s[1:]:
        s = s[1 : s.index('"', 1)]
    else:
        s = s.strip('"\'')
    if s.lower().endswith(".exe") and os.path.exists(s):
        return s
    # Strip trailing arguments like "%1" or ",0"
    m = re.match(r"^([A-Za-z]:\\[^\"*,?<>|\r\n]+?\.exe)\b", s, flags=re.IGNORECASE)
    if m and os.path.exists(m.group(1)):
        return m.group(1)
    return s if os.path.exists(s) else ""


def _refresh_catalog(force: bool = False) -> dict:
    now = time.time()
    if not force and (now - _CATALOG_CACHE["ts"]) < 120.0 and (_CATALOG_CACHE["start_apps"] or _CATALOG_CACHE["reg_paths"]):
        return _CATALOG_CACHE

    # 1. Start Menu Apps via Get-StartApps -ConvertTo-Json (UTF-8 safe)
    start_apps: list[dict] = []
    try:
        res = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            timeout=8,
            creationflags=_NO_WIN,
        )
        raw = (res.stdout or b"").decode("utf-8", errors="ignore").strip()
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                parsed = [parsed]
            if isinstance(parsed, list):
                for item in parsed:
                    name = str(item.get("Name", "") or "").strip()
                    appid = str(item.get("AppID", "") or "").strip()
                    if name and appid:
                        start_apps.append({"name": name, "appid": appid})
    except Exception:
        pass

    # 2. HKLM / HKCU App Paths
    reg_paths: dict[str, str] = {}
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths") as key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, i)
                        i += 1
                        try:
                            with winreg.OpenKey(key, sub) as sk:
                                val, _ = winreg.QueryValueEx(sk, None)
                                clean = _clean_unquoted_path(str(val or ""))
                                if clean:
                                    reg_paths[sub.lower()] = clean
                                    reg_paths[Path(sub).stem.lower()] = clean
                        except OSError:
                            continue
                    except OSError:
                        break
        except OSError:
            continue

    # 3. Start Menu .lnk shortcuts (both ProgramData and AppData)
    lnk_paths: dict[str, str] = {}
    sm_dirs = (
        Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    )
    for d in sm_dirs:
        if not d.exists():
            continue
        try:
            for p in d.rglob("*.lnk"):
                stem_low = p.stem.lower()
                if stem_low.startswith(_JUNK_PREFIXES):
                    continue
                lnk_paths.setdefault(stem_low, str(p))
        except Exception:
            continue

    _CATALOG_CACHE["ts"] = now
    _CATALOG_CACHE["start_apps"] = start_apps
    _CATALOG_CACHE["reg_paths"] = reg_paths
    _CATALOG_CACHE["lnk_paths"] = lnk_paths
    return _CATALOG_CACHE


def _start_uri(uri: str) -> bool:
    try:
        os.startfile(uri)
        return True
    except OSError:
        try:
            subprocess.Popen(["cmd", "/c", "start", "", uri], shell=False)
            return True
        except Exception:
            return False


def _launch_appid(appid: str) -> bool:
    """Launches any Start Menu UWP, PWA, or Win32 AppID via explorer shell:AppsFolder."""
    if not appid:
        return False
    try:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{appid}"], close_fds=True)
        return True
    except Exception:
        pass
    try:
        safe_id = appid.replace("'", "''")
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Start-Process ('shell:AppsFolder\\{safe_id}')"],
            capture_output=True,
            timeout=5,
            creationflags=_NO_WIN,
        )
        return res.returncode == 0
    except Exception:
        return False


def _is_junk_entry(name: str, query: str) -> bool:
    low = name.lower()
    q_low = query.lower()
    if low.startswith(_JUNK_PREFIXES) and "uninstall" not in q_low:
        return True
    if any(j in low for j in _JUNK_SUBSTRS) and not any(j in q_low for j in ("help", "manual", "faq", "uninstall")):
        return True
    return False


def _find_best_start_app(query: str) -> dict | None:
    cat = _refresh_catalog()
    apps = cat.get("start_apps") or []
    if not apps:
        return None

    q_low = query.strip().lower()
    q_norm = _norm_alnum(query)
    q_tokens = [t for t in re.split(r"[^a-z0-9]+", q_low) if t]

    candidates = [a for a in apps if not _is_junk_entry(a["name"], q_low)]
    if not candidates:
        candidates = apps

    # Tier 1: Exact case-insensitive or exact alphanumeric match
    for a in candidates:
        if a["name"].lower() == q_low or _norm_alnum(a["name"]) == q_norm:
            return a

    # Tier 2: Starts with / word boundary / token coverage match (prefer shorter clean name like "Firefox" over "Firefox Private Browsing")
    scored: list[tuple[int, int, dict]] = []
    for a in candidates:
        nm_low = a["name"].lower()
        nm_norm = _norm_alnum(nm_low)
        nm_tokens = [t for t in re.split(r"[^a-z0-9]+", nm_low) if t]
        score = 0
        if q_low in nm_low:
            score += 60
            if nm_low.startswith(q_low):
                score += 25
        elif q_norm and q_norm in nm_norm:
            score += 55
        if q_tokens and all(any(nt.startswith(qt) or qt == nt for nt in nm_tokens) for qt in q_tokens):
            score += 45
        if "private" in nm_low and "private" not in q_low:
            score -= 20
        if score > 0:
            scored.append((score, -len(a["name"]), a))

    if scored:
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return scored[0][2]

    # Tier 3: difflib fuzzy match for slight voice transcription typos
    name_map = {a["name"].lower(): a for a in candidates}
    close = difflib.get_close_matches(q_low, list(name_map.keys()), n=1, cutoff=0.68)
    if close:
        return name_map[close[0]]

    norm_map = {_norm_alnum(a["name"]): a for a in candidates if _norm_alnum(a["name"])}
    close_norm = difflib.get_close_matches(q_norm, list(norm_map.keys()), n=1, cutoff=0.68)
    if close_norm:
        return norm_map[close_norm[0]]

    return None


def _find_local_app_exe(name: str) -> str | None:
    low = name.strip().lower()
    local_app = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))

    if "antigravity" in low or "antygravity" in low:
        for c in (
            local_app / "Programs" / "Antigravity" / "Antigravity.exe",
            local_app / "Programs" / "Antigravity IDE" / "Antigravity IDE.exe",
            program_files / "Antigravity" / "Antigravity.exe",
            program_files / "Antigravity IDE" / "Antigravity IDE.exe",
            program_files / "Google" / "Antigravity IDE" / "Antigravity IDE.exe",
        ):
            if c.exists():
                return str(c)

    if low == "chrome" or "google chrome" in low:
        for c in (
            program_files / "Google" / "Chrome" / "Application" / "chrome.exe",
            program_files_x86 / "Google" / "Chrome" / "Application" / "chrome.exe",
            local_app / "Google" / "Chrome" / "Application" / "chrome.exe",
        ):
            if c.exists():
                return str(c)

    if "firefox" in low:
        for c in (
            program_files / "Mozilla Firefox" / "firefox.exe",
            program_files_x86 / "Mozilla Firefox" / "firefox.exe",
            local_app / "Mozilla Firefox" / "firefox.exe",
        ):
            if c.exists():
                return str(c)

    return None


def _registry_app_paths(name: str) -> str | None:
    """Searches HKLM/HKCU App Paths cleanly (with unquoted paths)."""
    cat = _refresh_catalog()
    reg_paths = cat.get("reg_paths") or {}
    low = name.strip().lower()
    for key in (low, low + ".exe", _norm_alnum(low)):
        if key in reg_paths and os.path.exists(reg_paths[key]):
            return reg_paths[key]
    for rk, rv in reg_paths.items():
        if low in rk and os.path.exists(rv):
            return rv
    return None


def _find_lnk_shortcut(name: str) -> str | None:
    cat = _refresh_catalog()
    lnks = cat.get("lnk_paths") or {}
    low = name.strip().lower()
    if low in lnks:
        return lnks[low]
    matches = [(k, v) for k, v in lnks.items() if low in k]
    if matches:
        matches.sort(key=lambda pair: len(pair[0]))
        return matches[0][1]
    close = difflib.get_close_matches(low, list(lnks.keys()), n=1, cutoff=0.72)
    if close:
        return lnks[close[0]]
    return None


def _parse_force_new(app_name: str, force_new: bool) -> tuple[str, bool]:
    raw = (app_name or "").strip()
    low = raw.lower()
    want_new = bool(force_new)
    if re.search(r"\b(new|new window|new instance|another|fresh)\b", low):
        want_new = True
        cleaned = re.sub(
            r"\b(open|launch|start|a|another|fresh|new window|new instance|window|instance|new)\b",
            " ",
            raw,
            flags=re.IGNORECASE,
        )
        cleaned = " ".join(cleaned.split()).strip()
        if cleaned:
            raw = cleaned
    # Strip natural trailing words like "application", "app", "program"
    cleaned2 = re.sub(r"\b(the|application|app|software|program|please|for me)\b", " ", raw, flags=re.IGNORECASE)
    cleaned2 = " ".join(cleaned2.split()).strip()
    if cleaned2:
        raw = cleaned2
    return raw, want_new


def list_installed_apps(filter_text: str = "") -> str:
    """Lists installed applications discovered on Windows Start Menu & Registry."""
    cat = _refresh_catalog(force=True)
    names = sorted({
        a["name"]
        for a in (cat.get("start_apps") or [])
        if not _is_junk_entry(a["name"], filter_text or "")
    })
    flt = (filter_text or "").strip().lower()
    if flt:
        names = [n for n in names if flt in n.lower() or _norm_alnum(flt) in _norm_alnum(n)]
    if not names:
        return f"No installed apps matching '{filter_text}' found in Windows Start Menu."
    preview = ", ".join(names[:60])
    extra = f" (and {len(names) - 60} more)" if len(names) > 60 else ""
    return f"Installed Windows apps ({len(names)}): {preview}{extra}"


def open_app(app_name: str, force_new: bool = False) -> str:
    if not app_name or not app_name.strip():
        return "Application name not specified."

    raw_stripped = app_name.strip()
    if raw_stripped.lower() in ("list", "list_apps", "installed", "all"):
        return list_installed_apps()
    if raw_stripped.lower().startswith("list:"):
        return list_installed_apps(raw_stripped.split(":", 1)[1])

    cleaned_name, want_new = _parse_force_new(raw_stripped, force_new)
    normalized = cleaned_name.lower().strip()

    # Handle Microsoft Store queries
    if "store" in normalized and normalized not in APP_ALIASES:
        query_part = (
            normalized.replace("microsoft store", "")
            .replace("windows store", "")
            .replace("ms store", "")
            .replace("store", "")
            .strip()
        )
        if query_part:
            uri = f"ms-windows-store://search/?query={urllib.parse.quote(query_part)}"
            if _start_uri(uri):
                return f"Opened Microsoft Store searching for '{query_part}'."
        if _start_uri("ms-windows-store:"):
            return "Opened Microsoft Store."

    # Also strip trailing "browser" only if alias doesn't keep it (keeps "Tor Browser", turns "Firefox browser" -> "Firefox")
    resolved = APP_ALIASES.get(normalized)
    if not resolved and normalized.endswith(" browser"):
        short_b = normalized[: -len(" browser")].strip()
        resolved = APP_ALIASES.get(short_b, short_b)
    if not resolved:
        resolved = cleaned_name

    # Fuzzy-find Start Menu entry for display & window matching too
    start_match = None if any(resolved.startswith(p) for p in _URI_PREFIXES) else (
        _find_best_start_app(resolved) or _find_best_start_app(cleaned_name)
    )

    # 0. Restore/focus existing window FIRST unless caller asked for a new window/instance
    if not want_new:
        for probe in dict.fromkeys([cleaned_name, resolved, start_match["name"] if start_match else ""]):
            if not probe:
                continue
            found, title = restore_or_focus_app(probe)
            if found:
                return f"{probe} was already open — brought it to focus ({title})."

    # 1. URI scheme
    if any(resolved.startswith(p) for p in _URI_PREFIXES):
        if _start_uri(resolved):
            return f"Opened {cleaned_name}."

    # 2. Direct local exe / PATH / Registry App Paths
    exe = (
        _find_local_app_exe(normalized)
        or _find_local_app_exe(resolved)
        or shutil.which(resolved)
        or shutil.which(resolved + ".exe")
        or _registry_app_paths(resolved)
        or _registry_app_paths(cleaned_name)
    )
    if exe:
        try:
            cmd = [exe]
            exe_low = os.path.basename(exe).lower()
            if want_new and exe_low in ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"):
                cmd.append("--new-window" if "firefox" not in exe_low else "-new-window")
            subprocess.Popen(cmd, close_fds=True)
            return f"Launched a new {cleaned_name} window." if want_new else f"Opened {cleaned_name}."
        except Exception:
            pass

    # 3. Matched Start Menu UWP / PWA / Win32 AppID via explorer shell:AppsFolder
    if start_match and _launch_appid(start_match["appid"]):
        return f"Opened {start_match['name']}."

    # 4. Start Menu .lnk shortcut
    lnk = _find_lnk_shortcut(resolved) or _find_lnk_shortcut(cleaned_name)
    if lnk:
        try:
            os.startfile(lnk)
            return f"Opened {Path(lnk).stem}."
        except Exception:
            pass

    # 5. os.startfile raw fallback
    try:
        os.startfile(resolved)
        return f"Opened {cleaned_name}."
    except OSError:
        pass

    return (
        f"Could not find '{cleaned_name}' installed on this PC. "
        f"Say 'download {cleaned_name}' if you want me to install it, or 'list installed apps' to check exact app names."
    )


def open_workspace(apps: list[str] | str | None = None, force_new: bool = False) -> str:
    """
    Opens or restores the user's daily workspace apps (default: Chrome, Antigravity, WhatsApp).
    Always checks if each app is already open and brings existing windows forward instead of opening duplicates,
    unless force_new=True is requested.
    """
    if isinstance(apps, str):
        parsed = [a.strip() for a in re.split(r"[,;/]+|\band\b", apps) if a.strip()]
        target_apps = parsed or list(DEFAULT_WORKSPACE_APPS)
    elif isinstance(apps, (list, tuple)) and len(apps) > 0:
        target_apps = [str(a).strip() for a in apps if str(a).strip()]
    else:
        target_apps = list(DEFAULT_WORKSPACE_APPS)

    restored: list[str] = []
    launched: list[str] = []
    failed: list[str] = []

    for app in target_apps:
        status = open_app(app, force_new=bool(force_new))
        low = status.lower()
        if "already open" in low or "brought it to focus" in low:
            restored.append(app)
        elif low.startswith("opened ") or low.startswith("launched "):
            launched.append(app)
        else:
            failed.append(f"{app} ({status})")

    parts = []
    if restored:
        parts.append("Restored already-open: " + ", ".join(restored))
    if launched:
        parts.append(("Opened fresh: " if force_new else "Launched: ") + ", ".join(launched))
    if failed:
        parts.append("Could not open: " + "; ".join(failed))
    return "Workspace ready. " + " | ".join(parts) if parts else "Workspace check completed."
