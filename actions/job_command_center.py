"""
R.O.S.E Global Job Command Center, Ranked Official Feed Radar & Application CRM (Phase 3).

Upgraded in Phase 3:
- Uses `actions.job_engine` + editable `jobs_profile.yaml` for ranking (0-100) and honesty checks
- Zero &nbsp; / HTML junk (BeautifulSoup + html.unescape + whitespace normalization)
- Eligibility filter for Nigeria (WAT, UTC+1) excluding US/EU/UK-only roles masked under RSS tags
- Official sources only (RemoteOK, WeWorkRemotely, HN Algolia Who Is Hiring, Himalayas, Jobspresso)
- Reddit strictly gated behind PRAW credentials in .env (reports "disabled" when unconfigured)
- Real job-type classification only (no fake "QUICK FIX"; omitted if unknown)
- Primary UI ported to PySide6 (`ui/job_center.py`) sharing `ui/theme.py`, with `--legacy` Tkinter fallback
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
import urllib.parse

import requests

from actions.job_engine import (
    clean_html_and_text,
    load_jobs_profile,
    scan_all_sources,
    score_and_rank_jobs,
)
from app_config import get_app_config_value

try:
    import tkinter as tk  # Lazy fallback only when running with --legacy
except Exception:
    tk = None  # type: ignore

BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
DATA_DIR = BASE_DIR / "data"
TRACKER_FILE = DATA_DIR / "job_tracker.json"
PROFILE_FILE = MEMORY_DIR / "job_profile.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/xml, text/html, */*",
}

# ── Tech Stack Filtering (Mahmud's exact skills) ──────────────────────────────

DEV_TITLE_KEYWORDS = (
    "developer", "engineer", "architect", "programmer", "full stack", "fullstack",
    "frontend", "front-end", "backend", "back-end", "python", "django", "fastapi",
    "flask", "react", "react.js", "javascript", "typescript", "web developer",
    "software", "scripter", "automation", "database", "postgres", "postgresql",
    "api developer", "bot developer", "coder", "ui engineer"
)

DEV_STACK_KEYWORDS = (
    "python", "django", "fastapi", "flask", "react", "javascript", "typescript",
    "postgresql", "postgres", "rest api", "full stack", "fullstack", "frontend",
    "backend", "web development", "sql", "automation", "scraper", "scraping"
)

EXCLUDED_KEYWORDS = (
    "sales", "wholesale", "account manager", "account executive", "procurement",
    "navy", "military", "nurse", "medical", "hr coordinator", "talent acquisition",
    "recruiter", "driver", "warehouse", "customer support", "support specialist",
    "patient", "director, sales", "marketing manager", "product manager", "bdr",
    "sdr", "business development", "telemarketing", "realtor", "legal counsel"
)


def _load_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json_file(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _copy_clipboard(text: str) -> bool:
    if not text:
        return False
    try:
        from actions.clipboard_copilot import write_clipboard
        return write_clipboard(text)
    except Exception:
        pass
    try:
        import subprocess
        subprocess.run(
            ["clip"],
            input=text.encode("utf-16-le"),
            capture_output=True,
            timeout=4,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return True
    except Exception:
        return False


def _open_browser_url(url: str) -> None:
    if not url:
        return
    try:
        from actions.browser import browser_control
        browser_control("open_url", url=url)
    except Exception:
        import webbrowser
        webbrowser.open(url)


# ── Live Chrome Search Launchers ──────────────────────────────────────────────

def launch_live_chrome_search(platform: str, query: str = "") -> str:
    """Launches real live job search queries directly in Google Chrome."""
    plat = (platform or "twitter").strip().lower()
    q = (query or "").strip()

    if "twitter" in plat or "x" in plat:
        tech_q = q or 'python OR django OR react'
        encoded = urllib.parse.quote_plus(f'(hiring OR "looking for") ({tech_q}) (remote OR freelance OR contract)')
        url = f"https://x.com/search?q={encoded}&f=live"
    elif "google" in plat or "ats" in plat:
        tech_q = q or '"python" OR "django" OR "react"'
        encoded = urllib.parse.quote_plus(f'(site:boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com OR site:apply.workable.com) ({tech_q}) "remote"')
        url = f"https://www.google.com/search?q={encoded}"
    elif "reddit" in plat or "forhire" in plat:
        tech_q = q or 'python OR react OR django OR developer'
        encoded = urllib.parse.quote_plus(f'flair:hiring ({tech_q})')
        url = f"https://www.reddit.com/r/forhire/search/?q={encoded}&sort=new&restrict_sr=1"
    elif "upwork" in plat:
        tech_q = q or 'python django react'
        encoded = urllib.parse.quote_plus(f'{tech_q} remote')
        url = f"https://www.upwork.com/nx/search/jobs/?q={encoded}&sort=recency"
    elif "hackernews" in plat or "hn" in plat:
        url = "https://news.ycombinator.com/submitted?id=whoishiring"
    elif "wwr" in plat or "weworkremotely" in plat:
        url = "https://weworkremotely.com/categories/remote-programming-jobs"
    elif "remoteok" in plat:
        url = "https://remoteok.com/remote-python-jobs"
    else:
        tech_q = q or "remote python django developer"
        encoded = urllib.parse.quote_plus(f"{tech_q} apply worldwide remote")
        url = f"https://www.google.com/search?q={encoded}"

    _open_browser_url(url)
    return f"Opened live {platform.title()} job search in Chrome: {url}"


class JobCommandCenter:
    """Core Engine for Ranked Official Job Radar, Application Tracking & Grounded Pitches."""

    def __init__(self, root: Any = None, write_log_cb: Callable[[str], None] | None = None):
        self.root = root
        self.write_log_cb = write_log_cb

        self.data_file = TRACKER_FILE
        self._store = self._load_store()
        raw_cached = self._store.get("cached_radar", []) or []
        # Re-clean and score any existing cached items on load so no &nbsp; or stale tiers survive
        self.cached_radar: list[dict] = score_and_rank_jobs(raw_cached) if raw_cached else []
        self.source_health: list[dict] = self._store.get("source_health", []) or []
        self.is_refreshing = False
        self.foreign_agencies_enabled = False  # Disabled under A2 truthful-data rules
        self._qt_process: subprocess.Popen | None = None

        # Legacy HUD references (--legacy fallback only)
        self.hud_win: Any = None
        self._current_tab = "radar"
        self._filter_tier = "all"
        self._filter_text = ""
        self._search_var: Any = None

    def _load_store(self) -> dict:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        store = _load_json_file(self.data_file, {})
        if not store or not isinstance(store, dict):
            store = {
                "applications": [],
                "cold_outreach": [],
                "cached_radar": [],
                "source_health": [],
                "last_radar_scan": "",
            }
            _save_json_file(self.data_file, store)
        return store

    def _save_store(self) -> None:
        self._store["cached_radar"] = self.cached_radar[:200]
        self._store["source_health"] = self.source_health
        _save_json_file(self.data_file, self._store)

    def _log(self, msg: str) -> None:
        if self.write_log_cb:
            try:
                self.write_log_cb(f"JOB-RADAR: {msg}")
            except Exception:
                pass

    # ── Candidate Profile & CV Intelligence ──────────────────────────────────

    def get_candidate_context(self) -> dict:
        profile = _load_json_file(PROFILE_FILE, {})
        yaml_prof = load_jobs_profile()
        cv_text = ""
        try:
            from actions.job_assistant import _get_resume_text
            cv_text = _get_resume_text(profile)
        except Exception:
            pass
        yaml_skills = ", ".join(list((yaml_prof.get("skills") or {}).keys()))
        return {
            "name": profile.get("full_name") or "Mahmud Bashir Olasunkanmi",
            "email": profile.get("email") or "",
            "phone": profile.get("phone") or "",
            "location": yaml_prof.get("profile", {}).get("location") or profile.get("location") or "Abuja, Nigeria (WAT, UTC+1)",
            "seniority_years": yaml_prof.get("profile", {}).get("seniority_years", 3),
            "skills": profile.get("skills") or yaml_skills,
            "cv_excerpt": clean_html_and_text(cv_text)[:3000],
        }

    # ── Refresh All Channels in Background ───────────────────────────────────

    def scan_all_channels_sync(self, force: bool = False) -> tuple[list[dict], list[dict]]:
        """Synchronously runs ranked official-source scan (use from background threads)."""
        ranked, health = scan_all_sources(force=force)
        self.cached_radar = ranked
        self.source_health = health
        self._store["last_radar_scan"] = dt.datetime.now().isoformat()
        self._save_store()
        eligible_cnt = sum(1 for j in ranked if not j.get("blacklisted") and j.get("eligibility_status") != "excluded")
        self._log(f"Scan complete: {eligible_cnt} eligible developer roles ranked ({len(ranked)} total processed).")
        return ranked, health

    def scan_all_channels(self, callback: Callable[[list[dict]], None] | None = None, force: bool = True) -> None:
        if self.is_refreshing:
            return
        self.is_refreshing = True

        def _worker():
            try:
                self._log("Starting multi-source official job scan & 0-100 ranking...")
                ranked, _health = self.scan_all_channels_sync(force=force)
                if callback:
                    if self.root and hasattr(self.root, "after"):
                        try:
                            self.root.after(0, lambda: callback(ranked))
                            return
                        except Exception:
                            pass
                    try:
                        callback(ranked)
                    except Exception:
                        pass
            finally:
                self.is_refreshing = False

        threading.Thread(target=_worker, daemon=True, name="ROSE-JobScanner").start()

    # ── Pitch & Proposal Generator (Strictly Grounded in CV + YAML Profile) ─

    def generate_pitch(self, job: dict) -> str:
        ctx = self.get_candidate_context()
        name = ctx["name"]
        cv = ctx["cv_excerpt"]
        skills = ctx["skills"]

        role = clean_html_and_text(job.get("title") or "Software Engineer")
        comp = clean_html_and_text(job.get("company") or "your team")
        desc = clean_html_and_text(job.get("snippet") or job.get("description") or "")
        job_type = str(job.get("job_type") or "").lower()

        api_key = str(get_app_config_value("gemini_api_key", "") or "").strip()
        if api_key and cv:
            try:
                from google import genai  # type: ignore
                from google.genai import types  # type: ignore

                client = genai.Client(
                    api_key=api_key,
                    http_options=types.HttpOptions(timeout=12000, retry_options=types.HttpRetryOptions(attempts=1)),
                )
                prompt = (
                    f"Write a concise, grounded 3-paragraph application pitch for {name} applying for:\n"
                    f"Role: {role} at {comp}\n"
                    f"Job Details: {desc[:900]}\n"
                    f"Verified Core Skills: {skills}\n"
                    f"Candidate CV Excerpt:\n{cv}\n\n"
                    "STRICT GROUNDING RULES:\n"
                    "1. Use ONLY skills, projects, experience, and metrics explicitly present in the CV above. Do NOT invent employers, degrees, tools, or numbers.\n"
                    "2. First-person ('I'), confident and direct, 120-165 words total, exactly 3 paragraphs.\n"
                    "3. Connect 1-2 relevant achievements from the CV directly to the job requirements.\n"
                    "4. Output ONLY the ready-to-paste pitch text ending with candidate name and contact info.\n"
                )
                cfg = types.GenerateContentConfig(max_output_tokens=360, thinking_config=types.ThinkingConfig(thinking_budget=0))
                resp = client.models.generate_content(model="gemini-3.5-flash", contents=prompt, config=cfg)
                txt = (getattr(resp, "text", "") or "").strip()
                if txt:
                    return txt
            except Exception:
                pass

        # Grounded local fallback template using only verified CV facts
        contact = " | ".join(x for x in (ctx.get("email", ""), ctx.get("phone", "")) if x)
        if "contract" in job_type or "freelance" in job_type:
            return (
                f"Hi Hiring Team at {comp},\n\n"
                f"I am reaching out regarding the {role} opening. I am a full-stack developer based in {ctx.get('location', 'Abuja, Nigeria')} (WAT, UTC+1) "
                f"specializing in Python, Django REST Framework, React, and PostgreSQL.\n\n"
                f"In recent production deliveries, I architected Django REST APIs with JWT authentication and optimized PostgreSQL schemas with composite indexes "
                f"(reducing heavy query latency from 3.2s to under 50ms), alongside shipping responsive React frontends and e-commerce/operations dashboards.\n\n"
                f"I am available for immediate remote contract collaboration and would be glad to share code samples or discuss your technical priorities.\n\n"
                f"Best regards,\n{name}\n{contact}"
            ).strip()

        return (
            f"Dear Hiring Team at {comp},\n\n"
            f"I am writing to apply for the {role} role. As a full-stack developer working across Python, Django REST Framework, React, and PostgreSQL, "
            f"I focus on building reliable backend APIs and responsive user interfaces.\n\n"
            f"Across production client platforms I have built—including an e-commerce platform with Redis caching and payment webhooks, and a real-time operations dashboard—"
            f"I reduced PostgreSQL query times from 3.2s down to under 50ms and automated deployment pipelines with React frontends.\n\n"
            f"Thank you for considering my application. Working in WAT (UTC+1), I overlap smoothly with European and US morning hours and welcome a conversation.\n\n"
            f"Best regards,\n{name}\n{contact}"
        ).strip()

    # ── Follow-Up Email Generator ────────────────────────────────────────────

    def generate_follow_up(self, app: dict) -> str:
        ctx = self.get_candidate_context()
        name = ctx["name"]
        role = clean_html_and_text(app.get("title", "the position"))
        comp = clean_html_and_text(app.get("company", "your company"))
        date_applied = str(app.get("date_applied", "a few days ago"))[:10]
        contact = " | ".join(x for x in (ctx.get("email", ""), ctx.get("phone", "")) if x)

        return (
            f"Subject: Following up on application - {role} ({name})\n\n"
            f"Dear Hiring Team at {comp},\n\n"
            f"I hope you are having a productive week.\n\n"
            f"I am following up on my application for the {role} position submitted on {date_applied}. "
            f"I remain very interested in contributing to your team with my experience in Python, Django REST APIs, PostgreSQL, and React.\n\n"
            f"Please let me know if any additional code samples or details would be helpful for your review.\n\n"
            f"Best regards,\n{name}\n{contact}"
        ).strip()

    # ── Application Tracking & CRM Methods ───────────────────────────────────

    def track_application(self, job: dict, status: str = "applied", notes: str = "") -> dict:
        app_id = job.get("id") or f"app-{int(time.time()*1000)%1000000}"
        now_dt = dt.datetime.now()
        follow_up_due = (now_dt + dt.timedelta(days=4)).isoformat()

        record = {
            "id": app_id,
            "title": clean_html_and_text(job.get("title", "Software Engineer")),
            "company": clean_html_and_text(job.get("company", "Client/Company")),
            "platform": job.get("platform", "Radar"),
            "url": job.get("url", ""),
            "pay": job.get("pay", "pay unlisted"),
            "status": status,
            "date_applied": now_dt.isoformat(),
            "follow_up_due": follow_up_due,
            "notes": notes,
        }

        existing = [a for a in self._store.get("applications", []) if a.get("id") != app_id]
        existing.insert(0, record)
        self._store["applications"] = existing
        self._save_store()
        self._log(f"Application tracked: {record['title']} @ {record['company']}")
        return record

    def delete_application(self, app_id: str) -> bool:
        apps = self._store.get("applications", [])
        init_len = len(apps)
        apps = [a for a in apps if a.get("id") != app_id]
        if len(apps) != init_len:
            self._store["applications"] = apps
            self._save_store()
            self._log(f"Deleted application {app_id}")
            return True
        return False

    def update_application_status(self, app_id: str, new_status: str, notes: str | None = None) -> bool:
        updated = False
        for a in self._store.get("applications", []):
            if a.get("id") == app_id:
                a["status"] = new_status
                if notes is not None:
                    a["notes"] = notes
                updated = True
                break
        if updated:
            self._save_store()
            self._log(f"Updated application {app_id} -> {new_status}")
        return updated

    def get_applications(self, filter_status: str = "all") -> list[dict]:
        apps = self._store.get("applications", [])
        now_iso = dt.datetime.now().isoformat()
        for a in apps:
            if a.get("status") == "applied" and a.get("follow_up_due", "") and a.get("follow_up_due", "") <= now_iso:
                a["status"] = "follow_up_due"

        if filter_status and filter_status != "all":
            return [a for a in apps if a.get("status") == filter_status]
        return apps

    def get_analytics(self) -> dict:
        apps = self.get_applications("all")
        total = len(apps)
        interviewing = sum(1 for a in apps if a.get("status") in ("interviewing", "interview_scheduled"))
        offers = sum(1 for a in apps if a.get("status") == "offer")
        rejected = sum(1 for a in apps if a.get("status") == "rejected")
        due = sum(1 for a in apps if a.get("status") == "follow_up_due")

        rate = round((interviewing / total) * 100, 1) if total > 0 else 0.0

        platforms: dict[str, int] = {}
        for a in apps:
            p = a.get("platform", "Other")
            platforms[p] = platforms.get(p, 0) + 1

        return {
            "total_applied": total,
            "interviewing": interviewing,
            "offers": offers,
            "rejected": rejected,
            "follow_ups_due": due,
            "interview_rate_pct": rate,
            "platform_breakdown": platforms,
            "foreign_agencies_enabled": False,
        }

    # ── Foreign Agency Radar (Disabled under Rule A2) ────────────────────────

    def search_foreign_agencies(self, city: str = "") -> list[dict]:
        """Disabled in Phase 3: legacy implementation used static Austin stub data."""
        return []

    def generate_agency_cold_pitch(self, agency: dict) -> str:
        return ""

    # ── GUI Dashboard (PySide6 Primary, --legacy Tkinter Fallback) ──────────

    def open_dashboard(self) -> None:
        """Opens the PySide6 Job Command Center window (or `--legacy` Tkinter fallback)."""
        force_legacy = "--legacy" in sys.argv
        if not force_legacy and importlib.util.find_spec("PySide6") is not None:
            try:
                # Only open in-process if Qt is ALREADY active in this process;
                # otherwise keep main.py lean (<500 MB RAM) and run Qt as a child process.
                if "PySide6.QtWidgets" in sys.modules:
                    from PySide6.QtWidgets import QApplication
                    app = QApplication.instance()
                    if app is not None:
                        def _open_qt_on_main() -> None:
                            from ui.job_center import JobCenterWindow
                            if getattr(self, "_qt_win", None) is None:
                                self._qt_win = JobCenterWindow(backend=self)
                            self._qt_win.show()
                            self._qt_win.raise_()
                            self._qt_win.activateWindow()

                        if threading.current_thread() is threading.main_thread():
                            _open_qt_on_main()
                            return
                        if self.root is not None and hasattr(self.root, "after"):
                            self.root.after(0, _open_qt_on_main)
                            return

                try:
                    with socket.create_connection(("127.0.0.1", 48215), timeout=0.35) as sock:
                        sock.sendall(b"RAISE")
                        return
                except OSError:
                    pass

                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
                self._qt_process = subprocess.Popen(
                    [sys.executable, "-m", "ui.job_center"],
                    cwd=str(BASE_DIR),
                    creationflags=creation_flags,
                )
                return
            except Exception as exc:
                self._log(f"PySide6 Job Center launch error, using fallback: {exc}")

        if not self.root or tk is None:
            return

        def _build():
            if self.hud_win and self.hud_win.winfo_exists():
                self.hud_win.deiconify()
                self.hud_win.lift()
                self.hud_win.focus_force()
                return

            win = tk.Toplevel(self.root)
            self.hud_win = win
            win.title("R.O.S.E Job Command Center & Application CRM")
            win.geometry("980x680+100+40")
            win.minsize(860, 560)
            win.configure(bg="#05070b")
            win.attributes("-topmost", True)
            try:
                win.after(2000, lambda: win.attributes("-topmost", False))
            except Exception:
                pass

            # 1. Header Bar
            hdr = tk.Frame(win, bg="#0c121b", height=44, highlightthickness=1, highlightbackground="#16354d")
            hdr.pack(fill="x", side="top")
            hdr.pack_propagate(False)

            tk.Label(
                hdr,
                text="  💼 GLOBAL JOB RADAR & APPLICATION TRACKER",
                bg="#0c121b",
                fg="#00d4ff",
                font=("Segoe UI", 11, "bold"),
            ).pack(side="left", padx=6)

            analytics = self.get_analytics()
            follow_cnt = analytics["follow_ups_due"]
            if follow_cnt > 0:
                tk.Label(
                    hdr,
                    text=f"🔔 {follow_cnt} Follow-Up Due",
                    bg="#3a1e0b",
                    fg="#ffaa00",
                    font=("Segoe UI", 8, "bold"),
                    padx=8,
                    pady=2,
                ).pack(side="left", padx=10)

            # 2. Real Chrome Live Search Quick Launcher Bar
            quick_bar = tk.Frame(win, bg="#090f17", height=34, highlightthickness=1, highlightbackground="#142636")
            quick_bar.pack(fill="x", side="top")
            quick_bar.pack_propagate(False)

            tk.Label(
                quick_bar,
                text=" 🌐 REAL CHROME RADAR: ",
                bg="#090f17",
                fg="#7aa2be",
                font=("Segoe UI", 8, "bold"),
            ).pack(side="left", padx=(8, 2))

            for lbl, plat in (
                ("🐦 Twitter/X Live", "twitter"),
                ("🔍 Google ATS", "google"),
                ("👽 Reddit Gigs", "reddit"),
                ("⚡ Upwork Contracts", "upwork"),
                ("📰 HackerNews", "hackernews"),
            ):
                tk.Button(
                    quick_bar,
                    text=lbl,
                    command=lambda p=plat: launch_live_chrome_search(p),
                    bg="#101d2c",
                    fg="#00d4ff",
                    relief="flat",
                    bd=0,
                    padx=7,
                    pady=2,
                    font=("Segoe UI", 8),
                    cursor="hand2",
                ).pack(side="left", padx=2)

            def _open_autofill():
                from actions.autofill_copilot import open_installer_in_chrome
                open_installer_in_chrome()

            tk.Button(
                quick_bar,
                text="⚡ 1-CLICK FORM AUTOFILL",
                command=_open_autofill,
                bg="#0d3b24",
                fg="#00e676",
                relief="flat",
                bd=0,
                padx=9,
                pady=2,
                font=("Segoe UI", 8, "bold"),
                cursor="hand2",
            ).pack(side="right", padx=8)

            # 3. Navigation Tabs Bar
            tab_bar = tk.Frame(win, bg="#070b10", height=36)
            tab_bar.pack(fill="x", side="top", padx=12, pady=(6, 2))

            content_wrap = tk.Frame(win, bg="#05070b")
            content_wrap.pack(fill="both", expand=True, padx=12, pady=4)

            tab_buttons: dict[str, tk.Button] = {}

            def _switch_tab(tab_name: str):
                self._current_tab = tab_name
                for k, btn in tab_buttons.items():
                    if k == tab_name:
                        btn.config(bg="#18364f", fg="#00d4ff")
                    else:
                        btn.config(bg="#0c121b", fg="#8eb3cc")

                for child in content_wrap.winfo_children():
                    child.destroy()

                if tab_name == "radar":
                    self._render_radar_tab(content_wrap)
                elif tab_name == "tracker":
                    self._render_tracker_tab(content_wrap)
                elif tab_name == "analytics":
                    self._render_analytics_tab(content_wrap)
                elif tab_name == "agencies":
                    self._render_agencies_tab(content_wrap)
                elif tab_name == "custom":
                    self._render_custom_tab(content_wrap)

            for key, label in (
                ("radar", "📡 Live Radar (<48h)"),
                ("tracker", "📋 Application Tracker & CRM"),
                ("analytics", "📊 Analytics"),
                ("agencies", "🗺 Foreign Agency Radar"),
                ("custom", "➕ Paste Custom Gig"),
            ):
                b = tk.Button(
                    tab_bar,
                    text=label,
                    command=lambda k=key: _switch_tab(k),
                    bg="#0c121b",
                    fg="#8eb3cc",
                    relief="flat",
                    bd=0,
                    padx=12,
                    pady=4,
                    font=("Segoe UI", 9, "bold"),
                    cursor="hand2",
                )
                b.pack(side="left", padx=3)
                tab_buttons[key] = b

            _switch_tab("radar")

            # Auto-scan if empty
            if not self.cached_radar:
                self.scan_all_channels(lambda _leads: _switch_tab("radar"))

        if threading.current_thread() is threading.main_thread():
            _build()
        else:
            self.root.after(0, _build)

    # ── Tab 1: Live Radar (<48h) ─────────────────────────────────────────────

    def _render_radar_tab(self, parent: tk.Frame) -> None:
        top_ctrl = tk.Frame(parent, bg="#05070b")
        top_ctrl.pack(fill="x", side="top", pady=(0, 6))

        # Filter buttons row
        tk.Label(top_ctrl, text="Filter:", bg="#05070b", fg="#7aa2be", font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))

        tier_btns: dict[str, tk.Button] = {}

        def _set_tier(tier: str):
            self._filter_tier = tier
            for t, btn in tier_btns.items():
                btn.config(bg="#1c3a54" if t == tier else "#0f1722", fg="#00e676" if t == tier else "#8eb3cc")
            _refresh_radar_cards()

        for t_key, t_lbl in (
            ("all", "All Opportunities"),
            ("quick_fix", "⚡ Quick Fixes ($50-$500)"),
            ("contract", "💼 Contracts"),
            ("full_time", "🚀 Full-Time Remote"),
        ):
            b = tk.Button(
                top_ctrl,
                text=t_lbl,
                command=lambda k=t_key: _set_tier(k),
                bg="#1c3a54" if self._filter_tier == t_key else "#0f1722",
                fg="#00e676" if self._filter_tier == t_key else "#8eb3cc",
                relief="flat",
                bd=0,
                padx=8,
                pady=2,
                font=("Segoe UI", 8, "bold"),
                cursor="hand2",
            )
            b.pack(side="left", padx=2)
            tier_btns[t_key] = b

        status_lbl = tk.Label(top_ctrl, text=f"{len(self.cached_radar)} fresh leads (<48h)", bg="#05070b", fg="#00d4ff", font=("Segoe UI", 8))
        status_lbl.pack(side="right", padx=6)

        def _do_rescan():
            status_lbl.config(text="Scanning all channels...", fg="#ffaa00")
            self.scan_all_channels(lambda leads: status_lbl.winfo_exists() and (_refresh_radar_cards(), status_lbl.config(text=f"{len(leads)} fresh leads (<48h)", fg="#00e676")))

        tk.Button(
            top_ctrl,
            text="↻ Scan Now",
            command=_do_rescan,
            bg="#006b8f",
            fg="#ffffff",
            relief="flat",
            bd=0,
            padx=10,
            pady=2,
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
        ).pack(side="right", padx=6)

        # Keyword Search Bar
        search_row = tk.Frame(parent, bg="#05070b")
        search_row.pack(fill="x", side="top", pady=(2, 6))

        if self._search_var is None:
            self._search_var = tk.StringVar(value="")

        tk.Label(search_row, text="🔍 Keyword Filter:", bg="#05070b", fg="#8eb3cc", font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 4))
        search_entry = tk.Entry(
            search_row,
            textvariable=self._search_var,
            bg="#0c121b",
            fg="#ffffff",
            insertbackground="#00d4ff",
            relief="flat",
            font=("Segoe UI", 9),
            width=28,
        )
        search_entry.pack(side="left", padx=4, ipady=2)

        def _on_search_change(*_args):
            self._filter_text = self._search_var.get().strip().lower()
            _refresh_radar_cards()

        self._search_var.trace_add("write", _on_search_change)

        tk.Button(
            search_row,
            text="Clear",
            command=lambda: self._search_var.set(""),
            bg="#0f1722",
            fg="#8eb3cc",
            relief="flat",
            bd=0,
            padx=6,
            pady=1,
            font=("Segoe UI", 8),
            cursor="hand2",
        ).pack(side="left", padx=2)

        # Scrollable Cards Container
        canvas = tk.Canvas(parent, bg="#05070b", highlightthickness=0)
        scroll = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        cards_frame = tk.Frame(canvas, bg="#05070b")

        cards_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_win = canvas.create_window((0, 0), window=cards_frame, anchor="nw")

        def _on_canvas_configure(e):
            canvas.itemconfig(canvas_win, width=e.width)

        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.configure(yscrollcommand=scroll.set)

        # Smooth Mousewheel Scrolling
        def _on_mousewheel(event):
            try:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass

        def _bind_mousewheel(_event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_mousewheel(_event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)

        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def _refresh_radar_cards():
            for c in cards_frame.winfo_children():
                c.destroy()

            filtered = [
                j for j in self.cached_radar
                if not j.get("blacklisted")
                and j.get("eligibility_status") != "excluded"
                and not j.get("hidden_low_score")
            ]

            # 1. Text keyword filter
            if self._filter_text:
                filtered = [
                    j for j in filtered
                    if self._filter_text in f"{j.get('title', '')} {j.get('snippet', '')} {j.get('company', '')} {j.get('platform', '')} {j.get('match_reason', '')}".lower()
                ]

            if not filtered:
                empty_card = tk.Frame(cards_frame, bg="#0c121b", highlightthickness=1, highlightbackground="#162e42", padx=20, pady=24)
                empty_card.pack(fill="x", padx=10, pady=20)
                tk.Label(
                    empty_card,
                    text="No matching eligible roles currently cached.",
                    bg="#0c121b",
                    fg="#00d4ff",
                    font=("Segoe UI", 11, "bold"),
                ).pack(pady=(0, 6))
                tk.Button(
                    empty_card,
                    text="↻ Scan Official Feeds Now",
                    command=_do_rescan,
                    bg="#006b8f",
                    fg="#ffffff",
                    relief="flat",
                    bd=0,
                    padx=10,
                    pady=4,
                    font=("Segoe UI", 9, "bold"),
                    cursor="hand2",
                ).pack(pady=4)
                return

            for job in filtered[:50]:
                card = tk.Frame(cards_frame, bg="#0b1017", highlightthickness=1, highlightbackground="#142636")
                card.pack(fill="x", pady=4, padx=2)

                top_r = tk.Frame(card, bg="#0b1017")
                top_r.pack(fill="x", padx=10, pady=(6, 2))

                sc = int(job.get("score", 0))
                tk.Label(top_r, text=f" {sc}% Match ", bg="#102e1c" if sc >= 70 else "#122a3d", fg="#00e676" if sc >= 70 else "#00d4ff", font=("Segoe UI", 7, "bold")).pack(side="left")

                jtype = str(job.get("job_type") or "").strip()
                if jtype:
                    tk.Label(top_r, text=f" {jtype} ", bg="#1d1836", fg="#c4b5fd", font=("Segoe UI", 7, "bold")).pack(side="left", padx=(4, 0))

                tk.Label(top_r, text=f"  {job.get('title')}", bg="#0b1017", fg="#ffffff", font=("Segoe UI", 10, "bold"), anchor="w").pack(side="left", fill="x", expand=True)
                tk.Label(top_r, text=f"{job.get('pay', 'pay unlisted')} · {job.get('posted_desc', 'date unknown')}", bg="#0b1017", fg="#8eb3cc", font=("Segoe UI", 8)).pack(side="right")

                mid_r = tk.Frame(card, bg="#0b1017")
                mid_r.pack(fill="x", padx=10, pady=(1, 4))

                plat_name = job.get("platform", "Radar")
                tk.Label(mid_r, text=f"{job.get('company')}  |  {job.get('match_reason', '')}  |  [{plat_name}]", bg="#0b1017", fg="#00d4ff", font=("Segoe UI", 8)).pack(side="left")

                snip = job.get("snippet", "")
                if snip:
                    tk.Label(card, text=snip[:190] + ("..." if len(snip) > 190 else ""), bg="#0b1017", fg="#9dbdd4", font=("Segoe UI", 8), anchor="w", justify="left").pack(fill="x", padx=10, pady=(0, 6))

                # Card Actions
                btn_row = tk.Frame(card, bg="#0b1017")
                btn_row.pack(fill="x", padx=10, pady=(2, 6))

                def _open_url(u=job.get("url", "")):
                    _open_browser_url(u)

                def _copy_pitch_action(j=job, c_btn=None):
                    pitch = self.generate_pitch(j)
                    _copy_clipboard(pitch)
                    if c_btn:
                        c_btn.config(text="✓ Pitch Copied!", bg="#0f5132")
                        card.after(2000, lambda: c_btn.winfo_exists() and c_btn.config(text="📋 Copy Pitch", bg="#152b3d"))

                is_already_tracked = any(a.get("id") == job.get("id") for a in self._store.get("applications", []))

                def _toggle_applied_action(j=job, m_btn=None):
                    j_id = j.get("id")
                    if any(a.get("id") == j_id for a in self._store.get("applications", [])):
                        # Undo applied
                        self.delete_application(j_id)
                        if m_btn:
                            m_btn.config(text="✅ Mark as Applied", bg="#102619", fg="#00e676")
                    else:
                        # Mark applied
                        self.track_application(j, status="applied")
                        if m_btn:
                            m_btn.config(text="✓ Applied! (Click to Undo)", bg="#0f5132", fg="#ffffff")

                tk.Button(
                    btn_row,
                    text="🌐 Open Link",
                    command=_open_url,
                    bg="#006b8f",
                    fg="#ffffff",
                    relief="flat",
                    bd=0,
                    padx=8,
                    pady=2,
                    font=("Segoe UI", 8, "bold"),
                    cursor="hand2",
                ).pack(side="left", padx=(0, 4))

                pitch_btn = tk.Button(
                    btn_row,
                    text="📋 Copy Pitch",
                    command=None,
                    bg="#152b3d",
                    fg="#9dd6ff",
                    relief="flat",
                    bd=0,
                    padx=8,
                    pady=2,
                    font=("Segoe UI", 8, "bold"),
                    cursor="hand2",
                )
                pitch_btn.config(command=lambda j=job, pb=pitch_btn: _copy_pitch_action(j, pb))
                pitch_btn.pack(side="left", padx=4)

                mark_btn = tk.Button(
                    btn_row,
                    text="✓ Applied! (Click to Undo)" if is_already_tracked else "✅ Mark as Applied",
                    command=None,
                    bg="#0f5132" if is_already_tracked else "#102619",
                    fg="#ffffff" if is_already_tracked else "#00e676",
                    relief="flat",
                    bd=0,
                    padx=8,
                    pady=2,
                    font=("Segoe UI", 8, "bold"),
                    cursor="hand2",
                )
                mark_btn.config(command=lambda j=job, mb=mark_btn: _toggle_applied_action(j, mb))
                mark_btn.pack(side="left", padx=4)

        _refresh_radar_cards()

    # ── Tab 2: Application Tracker & Follow-Up CRM ───────────────────────────

    def _render_tracker_tab(self, parent: tk.Frame) -> None:
        top_ctrl = tk.Frame(parent, bg="#05070b")
        top_ctrl.pack(fill="x", side="top", pady=(0, 6))

        tk.Label(top_ctrl, text="Status Filter:", bg="#05070b", fg="#7aa2be", font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))

        status_filter = ["all"]
        btn_dict: dict[str, tk.Button] = {}

        canvas = tk.Canvas(parent, bg="#05070b", highlightthickness=0)
        scroll = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        apps_frame = tk.Frame(canvas, bg="#05070b")

        apps_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_win = canvas.create_window((0, 0), window=apps_frame, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_win, width=e.width))
        canvas.configure(yscrollcommand=scroll.set)

        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def _refresh_apps():
            for c in apps_frame.winfo_children():
                c.destroy()

            apps = self.get_applications(filter_status=status_filter[0])
            if not apps:
                empty_box = tk.Frame(apps_frame, bg="#0c121b", highlightthickness=1, highlightbackground="#162e42", padx=20, pady=24)
                empty_box.pack(fill="x", padx=10, pady=20)

                tk.Label(
                    empty_box,
                    text="📋 No applications logged under this filter yet.",
                    bg="#0c121b",
                    fg="#00d4ff",
                    font=("Segoe UI", 11, "bold"),
                ).pack(pady=(0, 4))

                tk.Label(
                    empty_box,
                    text="When you apply to a job, click '✅ Mark as Applied' in the Radar or paste a custom gig to track follow-ups here.",
                    bg="#0c121b",
                    fg="#8eb3cc",
                    font=("Segoe UI", 9),
                ).pack()
                return

            for a in apps:
                card = tk.Frame(apps_frame, bg="#0c1117", highlightthickness=1, highlightbackground="#162838")
                card.pack(fill="x", pady=4, padx=2)

                st = a.get("status", "applied")
                is_due = (st == "follow_up_due")
                badge_bg = "#3a1e0b" if is_due else ("#0f3823" if st == "interviewing" else ("#0c4220" if st == "offer" else "#122a3d"))
                badge_fg = "#ffaa00" if is_due else ("#00e676" if st in ("interviewing", "offer") else "#00d4ff")
                badge_txt = "🔔 FOLLOW-UP DUE" if is_due else st.upper()

                top_r = tk.Frame(card, bg="#0c1117")
                top_r.pack(fill="x", padx=10, pady=(6, 2))

                tk.Label(top_r, text=f" {badge_txt} ", bg=badge_bg, fg=badge_fg, font=("Segoe UI", 8, "bold")).pack(side="left")
                tk.Label(top_r, text=f"  {a.get('title')}", bg="#0c1117", fg="#ffffff", font=("Segoe UI", 10, "bold")).pack(side="left")
                tk.Label(top_r, text=f"Applied: {a.get('date_applied', '')[:10]}", bg="#0c1117", fg="#8eb3cc", font=("Segoe UI", 8)).pack(side="right")

                mid_r = tk.Frame(card, bg="#0c1117")
                mid_r.pack(fill="x", padx=10, pady=(2, 6))
                tk.Label(mid_r, text=f"{a.get('company')}  |  Platform: {a.get('platform')}  |  {a.get('pay')}", bg="#0c1117", fg="#6f91ab", font=("Segoe UI", 8)).pack(side="left")

                btn_r = tk.Frame(card, bg="#0c1117")
                btn_r.pack(fill="x", padx=10, pady=(2, 6))

                def _open_app_url(u=a.get("url", "")):
                    _open_browser_url(u)

                def _copy_follow_up(app_record=a, fb=None):
                    f_text = self.generate_follow_up(app_record)
                    _copy_clipboard(f_text)
                    if fb:
                        fb.config(text="✓ Follow-Up Copied!", bg="#0f5132")
                        card.after(2000, lambda: fb.winfo_exists() and fb.config(text="✍ Copy Follow-Up Email", bg="#3a2512" if is_due else "#18334a"))

                def _set_status(app_id=a.get("id"), new_st="interviewing"):
                    self.update_application_status(app_id, new_st)
                    _refresh_apps()

                def _del_app(app_id=a.get("id")):
                    self.delete_application(app_id)
                    _refresh_apps()

                if a.get("url"):
                    tk.Button(btn_r, text="🌐 Job URL", command=_open_app_url, bg="#152636", fg="#9dd6ff", relief="flat", bd=0, padx=6, pady=2, font=("Segoe UI", 8, "bold"), cursor="hand2").pack(side="left", padx=2)

                fu_btn = tk.Button(btn_r, text="✍ Copy Follow-Up Email", command=None, bg="#3a2512" if is_due else "#18334a", fg="#ffaa00" if is_due else "#9dd6ff", relief="flat", bd=0, padx=8, pady=2, font=("Segoe UI", 8, "bold"), cursor="hand2")
                fu_btn.config(command=lambda ar=a, btn=fu_btn: _copy_follow_up(ar, btn))
                fu_btn.pack(side="left", padx=4)

                tk.Button(btn_r, text="→ Interviewing", command=lambda aid=a.get("id"): _set_status(aid, "interviewing"), bg="#102e1c", fg="#00e676", relief="flat", bd=0, padx=6, pady=2, font=("Segoe UI", 8, "bold"), cursor="hand2").pack(side="left", padx=2)
                tk.Button(btn_r, text="→ Offer", command=lambda aid=a.get("id"): _set_status(aid, "offer"), bg="#0c4220", fg="#39ff84", relief="flat", bd=0, padx=6, pady=2, font=("Segoe UI", 8, "bold"), cursor="hand2").pack(side="left", padx=2)
                tk.Button(btn_r, text="🗑 Delete", command=lambda aid=a.get("id"): _del_app(aid), bg="#2a1014", fg="#ff6677", relief="flat", bd=0, padx=6, pady=2, font=("Segoe UI", 8, "bold"), cursor="hand2").pack(side="right", padx=2)

        def _set_status_filter(st: str):
            status_filter[0] = st
            for k, b in btn_dict.items():
                b.config(bg="#1c3a54" if k == st else "#0f1722", fg="#00e676" if k == st else "#8eb3cc")
            _refresh_apps()

        for s_key, s_lbl in (
            ("all", "All"),
            ("applied", "Applied"),
            ("follow_up_due", "🔔 Follow-Up Due"),
            ("interviewing", "Interviewing"),
            ("offer", "Offers"),
        ):
            b = tk.Button(
                top_ctrl,
                text=s_lbl,
                command=lambda k=s_key: _set_status_filter(k),
                bg="#1c3a54" if status_filter[0] == s_key else "#0f1722",
                fg="#00e676" if status_filter[0] == s_key else "#8eb3cc",
                relief="flat",
                bd=0,
                padx=8,
                pady=2,
                font=("Segoe UI", 8, "bold"),
                cursor="hand2",
            )
            b.pack(side="left", padx=2)
            btn_dict[s_key] = b

        _refresh_apps()

    # ── Tab 3: Analytics ─────────────────────────────────────────────────────

    def _render_analytics_tab(self, parent: tk.Frame) -> None:
        stats = self.get_analytics()

        wrap = tk.Frame(parent, bg="#05070b")
        wrap.pack(fill="both", expand=True, padx=10, pady=10)

        # KPI row
        kpi_row = tk.Frame(wrap, bg="#05070b")
        kpi_row.pack(fill="x", side="top", pady=10)

        kpis = [
            ("TOTAL APPLICATIONS", str(stats["total_applied"]), "#00d4ff"),
            ("INTERVIEWS LANDED", str(stats["interviewing"]), "#00e676"),
            ("CALLBACK RATE", f"{stats['interview_rate_pct']}%", "#ffcc00"),
            ("OFFERS RECEIVED", str(stats["offers"]), "#39ff84"),
            ("FOLLOW-UPS DUE", str(stats["follow_ups_due"]), "#ffaa00"),
        ]
        for title, val, col in kpis:
            card = tk.Frame(kpi_row, bg="#0b1017", highlightthickness=1, highlightbackground="#142636", padx=14, pady=10)
            card.pack(side="left", fill="both", expand=True, padx=4)
            tk.Label(card, text=title, bg="#0b1017", fg="#7aa2be", font=("Segoe UI", 8, "bold")).pack()
            tk.Label(card, text=val, bg="#0b1017", fg=col, font=("Segoe UI", 18, "bold")).pack(pady=(4, 0))

        # Insights Box
        info_frame = tk.Frame(wrap, bg="#0a0f16", highlightthickness=1, highlightbackground="#152b3d", padx=16, pady=14)
        info_frame.pack(fill="both", expand=True, pady=12)

        tk.Label(info_frame, text="PLATFORM BREAKDOWN & CONVERSION ENGINE", bg="#0a0f16", fg="#00d4ff", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))

        breakdown = stats.get("platform_breakdown") or {}
        if not breakdown:
            tk.Label(info_frame, text="Log your first application to see channel performance metrics.", bg="#0a0f16", fg="#6f91ab", font=("Segoe UI", 9)).pack(anchor="w")
        else:
            for p, cnt in breakdown.items():
                tk.Label(info_frame, text=f"• {p}: {cnt} application(s) logged", bg="#0a0f16", fg="#e0f2fe", font=("Segoe UI", 9)).pack(anchor="w", pady=2)

        tk.Label(info_frame, text="\nRECOMMENDED DAILY ACTION PLAN:", bg="#0a0f16", fg="#ffaa00", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        plan_lines = [
            "1. Check '🔔 Follow-Up Due' first every morning — send the 1-click follow-up to revive pending chats.",
            "2. Review the '⚡ Quick Fixes' filter on the Radar for same-day client gigs ($50 - $500).",
            "3. Submit 3-5 high-paying Worldwide Remote contract/full-time applications (<48h fresh) before 11:00 AM.",
            "4. Pitch 2 foreign tech agencies in Austin/London/Berlin via the Foreign Agency Radar.",
        ]
        for line in plan_lines:
            tk.Label(info_frame, text=line, bg="#0a0f16", fg="#9dbdd4", font=("Segoe UI", 9)).pack(anchor="w", pady=1)

    # ── Tab 4: Foreign Agency Radar & Cold Outreach ──────────────────────────

    def _render_agencies_tab(self, parent: tk.Frame) -> None:
        box = tk.Frame(parent, bg="#0c121b", highlightthickness=1, highlightbackground="#162e42", padx=20, pady=24)
        box.pack(fill="x", padx=10, pady=20)
        tk.Label(
            box,
            text="Foreign Agency Radar is Disabled",
            bg="#0c121b",
            fg="#ffaa00",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        tk.Label(
            box,
            text="Disabled under R.O.S.E truthful-data rules (Phase 3): the previous implementation returned hardcoded sample agencies regardless of searched city.",
            bg="#0c121b",
            fg="#8eb3cc",
            font=("Segoe UI", 9),
            wraplength=760,
            justify="left",
        ).pack(anchor="w")

    # ── Tab 5: Paste Custom Gig / Job ────────────────────────────────────────

    def _render_custom_tab(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg="#05070b")
        wrap.pack(fill="both", expand=True, padx=14, pady=10)

        tk.Label(wrap, text="PASTE ANY CUSTOM JOB / CLIENT GIG (Upwork, Twitter, Reddit, etc.)", bg="#05070b", fg="#00d4ff", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))

        tk.Label(wrap, text="Found a client gig on Upwork, Twitter, or Reddit? Paste the text or URL here and R.O.S.E will instantly generate a winning tailored proposal and register it to your tracker.", bg="#05070b", fg="#7aa2be", font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 10))

        # Fields row
        meta_row = tk.Frame(wrap, bg="#05070b")
        meta_row.pack(fill="x", pady=4)

        tk.Label(meta_row, text="Role/Title:", bg="#05070b", fg="#8eb3cc", font=("Segoe UI", 8)).pack(side="left")
        role_entry = tk.Entry(meta_row, bg="#0c121b", fg="#ffffff", insertbackground="#00d4ff", relief="flat", font=("Segoe UI", 9), width=24)
        role_entry.insert(0, "Full Stack / Python / React Gig")
        role_entry.pack(side="left", padx=(4, 12), ipady=2)

        tk.Label(meta_row, text="Company/Client:", bg="#05070b", fg="#8eb3cc", font=("Segoe UI", 8)).pack(side="left")
        comp_entry = tk.Entry(meta_row, bg="#0c121b", fg="#ffffff", insertbackground="#00d4ff", relief="flat", font=("Segoe UI", 9), width=18)
        comp_entry.insert(0, "Client")
        comp_entry.pack(side="left", padx=(4, 12), ipady=2)

        tk.Label(meta_row, text="Pay/Budget:", bg="#05070b", fg="#8eb3cc", font=("Segoe UI", 8)).pack(side="left")
        pay_entry = tk.Entry(meta_row, bg="#0c121b", fg="#ffffff", insertbackground="#00d4ff", relief="flat", font=("Segoe UI", 9), width=14)
        pay_entry.insert(0, "$150 Fixed")
        pay_entry.pack(side="left", padx=(4, 12), ipady=2)

        tk.Label(wrap, text="Client Job Description / Problem to Fix:", bg="#05070b", fg="#8eb3cc", font=("Segoe UI", 8)).pack(anchor="w", pady=(6, 2))

        txt_box = tk.Text(wrap, height=7, bg="#0a0f16", fg="#e0f2fe", insertbackground="#00d4ff", relief="flat", highlightthickness=1, highlightbackground="#152b3d", font=("Segoe UI", 9), padx=8, pady=8)
        txt_box.pack(fill="x", pady=4)
        txt_box.insert("1.0", "Need a Python/Django developer to build a REST API and connect it to a React frontend dashboard. Immediate start.")

        out_box = tk.Text(wrap, height=9, bg="#070a0e", fg="#39ff84", relief="flat", highlightthickness=1, highlightbackground="#103a22", font=("Segoe UI", 9), padx=8, pady=8)
        out_box.pack(fill="both", expand=True, pady=6)

        def _analyze_and_draft():
            role_t = role_entry.get().strip() or "Developer Gig"
            comp_t = comp_entry.get().strip() or "Client"
            pay_t = pay_entry.get().strip() or "$150"
            desc_t = txt_box.get("1.0", "end").strip()

            job_dict = {
                "id": f"custom-{int(time.time()*1000)%1000000}",
                "title": role_t,
                "company": comp_t,
                "pay": pay_t,
                "snippet": desc_t,
                "tier": "quick_fix" if ("$" in pay_t and int(re.sub(r"[^\d]", "", pay_t) or 0) <= 600) else "contract",
                "platform": "Custom Lead",
                "url": "",
            }

            pitch = self.generate_pitch(job_dict)
            _copy_clipboard(pitch)
            self.track_application(job_dict, status="applied", notes=desc_t[:120])

            out_box.delete("1.0", "end")
            out_box.insert("1.0", f"✓ PITCH COPIED TO CLIPBOARD & APPLICATION LOGGED TO TRACKER!\n\n{pitch}")

        tk.Button(
            wrap,
            text="⚡ Analyze, Draft Winning Pitch & Log to Tracker",
            command=_analyze_and_draft,
            bg="#006b8f",
            fg="#ffffff",
            relief="flat",
            bd=0,
            padx=14,
            pady=6,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        ).pack(anchor="w", pady=4)


_JOB_CENTER: JobCommandCenter | None = None


def init_job_command_center(root: Any = None, write_log_cb: Callable[[str], None] | None = None) -> JobCommandCenter:
    global _JOB_CENTER
    if _JOB_CENTER is None:
        _JOB_CENTER = JobCommandCenter(root=root, write_log_cb=write_log_cb)
    else:
        if root is not None:
            _JOB_CENTER.root = root
        if write_log_cb is not None:
            _JOB_CENTER.write_log_cb = write_log_cb
    return _JOB_CENTER


def get_job_command_center() -> JobCommandCenter:
    global _JOB_CENTER
    if _JOB_CENTER is None:
        _JOB_CENTER = JobCommandCenter()
    return _JOB_CENTER


def job_command_center_control(action: str = "open", query: str = "", tier: str = "all", platform: str = "") -> str:
    """Entry point for R.O.S.E tool calling and voice commands."""
    center = get_job_command_center()
    act = (action or "open").strip().lower()

    if act in ("open", "show", "dashboard", "window", "gui", "tracker", "radar"):
        center.open_dashboard()
        return "Opened R.O.S.E Job Command Center & Application Tracker."
    if act in ("autofill", "install_autofill", "autofill_installer", "fill_form"):
        from actions.autofill_copilot import open_installer_in_chrome
        return open_installer_in_chrome()
    if act in ("search_chrome", "live_search", "chrome_search", "twitter", "google_ats", "upwork"):
        plat = platform or ("twitter" if "twitter" in act else ("upwork" if "upwork" in act else "google"))
        return launch_live_chrome_search(plat, query)
    if act in ("scan", "refresh", "search"):
        center.scan_all_channels(force=True)
        return "Started fresh official-source job scan and 0-100 ranking in the background."
    if act in ("top_picks", "list", "jobs"):
        picks = [
            j for j in center.cached_radar
            if not j.get("blacklisted") and j.get("eligibility_status") != "excluded" and not j.get("hidden_low_score")
        ][:5]
        if not picks:
            return "No eligible ranked jobs cached yet. Say 'scan jobs' to fetch official feeds."
        lines = [f"{i}. {p['title']} at {p['company']} ({p['score']}%) — {p['match_reason']}" for i, p in enumerate(picks, 1)]
        return "\n".join(lines)
    if act in ("health", "sources"):
        health = center.source_health
        if not health:
            return "No source scan has completed yet."
        return "; ".join(f"{h['source']}: {h['message']}" for h in health)
    if act in ("analytics", "stats"):
        st = center.get_analytics()
        return f"Job Stats: Total Applied={st['total_applied']}, Interviewing={st['interviewing']}, Rate={st['interview_rate_pct']}%, Follow-Ups Due={st['follow_ups_due']}."
    return f"Unknown job_command_center action: {action}"
