"""
Content Digest & Smart Utility Module for R.O.S.E:
- summarize_youtube: Extracts YouTube transcript/captions/chapters via yt-dlp and produces key takeaways
- summarize_article: Fetches any web URL and distills main insights into bullet points
- extract_tasks: Extracts action items from text or clipboard (and can save them as reminders)
- currency_convert: Real-time FX converter (USD, NGN, GBP, EUR, CAD, etc.)
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from actions.clipboard_copilot import read_clipboard, write_clipboard
from actions.reminders import add_reminder
from app_config import get_app_config_value

BASE_DIR = Path(__file__).resolve().parent.parent
NOTES_DIR = BASE_DIR / "memory" / "digests"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ROSE-Assistant/1.0"}


def _gemini_summarize(prompt: str, text: str) -> str:
    api_key = str(get_app_config_value("gemini_api_key", "") or "").strip()
    if not api_key:
        return ""
    try:
        from app_config import generate_gemini_content
        resp = generate_gemini_content(
            api_key,
            contents=f"{prompt}\n\n--- SOURCE ---\n{text[:14000]}",
        )
        return (getattr(resp, "text", "") or "").strip()
    except Exception:
        return ""


def _clean_vtt_text(vtt_raw: str) -> str:
    lines = []
    seen = set()
    for line in vtt_raw.splitlines():
        s = line.strip()
        if not s or s.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or "-->" in s or s.isdigit():
            continue
        s = re.sub(r"<[^>]+>", "", s)
        s = " ".join(s.split())
        if s and s not in seen:
            seen.add(s)
            lines.append(s)
    return " ".join(lines)


def _fetch_youtube_transcript(url_or_query: str) -> tuple[dict, str]:
    import yt_dlp  # type: ignore

    target = (url_or_query or "").strip()
    if not target.startswith(("http://", "https://")):
        target = f"ytsearch1:{target}"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-US", "en-GB"],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(target, download=False)
        if "entries" in info and info["entries"]:
            info = info["entries"][0]

    meta = {
        "title": info.get("title", "YouTube Video"),
        "channel": info.get("uploader") or info.get("channel") or "Unknown",
        "duration": int(info.get("duration") or 0),
        "url": info.get("webpage_url") or target,
        "description": (info.get("description") or "")[:1800],
        "chapters": [c.get("title", "") for c in (info.get("chapters") or []) if c.get("title")],
    }

    transcript = ""
    subs_sources = [info.get("subtitles") or {}, info.get("automatic_captions") or {}]
    for source in subs_sources:
        for lang in ("en", "en-US", "en-GB", "en-orig"):
            tracks = source.get(lang)
            if not tracks:
                continue
            vtt_track = next((t for t in tracks if t.get("ext") == "vtt" and t.get("url")), tracks[0])
            t_url = vtt_track.get("url")
            if t_url:
                try:
                    resp = requests.get(t_url, headers=_HEADERS, timeout=8)
                    if resp.ok:
                        transcript = _clean_vtt_text(resp.text)
                        if transcript:
                            break
                except Exception:
                    pass
        if transcript:
            break

    return meta, transcript


def content_digest(
    action: str = "summarize_youtube",
    target: str = "",
    amount: float = 1.0,
    from_currency: str = "USD",
    to_currency: str = "NGN",
    save_note: bool = False,
    create_reminders: bool = False,
) -> str:
    """
    Actions:
    - summarize_youtube: Summarizes a YouTube video/lecture by URL or search query
    - summarize_article: Fetches and summarizes any web page URL
    - extract_tasks: Pulls action items from text or current clipboard (optionally adds reminders)
    - currency_convert: Converts money amounts using real-time FX rates (e.g. USD to NGN)
    """
    act = (action or "summarize_youtube").strip().lower()
    tgt = (target or "").strip()

    if act in ("currency_convert", "convert_currency", "fx", "exchange_rate"):
        src = (from_currency or "USD").strip().upper()
        dst = (to_currency or "NGN").strip().upper()
        amt = float(amount or 1.0)
        try:
            resp = requests.get(f"https://open.er-api.com/v6/latest/{src}", timeout=6)
            if not resp.ok:
                return f"Exchange rate lookup failed (HTTP {resp.status_code})."
            data = resp.json()
            rates = data.get("rates") or {}
            if dst not in rates:
                return f"Currency code '{dst}' not found in live FX rates."
            rate = float(rates[dst])
            converted = amt * rate
            return (
                f"Live Exchange Rate ({src} -> {dst}): 1 {src} = {rate:,.2f} {dst}. "
                f"{amt:,.2f} {src} = {converted:,.2f} {dst}."
            )
        except Exception as exc:
            return f"Currency conversion error: {exc}"

    if act in ("summarize_youtube", "youtube_summary", "video_summary"):
        if not tgt:
            tgt = read_clipboard()
        if not tgt:
            return "Please provide a YouTube link or video title to summarize."
        try:
            meta, transcript = _fetch_youtube_transcript(tgt)
        except Exception as exc:
            return f"Could not inspect YouTube video '{tgt}': {exc}"

        dur_m, dur_s = divmod(meta["duration"], 60)
        source_body = transcript or (
            "Chapters: " + ", ".join(meta["chapters"]) + "\nDescription:\n" + meta["description"]
        )
        summary = _gemini_summarize(
            f"Summarize this YouTube video '{meta['title']}' by {meta['channel']} ({dur_m}m {dur_s}s) into "
            "4-6 clear executive bullet takeaways and up to 3 concrete action points in polished English.",
            source_body,
        ) or source_body[:900]

        write_clipboard(summary)
        note_msg = ""
        if save_note:
            NOTES_DIR.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", meta["title"])[:45].strip("_") or "youtube_summary"
            out_file = NOTES_DIR / f"{dt.date.today().isoformat()}_{slug}.md"
            out_file.write_text(f"# {meta['title']}\nURL: {meta['url']}\n\n{summary}\n", encoding="utf-8")
            note_msg = f" Saved note to {out_file.name}."
        return f"YouTube Summary — {meta['title']} ({meta['channel']}):\n{summary}\n(Copied to clipboard.{note_msg})"

    if act in ("summarize_article", "summarize_url", "read_article"):
        url = tgt or read_clipboard()
        if not url.startswith(("http://", "https://")):
            return "Please provide or copy a valid webpage URL (https://...) to summarize."
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=8)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            for bad in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                bad.decompose()
            title = (soup.title.string.strip() if soup.title and soup.title.string else url)
            text = "\n".join(p.get_text(" ", strip=True) for p in soup.find_all(["h1", "h2", "p", "li"]))
            if len(text) < 120:
                text = soup.get_text(" ", strip=True)
        except Exception as exc:
            return f"Could not read webpage '{url}': {exc}"

        summary = _gemini_summarize(
            f"Summarize the article '{title}' into 4-5 sharp bullet points with key facts and practical takeaways.",
            text,
        ) or text[:1000]
        write_clipboard(summary)
        return f"Article Summary — {title}:\n{summary}\n(Copied to clipboard.)"

    if act in ("extract_tasks", "action_items", "extract_todos"):
        src_text = tgt or read_clipboard()
        if not src_text:
            return "No text provided and clipboard is empty. Copy or pass notes/messages to extract tasks."
        tasks_raw = _gemini_summarize(
            "Extract ONLY concrete actionable tasks from the text below, one per line starting with '- '. Maximum 6 items.",
            src_text,
        )
        lines = [ln.lstrip("-*• ").strip() for ln in tasks_raw.splitlines() if ln.strip()][:6]
        if not lines:
            return "No clear actionable tasks found in the provided text."
        created_count = 0
        if create_reminders:
            for task_title in lines:
                add_reminder(title=task_title, due_iso="", list_name="Action Items")
                created_count += 1
        extra = f" Added {created_count} reminder(s) to your list." if created_count else ""
        return "Extracted action items:\n" + "\n".join(f"- {t}" for t in lines) + extra

    return f"Unknown content_digest action: {action}"

