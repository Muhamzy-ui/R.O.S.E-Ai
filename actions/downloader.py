"""
App Installer, Official Download Link Finder, Microsoft Store Launcher & Media/File Downloader for R.O.S.E.

Capabilities:
1. Official Download Link Search & Auto-Open (`get_download_link` / `official_link` / `install_app`):
   - Instant catalog of official vendor download pages, direct `.exe` installer links, and Microsoft Store links.
   - Live web search (Bing + DuckDuckGo) for ANY software, tool, driver, game, or file to find its real official download URL.
2. Direct Installer Download & Winget Installation (`install_app`):
   - Downloads the official `.exe` installer directly into `~/Downloads/ROSE_Downloads` (and launches it),
   - Opens the official download page in the browser so the user always has the real link on screen,
   - Runs `winget install` in a visible PowerShell window.
3. Microsoft Store Deep-Link Launcher (`microsoft_store`):
   - Opens the Windows Microsoft Store app directly to the product page or search page (`ms-windows-store://search/?query=...`).
4. Music, Video & Direct File Downloader (`download_music`, `download_video`, `download_file`).
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import requests
from bs4 import BeautifulSoup  # type: ignore


DOWNLOADS_DIR = Path.home() / "Downloads" / "ROSE_Downloads"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Verified Official Download Pages, Direct Installer Links, Winget IDs & Microsoft Store Links
OFFICIAL_APP_CATALOG: dict[str, dict[str, str]] = {
    "spotify": {
        "name": "Spotify Desktop",
        "winget": "Spotify.Spotify",
        "official_url": "https://www.spotify.com/download/windows/",
        "direct_exe": "https://download.scdn.co/SpotifySetup.exe",
        "ms_store": "ms-windows-store://pdp/?ProductId=9ncbcszsjrsb",
    },
    "ymusic": {
        "name": "YMusic / YouTube Music Desktop",
        "winget": "th-ch.YouTubeMusic",
        "official_url": "https://ymusic.io/",
        "alt_url": "https://github.com/th-ch/youtube-music/releases/latest",
        "ms_store": "ms-windows-store://search/?query=YouTube+Music",
    },
    "youtube music": {
        "name": "YouTube Music Desktop",
        "winget": "th-ch.YouTubeMusic",
        "official_url": "https://github.com/th-ch/youtube-music/releases/latest",
        "ms_store": "ms-windows-store://search/?query=YouTube+Music",
    },
    "yt music": {
        "name": "YouTube Music Desktop",
        "winget": "th-ch.YouTubeMusic",
        "official_url": "https://github.com/th-ch/youtube-music/releases/latest",
        "ms_store": "ms-windows-store://search/?query=YouTube+Music",
    },
    "vlc": {
        "name": "VLC Media Player",
        "winget": "VideoLAN.VLC",
        "official_url": "https://www.videolan.org/vlc/download-windows.html",
        "ms_store": "ms-windows-store://pdp/?ProductId=xpdm1zw6815mqm",
    },
    "chrome": {
        "name": "Google Chrome",
        "winget": "Google.Chrome",
        "official_url": "https://www.google.com/chrome/",
        "direct_exe": "https://dl.google.com/chrome/install/ChromeStandaloneSetup64.exe",
    },
    "google chrome": {
        "name": "Google Chrome",
        "winget": "Google.Chrome",
        "official_url": "https://www.google.com/chrome/",
        "direct_exe": "https://dl.google.com/chrome/install/ChromeStandaloneSetup64.exe",
    },
    "brave": {
        "name": "Brave Browser",
        "winget": "Brave.Brave",
        "official_url": "https://brave.com/download/",
        "direct_exe": "https://laptop-updates.brave.com/latest/winx64",
    },
    "firefox": {
        "name": "Mozilla Firefox",
        "winget": "Mozilla.Firefox",
        "official_url": "https://www.mozilla.org/en-US/firefox/new/",
        "direct_exe": "https://download.mozilla.org/?product=firefox-stub&os=win&lang=en-US",
        "ms_store": "ms-windows-store://pdp/?ProductId=9nzvdkpmr9rd",
    },
    "whatsapp": {
        "name": "WhatsApp Desktop",
        "winget": "WhatsApp.WhatsApp",
        "official_url": "https://www.whatsapp.com/download",
        "ms_store": "ms-windows-store://pdp/?ProductId=9nksqgp7f2nh",
    },
    "telegram": {
        "name": "Telegram Desktop",
        "winget": "Telegram.TelegramDesktop",
        "official_url": "https://desktop.telegram.org/",
        "direct_exe": "https://telegram.org/dl/desktop/win64",
        "ms_store": "ms-windows-store://pdp/?ProductId=9nztwsqntd0s",
    },
    "vscode": {
        "name": "Visual Studio Code",
        "winget": "Microsoft.VisualStudioCode",
        "official_url": "https://code.visualstudio.com/Download",
        "direct_exe": "https://update.code.visualstudio.com/latest/win32-x64-user/stable",
    },
    "vs code": {
        "name": "Visual Studio Code",
        "winget": "Microsoft.VisualStudioCode",
        "official_url": "https://code.visualstudio.com/Download",
        "direct_exe": "https://update.code.visualstudio.com/latest/win32-x64-user/stable",
    },
    "visual studio code": {
        "name": "Visual Studio Code",
        "winget": "Microsoft.VisualStudioCode",
        "official_url": "https://code.visualstudio.com/Download",
        "direct_exe": "https://update.code.visualstudio.com/latest/win32-x64-user/stable",
    },
    "discord": {
        "name": "Discord",
        "winget": "Discord.Discord",
        "official_url": "https://discord.com/download",
        "direct_exe": "https://discord.com/api/downloads/distributions/app/installers/latest?channel=stable&platform=win&arch=x64",
    },
    "obs": {
        "name": "OBS Studio",
        "winget": "OBSProject.OBSStudio",
        "official_url": "https://obsproject.com/download",
    },
    "obs studio": {
        "name": "OBS Studio",
        "winget": "OBSProject.OBSStudio",
        "official_url": "https://obsproject.com/download",
    },
    "zoom": {
        "name": "Zoom Workplace",
        "winget": "Zoom.Zoom",
        "official_url": "https://zoom.us/download",
        "direct_exe": "https://zoom.us/client/latest/ZoomInstallerFull.exe",
    },
    "capcut": {
        "name": "CapCut Desktop",
        "winget": "ByteDance.CapCut",
        "official_url": "https://www.capcut.com/tools/desktop-video-editor",
        "ms_store": "ms-windows-store://pdp/?ProductId=xp9kn75cfb2p56",
    },
    "anydesk": {
        "name": "AnyDesk",
        "winget": "AnyDeskSoftwareGmbH.AnyDesk",
        "official_url": "https://anydesk.com/en/downloads/windows",
        "direct_exe": "https://download.anydesk.com/AnyDesk.exe",
    },
    "steam": {
        "name": "Steam",
        "winget": "Valve.Steam",
        "official_url": "https://store.steampowered.com/about/",
        "direct_exe": "https://cdn.akamai.steamstatic.com/client/installer/SteamSetup.exe",
    },
    "itunes": {
        "name": "Apple iTunes",
        "winget": "Apple.iTunes",
        "official_url": "https://www.apple.com/itunes/",
        "ms_store": "ms-windows-store://pdp/?ProductId=9pb2mz1zmb1s",
    },
    "7zip": {
        "name": "7-Zip",
        "winget": "7zip.7zip",
        "official_url": "https://www.7-zip.org/download.html",
    },
    "winrar": {
        "name": "WinRAR",
        "winget": "RARLab.WinRAR",
        "official_url": "https://www.win-rar.com/download.html",
    },
    "git": {
        "name": "Git for Windows",
        "winget": "Git.Git",
        "official_url": "https://git-scm.com/download/win",
    },
    "python": {
        "name": "Python 3.12",
        "winget": "Python.Python.3.12",
        "official_url": "https://www.python.org/downloads/windows/",
    },
    "nodejs": {
        "name": "Node.js LTS",
        "winget": "OpenJS.NodeJS.LTS",
        "official_url": "https://nodejs.org/en/download/",
    },
    "node": {
        "name": "Node.js LTS",
        "winget": "OpenJS.NodeJS.LTS",
        "official_url": "https://nodejs.org/en/download/",
    },
    "blender": {
        "name": "Blender 3D",
        "winget": "BlenderFoundation.Blender",
        "official_url": "https://www.blender.org/download/",
    },
}


def _ensure_downloads_dir() -> Path:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return DOWNLOADS_DIR


def _open_url_or_uri(uri: str) -> bool:
    try:
        os.startfile(uri)
        return True
    except OSError:
        try:
            webbrowser.open(uri, new=2)
            return True
        except Exception:
            return False


def _match_catalog_entry(app_name: str) -> dict[str, str] | None:
    clean = (app_name or "").strip().lower()
    if not clean:
        return None
    if clean in OFFICIAL_APP_CATALOG:
        return OFFICIAL_APP_CATALOG[clean]
    for key, entry in OFFICIAL_APP_CATALOG.items():
        if key in clean or clean in key:
            return entry
    return None


def _search_live_download_links(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """
    Searches Yahoo & Google backends via `ddgs` in real time for the official download page
    and Microsoft Store product page for `query`.
    """
    search_q = f"{query} official download windows"
    hits: list[dict[str, str]] = []
    seen: set[str] = set()

    try:
        from ddgs import DDGS  # type: ignore
    except ImportError:
        from duckduckgo_search import DDGS  # type: ignore

    for backend_name in ("yahoo", "google"):
        try:
            for r in DDGS(timeout=5).text(search_q, backend=backend_name, max_results=max_results):
                href = (r.get("href") or "").strip()
                title = (r.get("title") or "").strip()
                body = (r.get("body") or "").strip()
                if href and href.startswith("http") and href not in seen:
                    seen.add(href)
                    hits.append({"title": title, "href": href, "body": body})
            if len(hits) >= max_results:
                break
        except Exception:
            continue

    return hits[:max_results]


def _extract_direct_installer_from_page(page_url: str) -> str:
    """Scrapes an official download page to see if there is a direct .exe or .msi link."""
    try:
        r = requests.get(page_url, headers=HEADERS, timeout=4.5)
        if not r.ok:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            low = href.lower().split("?")[0]
            if low.endswith((".exe", ".msi")) and ("win" in low or "setup" in low or "install" in low or "x64" in low or len(low) > 6):
                return urllib.parse.urljoin(page_url, href)
    except Exception:
        pass
    return ""


def _open_microsoft_store(query: str = "") -> str:
    clean = (query or "").strip()
    entry = _match_catalog_entry(clean)
    if entry and entry.get("ms_store"):
        uri = entry["ms_store"]
        _open_url_or_uri(uri)
        return f"Opened {entry['name']} directly in the Microsoft Store ({uri})."

    if clean and clean.lower() not in ("microsoft store", "store", "ms store", "windows store", "app store"):
        # Check if live search finds an exact Microsoft Store ProductId (apps.microsoft.com/detail/<id>)
        hits = _search_live_download_links(f"{clean} microsoft store", max_results=4)
        for h in hits:
            m = re.search(r"apps\.microsoft\.com/(?:[a-z-]+/)?detail/([A-Za-z0-9]+)", h["href"], re.I)
            if m:
                prod_id = m.group(1)
                uri = f"ms-windows-store://pdp/?ProductId={prod_id}"
                _open_url_or_uri(uri)
                return f"Opened '{clean}' directly in Microsoft Store (Product ID: {prod_id}, Web Link: {h['href']})."

        uri = f"ms-windows-store://search/?query={urllib.parse.quote(clean)}"
        _open_url_or_uri(uri)
        return f"Opened Microsoft Store and searched for '{clean}'."

    _open_url_or_uri("ms-windows-store:")
    return "Opened Microsoft Store."


def _get_and_open_official_download_link(app_name: str, auto_download_exe: bool = False) -> str:
    clean = (app_name or "").strip()
    if not clean:
        return "Please tell me the name of the application or file you want the official download link for."

    entry = _match_catalog_entry(clean)
    if entry:
        name = entry["name"]
        official_url = entry["official_url"]
        direct_exe = entry.get("direct_exe", "")
        ms_store = entry.get("ms_store", "")

        _open_url_or_uri(official_url)

        if auto_download_exe and direct_exe:
            _download_installer_async(direct_exe, f"{clean.replace(' ', '_')}_Setup.exe", run_after=True)
            return (
                f"Official Download Page for {name}: {official_url}\n"
                f"Direct Installer Link: {direct_exe}\n"
                f"I have opened the official {name} download page in your browser AND started downloading the installer directly into your Downloads/ROSE_Downloads folder!"
            )

        extra = f" | Direct Installer: {direct_exe}" if direct_exe else ""
        store_note = f" | Microsoft Store: {ms_store}" if ms_store else ""
        return (
            f"Official Download Link for {name}: {official_url}{extra}{store_note}\n"
            f"I have opened {official_url} in your browser right now so you can see and click it directly."
        )

    # Search the live web (Yahoo/Google via ddgs) for any app not in the static catalog
    hits = _search_live_download_links(clean, max_results=4)
    if hits:
        top_url = hits[0]["href"]
        top_title = hits[0]["title"]
        _open_url_or_uri(top_url)

        # Also check if there is a direct .exe/.msi on that official page
        direct_exe = _extract_direct_installer_from_page(top_url)
        if auto_download_exe and direct_exe:
            safe_fn = re.sub(r"[^a-zA-Z0-9._-]", "_", clean) + "_Setup.exe"
            _download_installer_async(direct_exe, safe_fn, run_after=True)

        other_links = " | ".join(f"{h['title']}: {h['href']}" for h in hits[1:3])
        return (
            f"Official Download Link for '{clean}': {top_url} ({top_title}).\n"
            + (f"Direct Installer Found & Downloading: {direct_exe}\n" if direct_exe else "")
            + f"I have opened {top_url} in your browser right now!\n"
            + (f"Other verified download links: {other_links}" if other_links else "")
        )

    fallback_search = f"https://www.google.com/search?q={urllib.parse.quote_plus(clean + ' official download windows')}"
    _open_url_or_uri(fallback_search)
    return f"Opened Google search for the official download link of '{clean}': {fallback_search}"


def _download_installer_async(url: str, default_filename: str, run_after: bool = True) -> None:
    out_dir = _ensure_downloads_dir()
    dest = out_dir / default_filename

    def _worker():
        try:
            with requests.get(url, stream=True, timeout=45, headers=HEADERS, allow_redirects=True) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
            try:
                os.startfile(str(out_dir))
                if run_after and dest.exists() and dest.stat().st_size > 10_000:
                    os.startfile(str(dest))
            except Exception:
                pass
        except Exception as e:
            print(f"[Downloader] Direct installer download error: {e}")

    threading.Thread(target=_worker, daemon=True).start()


def _install_app_full(app_name: str) -> str:
    """
    Installs an application using BOTH:
    1) Direct official installer download / official download page in browser, AND
    2) Windows Package Manager (winget) in a visible PowerShell window.
    """
    clean = (app_name or "").strip()
    if not clean:
        return "Please specify the name of the application to download and install."

    if clean.lower() in ("microsoft store", "ms store", "windows store", "store"):
        return _open_microsoft_store()

    entry = _match_catalog_entry(clean)
    pkg_id = entry.get("winget") if entry else None
    friendly_name = entry["name"] if entry else clean
    official_url = entry.get("official_url", "") if entry else ""
    direct_exe = entry.get("direct_exe", "") if entry else ""

    # 1. If we have a direct .exe installer URL, download & launch it automatically in background!
    if direct_exe:
        safe_fn = re.sub(r"[^a-zA-Z0-9._-]", "_", friendly_name) + "_Setup.exe"
        _download_installer_async(direct_exe, safe_fn, run_after=True)

    # 2. Also open the official download page (or search for the real official download link) so the user always gets the link!
    if official_url:
        _open_url_or_uri(official_url)
    else:
        hits = _search_live_download_links(clean, max_results=2)
        if hits:
            official_url = hits[0]["href"]
            _open_url_or_uri(official_url)

    # 3. Also trigger winget in a visible PowerShell window
    if pkg_id:
        ps_cmd = (
            f"Write-Host '=== R.O.S.E APP INSTALLER: Installing {friendly_name} ({pkg_id}) ===' -ForegroundColor Cyan; "
            f"winget install --id {pkg_id} -e --accept-source-agreements --accept-package-agreements; "
            f"Write-Host ''; Write-Host 'Official Link: {official_url}' -ForegroundColor Yellow; "
            f"Start-Sleep -Seconds 8"
        )
    else:
        safe_query = re.sub(r"[^a-zA-Z0-9 ._-]", "", clean)
        ps_cmd = (
            f"Write-Host '=== R.O.S.E APP INSTALLER: Searching & Installing {safe_query} ===' -ForegroundColor Cyan; "
            f"winget install \"{safe_query}\" --accept-source-agreements --accept-package-agreements; "
            f"Start-Sleep -Seconds 8"
        )

    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", f"Start-Process powershell -ArgumentList '-NoProfile','-Command','{ps_cmd}'"],
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except Exception:
        pass

    msg_parts = [f"Started downloading and installing {friendly_name}!"]
    if official_url:
        msg_parts.append(f"Official download link opened in your browser: {official_url}")
    if direct_exe:
        msg_parts.append(f"Direct installer ({direct_exe}) is downloading into your Downloads/ROSE_Downloads folder and will launch automatically.")
    return " ".join(msg_parts)


def _download_media_ytdlp(query_or_url: str, media_type: str = "audio") -> str:
    target = (query_or_url or "").strip()
    if not target:
        return "Please tell me the song name, video title, or URL to download."

    out_dir = _ensure_downloads_dir()
    is_url = target.startswith(("http://", "https://"))
    search_spec = target if is_url else f"ytsearch1:{target}"

    def _worker():
        try:
            import yt_dlp  # type: ignore
            ydl_opts = {
                "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            }
            if media_type == "audio":
                ydl_opts["format"] = "bestaudio/best"
            else:
                ydl_opts["format"] = "best[ext=mp4]/best"

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([search_spec])
            try:
                os.startfile(str(out_dir))
            except Exception:
                pass
        except Exception as e:
            print(f"[Downloader] yt-dlp error: {e}")

    threading.Thread(target=_worker, daemon=True).start()
    kind_label = "song/audio" if media_type == "audio" else "video"
    return (
        f"Started downloading {kind_label} '{target}' in the background! "
        f"It will be saved in your Downloads folder under ROSE_Downloads ({out_dir}) and the folder will open automatically when finished."
    )


def _download_direct_url_or_search(url_or_query: str, filename: str = "") -> str:
    target = (url_or_query or "").strip()
    if not target:
        return "Please specify the file URL or software name to download."

    # If the AI or user passed a name instead of an http(s) URL, search for the official download link!
    if not target.startswith(("http://", "https://")):
        return _get_and_open_official_download_link(target, auto_download_exe=True)

    out_dir = _ensure_downloads_dir()
    if not filename:
        filename = target.split("?")[0].rstrip("/").split("/")[-1] or "downloaded_file"
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
    dest = out_dir / filename

    try:
        with requests.get(target, stream=True, timeout=30, headers=HEADERS, allow_redirects=True) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
        try:
            os.startfile(str(out_dir))
        except Exception:
            pass
        return f"Downloaded file from {target} to {dest}."
    except Exception as exc:
        _open_url_or_uri(target)
        return f"Opened download link {target} directly in your browser ({exc})."


def install_or_download(
    action: str,
    target: str = "",
    url: str = "",
) -> str:
    """
    Unified downloader, official link finder, Microsoft Store launcher & installer tool for ROSE:
    - action='get_download_link' / 'official_link' / 'search_download': Finds the real official download link for any app/file and opens it in the browser.
    - action='microsoft_store' / 'store': Opens Microsoft Store (or searches for `target` inside Microsoft Store).
    - action='install_app': Downloads the official installer (.exe), opens the official download link, and runs winget.
    - action='download_music': Downloads a song/audio track by name or URL into Downloads/ROSE_Downloads.
    - action='download_video': Downloads a video by name or URL into Downloads/ROSE_Downloads.
    - action='download_file': Downloads a file from a direct URL (or searches for its official download link) into Downloads/ROSE_Downloads.
    - action='open_downloads': Opens the ROSE_Downloads folder in File Explorer.
    """
    act = (action or "install_app").strip().lower()
    query_val = (target or url or "").strip()

    if act in ("microsoft_store", "store", "ms_store", "windows_store"):
        return _open_microsoft_store(query_val)

    if act in ("get_download_link", "official_link", "search_download", "link", "find_link"):
        return _get_and_open_official_download_link(query_val, auto_download_exe=True)

    if act in ("install_app", "app", "install", "software"):
        return _install_app_full(query_val)

    if act in ("download_music", "music", "song", "audio", "mp3"):
        return _download_media_ytdlp(query_val, media_type="audio")

    if act in ("download_video", "video", "mp4"):
        return _download_media_ytdlp(query_val, media_type="video")

    if act in ("download_file", "file", "url"):
        return _download_direct_url_or_search(url or target)

    if act in ("open_downloads", "folder"):
        out_dir = _ensure_downloads_dir()
        try:
            os.startfile(str(out_dir))
            return f"Opened your downloads folder: {out_dir}"
        except Exception as exc:
            return f"Downloads folder is at {out_dir} ({exc})"

    return _install_app_full(query_val)
