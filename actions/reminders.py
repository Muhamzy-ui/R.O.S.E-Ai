"""
Reminders & Timed Alerts — Windows Edition for R.O.S.E
Supports ISO timestamps ("2026-11-12T00:30") AND relative strings ("in 2 minutes", "in 30 seconds", "in 1 hour"),
exact remaining countdown reporting (minutes & seconds), scheduled PC action execution,
and live background alerting via pop_due_reminders() so ROSE executes tasks & speaks out loud when due!
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_FILE = BASE_DIR / "memory" / "reminders.json"


def _load_local() -> list[dict]:
    if not LOCAL_FILE.exists():
        return []
    try:
        items = json.loads(LOCAL_FILE.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            return []
        # Auto-heal legacy items that were already notified so they don't show as overdue
        healed = False
        now_ts = int(dt.datetime.now().timestamp())
        for item in items:
            if item.get("notified") and not item.get("completed"):
                item["completed"] = True
                item.setdefault("completed_at", now_ts)
                healed = True
        if healed:
            _save_local(items)
        return items
    except Exception:
        return []


def _save_local(items: list[dict]) -> None:
    LOCAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_query(query: str) -> tuple[str, int]:
    q = (query or "").strip().lower()
    if any(t in q for t in ("clear_overdue", "clear overdue", "clear_all", "clear", "cleanup", "dismiss")):
        return "clear_overdue" if "overdue" in q else "clear", 10
    if any(t in q for t in ("remaining", "remain", "countdown", "time left", "time_left", "how many", "how long", "seconds", "minutes", "status", "left")):
        return "remaining", 10
    if any(t in q for t in ("today", "bugun")):
        return "today", 8
    if any(t in q for t in ("overdue", "past", "geciken")):
        return "overdue", 8
    if any(t in q for t in ("next", "siradaki")):
        return "next", 1
    if any(t in q for t in ("all", "list", "hepsi", "tum")):
        return "all", 15
    return "upcoming", 10


def _normalize_due_iso(due_iso: str) -> tuple[int, bool]:
    raw = (due_iso or "").strip()
    if not raw:
        return 0, False

    now = dt.datetime.now()
    low = raw.lower()

    # Compound or simple relative time support:
    # e.g. "in 1 minute 30 seconds", "in 2 minutes", "in 30 seconds", "in 1 hour", "5m", "45s"
    parts = re.findall(
        r"(\d+)\s*(hr|hrs|hour|hours|h|min|mins|minute|minutes|m|sec|secs|second|seconds|s)\b",
        low,
    )
    if parts:
        total_seconds = 0
        for num_str, unit in parts:
            val = int(num_str)
            if unit.startswith("s"):
                total_seconds += val
            elif unit.startswith("m"):
                total_seconds += val * 60
            else:
                total_seconds += val * 3600
        if total_seconds > 0:
            target = now + dt.timedelta(seconds=total_seconds)
            return int(target.timestamp()), False

    # Time-only support: "14:30" or "08:00"
    time_only = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", raw)
    if time_only:
        hh = int(time_only.group(1))
        mm = int(time_only.group(2))
        ss = int(time_only.group(3) or 0)
        target = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
        if target <= now:
            target += dt.timedelta(days=1)
        return int(target.timestamp()), False

    candidates = (
        ("%Y-%m-%dT%H:%M:%S", False),
        ("%Y-%m-%dT%H:%M", False),
        ("%Y-%m-%d %H:%M:%S", False),
        ("%Y-%m-%d %H:%M", False),
        ("%d.%m.%Y %H:%M", False),
        ("%Y-%m-%d", True),
        ("%d.%m.%Y", True),
    )
    if raw.endswith("Z"):
        try:
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return int(parsed.timestamp()), False
        except ValueError:
            pass
    for fmt, all_day in candidates:
        try:
            parsed = dt.datetime.strptime(raw, fmt)
            return int(parsed.timestamp()), all_day
        except ValueError:
            continue
    raise ValueError("Invalid reminder date/time. Use 'YYYY-MM-DDTHH:MM', 'HH:MM', or 'in X minutes'.")


def _day_label(when: dt.datetime, now: dt.datetime) -> str:
    today = now.date()
    target = when.date()
    if target == today:
        return "Today"
    if target == today + dt.timedelta(days=1):
        return "Tomorrow"
    return when.strftime("%a, %d %b")


def format_remaining_duration(due_ts: int, now_ts: int | None = None) -> str:
    if due_ts <= 0:
        return "no countdown set"
    now_val = int(dt.datetime.now().timestamp()) if now_ts is None else int(now_ts)
    diff = int(due_ts) - now_val
    overdue = diff < 0
    sec_total = abs(diff)
    hours, rem = divmod(sec_total, 3600)
    minutes, seconds = divmod(rem, 60)

    chunks = []
    if hours:
        chunks.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if minutes or (hours and seconds):
        chunks.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    chunks.append(f"{seconds} second" + ("s" if seconds != 1 else ""))

    phrase = ", ".join(chunks)
    if diff == 0:
        return "due right now (0 seconds remaining)"
    if overdue:
        return f"overdue by {phrase}"
    return f"{phrase} remaining (total {sec_total} seconds)"


def _format_due(item: dict, now: dt.datetime) -> str:
    if item.get("due_ts", 0) <= 0:
        return "No time set"
    due = dt.datetime.fromtimestamp(item["due_ts"])
    if item.get("all_day"):
        return f"{_day_label(due, now)} (All day)"
    return f"{_day_label(due, now)} at {due.strftime('%H:%M:%S' if due.second else '%H:%M')}"


def _format_reminder_line(item: dict, now: dt.datetime, include_remaining: bool = True) -> str:
    now_ts = int(now.timestamp())
    due_ts = int(item.get("due_ts", 0) or 0)
    parts = [f"{_format_due(item, now)} — {item['title']}"]
    if include_remaining and due_ts > 0 and not item.get("all_day"):
        parts.append(f"[{format_remaining_duration(due_ts, now_ts)}]")
    if item.get("scheduled_action"):
        parts.append(f"[Action: {item['scheduled_action']}]")
    if item.get("notes"):
        parts.append(f"({item['notes']})")
    if item.get("priority") == 2:
        parts.append("[High Priority]")
    return " ".join(parts)


def get_reminders(query: str = "upcoming", limit: int = 10, list_name: str = "") -> str:
    mode, default_limit = _normalize_query(query)
    limit = max(1, min(20, int(limit or default_limit)))

    all_items = _load_local()
    now = dt.datetime.now()
    now_ts = int(now.timestamp())

    if mode in ("clear_overdue", "clear"):
        cleared = 0
        for item in all_items:
            if item.get("completed"):
                continue
            due_ts = int(item.get("due_ts", 0) or 0)
            if mode == "clear" or (0 < due_ts < now_ts):
                item["completed"] = True
                item["completed_at"] = now_ts
                cleared += 1
        if cleared:
            _save_local(all_items)
        return f"Cleared {cleared} reminder(s)." if cleared else "No reminders needed clearing."

    items = [i for i in all_items if not i.get("completed", False)]

    today_start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    today_end = today_start + 86400

    if mode == "today":
        items = [i for i in items if today_start <= i.get("due_ts", 0) < today_end]
    elif mode == "overdue":
        items = [i for i in items if 0 < i.get("due_ts", 0) < now_ts]
    elif mode == "next":
        items = [i for i in items if i.get("due_ts", 0) >= now_ts]
    elif mode == "remaining":
        items = [i for i in items if i.get("due_ts", 0) >= now_ts]
    elif mode == "upcoming":
        items = [i for i in items if i.get("due_ts", 0) >= now_ts or i.get("due_ts", 0) == 0]

    items.sort(key=lambda i: (i.get("due_ts", 0) <= 0, i.get("due_ts", 0), i["title"].lower()))

    if not items:
        if mode == "remaining":
            return "There are no active timed reminders counting down right now."
        if mode == "overdue":
            return "You have no overdue reminders."
        return "You have no active reminders for that period."

    if mode == "remaining":
        if len(items) == 1:
            rem = items[0]
            rem_str = format_remaining_duration(int(rem.get("due_ts", 0) or 0), now_ts)
            return f"Reminder '{rem['title']}' has {rem_str} (due {_format_due(rem, now)})."
        lines = [f"Active countdowns ({len(items[:limit])}):"]
        for rem in items[:limit]:
            rem_str = format_remaining_duration(int(rem.get("due_ts", 0) or 0), now_ts)
            lines.append(f"- '{rem['title']}': {rem_str} ({_format_due(rem, now)})")
        return "\n".join(lines)

    if mode == "next":
        return f"Next reminder: {_format_reminder_line(items[0], now)}."

    items = items[:limit]
    lines = [f"Found {len(items)} reminder(s):"]
    for item in items:
        lines.append(f"- {_format_reminder_line(item, now)}")
    return "\n".join(lines)


def add_reminder(
    title: str,
    due_iso: str = "",
    notes: str = "",
    list_name: str = "",
    priority: str = "",
    all_day: bool = False,
    scheduled_action: str = "",
) -> str:
    if not title or not title.strip():
        return "Reminder title cannot be empty."

    due_ts = 0
    all_day_final = bool(all_day)
    if due_iso and due_iso.strip():
        try:
            due_ts, inferred = _normalize_due_iso(due_iso)
        except ValueError as exc:
            return str(exc)
        all_day_final = all_day_final or inferred

    prio_map = {"low": 0, "medium": 1, "high": 2}
    now = dt.datetime.now()
    now_ts = int(now.timestamp())
    created = {
        "id": uuid.uuid4().hex,
        "title": title.strip(),
        "list_name": list_name or "Personal",
        "notes": notes or "",
        "priority": prio_map.get((priority or "").lower(), 1),
        "due_ts": due_ts,
        "all_day": all_day_final,
        "scheduled_action": (scheduled_action or "").strip(),
        "created_ts": now_ts,
        "notified": False,
        "completed": False,
    }
    items = _load_local()
    items.append(created)
    _save_local(items)

    when = _format_due(created, now)
    rem_info = f" ({format_remaining_duration(due_ts, now_ts)})" if due_ts > 0 and not all_day_final else ""
    act_info = f" [Will also execute: {created['scheduled_action']}]" if created["scheduled_action"] else ""
    return f"Reminder saved: {when}{rem_info} — {created['title']}{act_info}"


def pop_due_reminders() -> list[dict]:
    """
    Checks for any reminders whose due_ts <= current time and haven't been triggered yet.
    Marks triggered reminders as both notified=True and completed=True so they never linger as overdue!
    """
    items = _load_local()
    if not items:
        return []
    now_ts = int(dt.datetime.now().timestamp())
    due_now: list[dict] = []
    changed = False
    for item in items:
        due_ts = int(item.get("due_ts", 0) or 0)
        if due_ts > 0 and due_ts <= now_ts and not item.get("notified", False) and not item.get("completed", False):
            item["notified"] = True
            item["completed"] = True
            item["completed_at"] = now_ts
            due_now.append(dict(item))
            changed = True
    if changed:
        _save_local(items)
    return due_now
