"""
Personal Morning Wake-Up Assistant for R.O.S.E (Timezone: Africa/Lagos, UTC+1).
- Triggers daily at 07:00 AM (or user-configured time)
- Pops up ROSE's screen, plays an alarm chime, and speaks a full morning briefing
  (Greeting + Date/Time + Lagos Weather + Today's Calendar + Today's Reminders)
- Optional Twilio Outbound Voice Call to the user's Nigerian (+234) mobile number if Twilio keys are configured
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from app_config import get_app_config_value
from actions.weather import get_weather_summary, get_configured_location, get_location_datetime
from actions.calendar import get_calendar_events
from actions.reminders import get_reminders


BASE_DIR = Path(__file__).resolve().parent.parent
WAKEUP_FILE = BASE_DIR / "memory" / "morning_wakeup.json"


def _load_cfg() -> dict:
    default = {
        "enabled": True,
        "time_hhmm": "07:00",
        "timezone": "Africa/Lagos",
        "phone_number": "",
        "call_enabled": False,
        "last_triggered_date": "",
    }
    if not WAKEUP_FILE.exists():
        return default
    try:
        data = json.loads(WAKEUP_FILE.read_text(encoding="utf-8"))
        default.update(data)
        return default
    except Exception:
        return default


def _save_cfg(cfg: dict) -> None:
    WAKEUP_FILE.parent.mkdir(parents=True, exist_ok=True)
    WAKEUP_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def build_morning_briefing_text() -> str:
    loc = get_configured_location()
    city = loc["city"]
    display = loc["display"]
    tz_name = loc["timezone"]
    now = get_location_datetime(loc)
    date_str = now.strftime("%A, %d %B %Y")
    time_str = now.strftime("%I:%M %p")
    weather = get_weather_summary(city)
    weather_line = weather if weather.lower().startswith("weather ") else f"Weather in {city}: {weather}"
    cal = get_calendar_events("today", limit=5)
    rem = get_reminders("today", limit=5)

    job_follow_line = ""
    try:
        from actions.job_command_center import get_job_command_center
        jc = get_job_command_center()
        stats = jc.get_analytics()
        fu = stats.get("follow_ups_due", 0)
        if fu > 0:
            job_follow_line = f"\nJob Radar Follow-Up Alert: You have {fu} application(s) awaiting follow-up today (press F10 or say 'open job tracker' to review)."
    except Exception:
        pass

    return (
        f"[MORNING CONTEXT & TODAY'S BRIEF — {display.upper()} ({tz_name})]\n"
        f"Current Date & Local Time: {date_str}, {time_str}\n"
        f"{weather_line}\n"
        f"Today's Scheduled Plan (Calendar): {cal}\n"
        f"Today's Reminders & Tasks: {rem}"
        f"{job_follow_line}\n"
        "Instructions for ROSE: Reply warmly and casually in English (unless the Boss already asked you to switch to Broken/Pidgin) "
        "and call the user 'Boss'. Start naturally by saying good morning and asking how their night was "
        "(e.g., 'Morning, Boss! How was your night? Did you sleep well?') plus at most a quick 1-line note on the time/weather. "
        "Do NOT dump a long monologue of every calendar/reminder item right away — wait for the Boss to answer, "
        "then go over today's schedule and plans interactively together."
    )


def _place_twilio_wakeup_call(phone_number: str, briefing_summary: str) -> str:
    sid = str(get_app_config_value("twilio_account_sid", "") or "").strip()
    token = str(get_app_config_value("twilio_auth_token", "") or "").strip()
    from_num = str(get_app_config_value("twilio_from_number", "") or "").strip()

    if not (sid and token and from_num and phone_number):
        return (
            "Local PC voice wake-up triggered. (Note: To also place a cellular phone call to your mobile number, "
            "add twilio_account_sid, twilio_auth_token, and twilio_from_number in config/api_keys.json)."
        )

    try:
        import requests
        twiml = (
            f"<Response><Say voice='Polly.Joanna'>Good morning! This is Rose, your personal AI assistant. "
            f"{briefing_summary[:400]} Have a productive day!</Say></Response>"
        )
        resp = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json",
            auth=(sid, token),
            data={
                "To": phone_number,
                "From": from_num,
                "Twiml": twiml,
            },
            timeout=12,
        )
        if resp.ok:
            return f"Placed live wake-up telephone call to {phone_number} via Twilio!"
        return f"Twilio call error ({resp.status_code}): {resp.text[:160]}"
    except Exception as exc:
        return f"Failed to place Twilio call: {exc}"


def morning_wakeup_control(
    action: str = "status",
    time_hhmm: str = "07:00",
    phone_number: str = "",
    call_enabled: bool = False,
) -> str:
    """
    - action='enable': Enables daily morning wake-up at time_hhmm (default 07:00 Africa/Lagos)
    - action='disable': Disables the daily morning wake-up
    - action='status': Checks current wake-up configuration
    - action='test_now': Immediately triggers the morning wake-up greeting & briefing
    """
    act = (action or "status").strip().lower()
    cfg = _load_cfg()

    if act == "enable":
        loc = get_configured_location()
        cfg["enabled"] = True
        cfg["timezone"] = loc["timezone"]
        if time_hhmm:
            cfg["time_hhmm"] = time_hhmm.strip()
        if phone_number:
            cfg["phone_number"] = phone_number.strip()
        if call_enabled:
            cfg["call_enabled"] = True
        _save_cfg(cfg)
        return (
            f"Daily morning wake-up enabled for {cfg['time_hhmm']} ({loc['timezone']} timezone). "
            f"I will wake you up with the date, time, {loc['city']} weather, your calendar, and reminders."
        )

    if act == "disable":
        cfg["enabled"] = False
        _save_cfg(cfg)
        return "Daily morning wake-up assistant has been disabled."

    if act in ("test_now", "briefing", "good_morning", "plan", "today"):
        brief = build_morning_briefing_text()
        call_note = ""
        if act == "test_now" and cfg.get("call_enabled") and (phone_number or cfg.get("phone_number")):
            call_note = "\n" + _place_twilio_wakeup_call(phone_number or cfg.get("phone_number", ""), brief)
        return brief + call_note

    loc = get_configured_location()
    return (
        f"Morning Wake-Up Status: {'ENABLED' if cfg.get('enabled') else 'DISABLED'} "
        f"at {cfg.get('time_hhmm', '07:00')} ({loc['timezone']}). "
        f"Mobile Call: {'Enabled (' + cfg.get('phone_number', '') + ')' if cfg.get('call_enabled') else 'Local PC Voice Mode'}."
    )


def check_and_pop_morning_wakeup() -> str | None:
    """Called periodically by main.py's background loop to fire at exactly 07:00 Africa/Lagos."""
    cfg = _load_cfg()
    if cfg.get("enabled", True) is False:
        return None
    loc = get_configured_location()
    now = get_location_datetime(loc)
    today_str = now.strftime("%Y-%m-%d")
    current_hhmm = now.strftime("%H:%M")

    if cfg.get("last_triggered_date") == today_str:
        return None

    target_hhmm = str(cfg.get("time_hhmm", "07:00")).strip()
    if current_hhmm == target_hhmm:
        cfg["last_triggered_date"] = today_str
        _save_cfg(cfg)
        brief = build_morning_briefing_text()
        if cfg.get("call_enabled") and cfg.get("phone_number"):
            _place_twilio_wakeup_call(cfg["phone_number"], brief)
        return brief
    return None

