"""
Workflow Autopilot: Power Routines & Antigravity Git/Dev Co-Pilot for R.O.S.E.
Combines single-command daily macros (deep_work, morning_startup, end_of_day, job_hunt_sprint, meeting_mode)
with hands-free developer workflows (git_status, git_diff, git_commit, run_tests, explain_error).
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

from actions.browser import _chrome_winrt_play_status
from actions.busy_bar import busy_bar_control
from actions.calendar import get_calendar_events
from actions.clipboard_copilot import clipboard_copilot, read_clipboard
from actions.job_assistant import job_assistant
from actions.laptop_control import laptop_control, split_screen
from actions.morning_wakeup import build_morning_briefing_text
from actions.open_app import open_workspace
from actions.pc_optimizer import pc_optimizer
from actions.reminders import get_reminders

BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
FOCUS_FILE = MEMORY_DIR / "focus_stats.json"
DAILY_LOGS_DIR = MEMORY_DIR / "daily_logs"
_NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def _load_focus_stats() -> dict:
    if not FOCUS_FILE.exists():
        return {"sessions": [], "total_minutes": 0}
    try:
        return json.loads(FOCUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"sessions": [], "total_minutes": 0}


def _record_focus_session(minutes: int, label: str) -> dict:
    data = _load_focus_stats()
    data.setdefault("sessions", []).append({
        "date": dt.date.today().isoformat(),
        "started_at": dt.datetime.now().strftime("%H:%M"),
        "minutes": minutes,
        "label": label,
    })
    data["sessions"] = data["sessions"][-100:]
    data["total_minutes"] = int(data.get("total_minutes", 0)) + minutes
    FOCUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    FOCUS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def _resolve_repo_path(path_hint: str) -> Path | None:
    if path_hint and path_hint.strip():
        p = Path(path_hint.strip().strip("\"'")).expanduser()
        if p.exists():
            return p
        # Search Desktop / Documents / Scratch by folder name
        for parent in (Path.home() / "Desktop", Path.home() / "Documents", BASE_DIR.parent):
            cand = parent / path_hint.strip()
            if cand.exists():
                return cand
    # Auto-detect active git repos on Desktop or BASE_DIR
    if (BASE_DIR / ".git").exists():
        return BASE_DIR
    for parent in (Path.home() / "Desktop", Path.home() / "Documents"):
        if not parent.exists():
            continue
        for child in parent.iterdir():
            if child.is_dir() and (child / ".git").exists():
                return child
    return BASE_DIR


def _run_cmd(args: list[str], cwd: Path, timeout: int = 12) -> tuple[int, str]:
    try:
        res = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_NO_WIN,
        )
        out = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
        return res.returncode, out
    except Exception as exc:
        return 1, str(exc)


def workflow_autopilot(
    action: str = "deep_work",
    target: str = "",
    message: str = "",
    minutes: int = 45,
    confirmed: bool = False,
) -> str:
    """
    Power Routines:
      - deep_work / focus_mode: Opens workspace, splits Antigravity+Chrome, starts Busy Bar focus timer, logs focus streak
      - morning_startup: Morning briefing + opens workspace + checks PC performance
      - end_of_day / wrap_up: Saves daily summary log, checks tomorrow's calendar, closes Busy Bar
      - job_hunt_sprint: Links CV/profile + searches fresh jobs for target role
      - meeting_mode: Sets Busy Bar 'IN A MEETING', pauses active browser media, restores workspace
      - focus_stats: Reports total Deep Work sessions & minutes logged
    Dev & Git Co-Pilot:
      - git_status | git_diff | git_commit (requires confirmed=True & message) | run_tests | explain_error
    """
    act = (action or "deep_work").strip().lower()
    mins = max(5, min(180, int(minutes or 45)))

    if act in ("deep_work", "focus_mode", "work_mode", "study_mode"):
        ws = open_workspace()
        tile = split_screen("Antigravity", "Chrome")
        try:
            busy_res = busy_bar_control("timer", "DEEP WORK", countdown_minutes=mins)
        except Exception as exc:
            busy_res = f"Busy Bar skipped ({exc})"
        stats = _record_focus_session(mins, target or "Deep Work")
        today_count = sum(1 for s in stats.get("sessions", []) if s.get("date") == dt.date.today().isoformat())
        return (
            f"Deep Work Mode activated ({mins} min timer). {ws} | {tile} | {busy_res} "
            f"(Session #{today_count} today logged.)"
        )

    if act in ("morning_startup", "start_my_day", "morning_routine"):
        briefing = build_morning_briefing_text()
        ws = open_workspace()
        health = pc_optimizer("health_report")
        return f"{briefing}\n\nWorkspace: {ws}\nSystem Check: {health}"

    if act in ("end_of_day", "wrap_up", "evening_wrap_up", "finish_work"):
        cal_tmrw = get_calendar_events("tomorrow", limit=5)
        apps_hist = job_assistant("list_applications")
        stats = _load_focus_stats()
        today_str = dt.date.today().isoformat()
        today_mins = sum(
            int(s.get("minutes", 0))
            for s in stats.get("sessions", [])
            if s.get("date") == today_str
        )
        try:
            busy_bar_control("close")
        except Exception:
            pass

        DAILY_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_file = DAILY_LOGS_DIR / f"{today_str}.md"
        summary_body = (
            f"# Daily Wrap-Up — {today_str}\n\n"
            f"- Deep Work Logged Today: {today_mins} minutes\n"
            f"- Job Applications:\n{apps_hist}\n\n"
            f"- Tomorrow's Schedule:\n{cal_tmrw}\n"
        )
        log_file.write_text(summary_body, encoding="utf-8")
        return (
            f"End-of-day wrap-up saved to {log_file.name}. Today you logged {today_mins} focus minutes. "
            f"Tomorrow's schedule: {cal_tmrw}"
        )

    if act in ("job_hunt_sprint", "job_sprint"):
        prof = job_assistant("get_profile")
        jobs = job_assistant("search_jobs", query=target or "python developer remote")
        return f"{prof}\n\n{jobs}"

    if act in ("meeting_mode", "call_mode"):
        ws = open_workspace(["Chrome", "WhatsApp"])
        paused_note = ""
        if _chrome_winrt_play_status(try_play=False) == "PLAYING":
            laptop_control("media_play_pause")
            paused_note = " Paused active browser playback."
        try:
            busy_bar_control("display", "IN A MEETING")
        except Exception:
            pass
        return f"Meeting Mode ready. {ws}.{paused_note} Busy Bar set to 'IN A MEETING'."

    if act in ("focus_stats", "streak"):
        stats = _load_focus_stats()
        today_str = dt.date.today().isoformat()
        today_sessions = [s for s in stats.get("sessions", []) if s.get("date") == today_str]
        today_mins = sum(int(s.get("minutes", 0)) for s in today_sessions)
        return (
            f"Focus Stats: {len(today_sessions)} session(s) ({today_mins} minutes) today | "
            f"All-time total: {stats.get('total_minutes', 0)} minutes across {len(stats.get('sessions', []))} sessions."
        )

    # ── Dev & Git Co-Pilot ────────────────────────────────────────────────────
    repo = _resolve_repo_path(target)
    if repo is None:
        return "Could not find a project directory."

    if act == "git_status":
        code, out = _run_cmd(["git", "status", "-sb"], cwd=repo)
        if code != 0:
            return f"Git status in '{repo.name}': {out or 'Not a git repository.'}"
        return f"Git status ({repo.name}):\n{out}"

    if act == "git_diff":
        code, out = _run_cmd(["git", "diff", "--stat"], cwd=repo)
        if code != 0:
            return f"Git diff error in '{repo.name}': {out}"
        return f"Git changes ({repo.name}):\n{out or 'Working tree clean (no unstaged changes).'}"

    if act == "git_commit":
        commit_msg = (message or "").strip() or f"checkpoint: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        if not confirmed:
            return (
                f"CONFIRMATION REQUIRED: This will run `git add -A && git commit -m \"{commit_msg}\"` inside '{repo}'. "
                f"Ask the user to confirm, then call workflow_autopilot(action='git_commit', target='{repo}', message='{commit_msg}', confirmed=True)."
            )
        c1, o1 = _run_cmd(["git", "add", "-A"], cwd=repo)
        if c1 != 0:
            return f"git add failed: {o1}"
        c2, o2 = _run_cmd(["git", "commit", "-m", commit_msg], cwd=repo)
        return f"Git commit result ({repo.name}):\n{o2 or 'Committed.'}"

    if act == "run_tests":
        if (repo / "pytest.ini").exists() or (repo / "tests").exists() or any(repo.glob("test_*.py")):
            code, out = _run_cmd(["pytest", "-q"], cwd=repo, timeout=25)
            return f"Tests finished (exit {code}) in {repo.name}:\n{out[-1500:]}"
        if (repo / "package.json").exists():
            code, out = _run_cmd(["npm", "test", "--", "--watchAll=false"], cwd=repo, timeout=25)
            return f"NPM tests finished (exit {code}) in {repo.name}:\n{out[-1500:]}"
        return f"No pytest or npm test suite detected automatically in '{repo}'."

    if act == "explain_error":
        clip = read_clipboard()
        if clip.strip():
            return clipboard_copilot("fix_code", instruction=message or "Explain root cause concisely and show fix.")
        from actions.screen_vision import analyze_screen
        return analyze_screen("Read any error message or code problem visible on screen and explain the exact fix.")

    return f"Unknown workflow_autopilot action: {action}"

