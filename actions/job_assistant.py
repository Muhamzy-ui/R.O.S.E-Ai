"""
Job Search, Nigeria AI-Task Platforms & 20-Job Daily Sprint Assistant for R.O.S.E.
- Recommends verified 2026-2027 AI training / coding evaluation platforms accepting Nigeria (Payoneer/Deel/USD rails)
- Searches live remote openings (Remotive, Arbeitnow + web fallback), deduplicates against history
- Queues up to 20 jobs/day (`daily_sprint`), drafts CV-tailored cover letters + quick-paste sheets
- Opens manageable batches (`open_batch`, e.g. 5 tabs in Chrome at a time) for logged-in user review & safe submission
- Drafts async/follow-up video pitch scripts (`video_script`) and 3-day follow-up emails (`follow_up`)
- Safety rule: Never bypasses CAPTCHAs/KYC/identity checks or blindly presses final Submit without user review.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

import requests

from actions.file_intel import SEARCH_ROOTS, _read_single_file
from app_config import get_app_config_value

BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
PROFILE_FILE = MEMORY_DIR / "job_profile.json"
CACHE_FILE = MEMORY_DIR / "jobs_cache.json"
DAILY_QUEUE_FILE = MEMORY_DIR / "daily_queue.json"
APPS_FILE = MEMORY_DIR / "job_applications.json"
DRAFTS_DIR = MEMORY_DIR / "job_drafts"

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ROSE-Assistant/1.0"}
_TEXT_MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.5-flash")

# Verified 2026-2027 AI Training / Evaluation / Micro-task Platforms with Nigeria Payability Info
AI_TASK_PLATFORMS_NG = [
    {
        "name": "Mercor",
        "url": "https://work.mercor.com",
        "track": "Coding / Full-Stack / Cybersecurity AI Evaluation",
        "pay": "$30 - $150+/hr",
        "stress": "Medium (AI video interview + technical match; async hourly work)",
        "payout": "Stripe / International bank (connect Raenest/Grey/Payoneer)",
        "ng_status": "Active & Nigeria-friendly for software/STEM talent",
        "fit": "EXCELLENT FIT — Your Python/Django/PostgreSQL CV matches coding evaluator contracts; R.O.S.E Interview Mode can coach your screening call.",
    },
    {
        "name": "Micro1",
        "url": "https://www.micro1.ai",
        "url_jobs": "https://www.micro1.ai/jobs",
        "track": "AI Agent Bug-Fixing, Python/Backend Code Evaluation",
        "pay": "$45 - $100+/hr",
        "stress": "Medium-Low (self-paced async coding/evaluation tasks once vetted)",
        "payout": "Deel / International Wire",
        "ng_status": "Active globally including Nigeria",
        "fit": "EXCELLENT FIT — High 2026-2027 growth as AI labs pay engineers to test and debug autonomous coding agents.",
    },
    {
        "name": "Alignerr (by Labelbox)",
        "url": "https://www.alignerr.com",
        "track": "Python / STEM Coding RLHF & Prompt Alignment",
        "pay": "$20 - $40+/hr",
        "stress": "Medium (rigorous Python assessment up front, flexible task work after)",
        "payout": "Payoneer / Tipalti",
        "ng_status": "Active (apply honestly from real Nigerian IP — never use VPN)",
        "fit": "HIGH FIT — Pass their Python Coder assessment for steady coding-evaluation projects.",
    },
    {
        "name": "OneForma (by Centific)",
        "url": "https://www.oneforma.com",
        "track": "Low-Stress AI Rating, Search Evaluation, Audio/Text Annotation",
        "pay": "$5 - $16/hr (task/project based)",
        "stress": "LOW (clear guidelines, good immediate starter tasks while hunting coding contracts)",
        "payout": "Payoneer (reliable in Nigeria)",
        "ng_status": "Long-standing active Nigeria worker base",
        "fit": "GOOD IMMEDIATE CASHFLOW — Apply for 'Lightspeed / AI Annotation / UHRS' tracks.",
    },
    {
        "name": "TELUS Digital AI",
        "url": "https://jobs.telusdigital.com",
        "track": "Search Quality, AI Response Rating & Media Evaluation",
        "pay": "$4 - $14/hr",
        "stress": "LOW (flexible weekly hours, predictable task queues)",
        "payout": "Payoneer / Direct Wire",
        "ng_status": "Active in Nigeria",
        "fit": "STEADY SUPPLEMENT — Reliable side income with low technical stress.",
    },
    {
        "name": "Mindrift (Toloka Specialist)",
        "url": "https://mindrift.ai",
        "track": "Coding Evaluation, Prompt Engineering & Technical Writing",
        "pay": "$25 - $60/hr (technical tracks)",
        "stress": "Low-Medium (async project batches)",
        "payout": "Payoneer / Bank / Skrill",
        "ng_status": "Active for qualified technical freelancers",
        "fit": "STRONG FIT — Apply to their 'Coding / Software Development AI Tutor' track.",
    },
    {
        "name": "Turing",
        "url": "https://www.turing.com",
        "track": "LLM Coding Trainer & Remote Python/Backend Engineering",
        "pay": "$25 - $70+/hr",
        "stress": "Medium (coding challenge + steady contractor roles)",
        "payout": "Deel / Payoneer / Wire",
        "ng_status": "Active in Nigeria for vetted engineers",
        "fit": "STRONG FIT — Major AI model labs outsource Python RLHF trainers through Turing.",
    },
]


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _copy_to_clipboard(text: str) -> bool:
    if not text:
        return False
    try:
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


def _ask_gemini_text(prompt: str) -> str:
    api_key = str(get_app_config_value("gemini_api_key", "") or "").strip()
    if not api_key:
        return ""
    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore

        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=20000, retry_options=types.HttpRetryOptions(attempts=1)),
        )
        for model_name in _TEXT_MODELS:
            try:
                resp = client.models.generate_content(model=model_name, contents=prompt)
                txt = (getattr(resp, "text", "") or "").strip()
                if txt:
                    return txt
            except Exception:
                continue
    except Exception:
        pass
    return ""


def _discover_resume_file() -> Path | None:
    keywords = ("cv", "resume", "curriculum")
    exts = {".pdf", ".docx", ".doc", ".txt"}
    for folder_key in ("desktop", "documents", "downloads"):
        root = SEARCH_ROOTS[folder_key]
        if not root.exists():
            continue
        try:
            for p in root.rglob("*"):
                if p.is_file() and p.suffix.lower() in exts:
                    if "node_modules" in p.parts:
                        continue
                    low = p.name.lower()
                    if any(k in low for k in keywords):
                        return p
        except Exception:
            continue
    return None


def _auto_enrich_profile_from_resume(profile: dict, resume_text: str) -> dict:
    """Fills missing contact/skills fields cleanly from CV text without overwriting user edits."""
    if not resume_text:
        return profile
    changed = False
    if not profile.get("email"):
        m = re.search(r"[a-zA-Z0-9_.+-]+\s*@\s*[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", resume_text)
        if m:
            profile["email"] = re.sub(r"\s+", "", m.group(0))
            changed = True
    if not profile.get("phone"):
        m = re.search(r"(\+?234[\s-]?\d{3}[\s-]?\d{3}[\s-]?\d{4}|0[789][01]\d[\s-]?\d{3}[\s-]?\d{4}|\b0[789][01]\d{8}\b)", resume_text)
        if m:
            profile["phone"] = re.sub(r"\s+", "", m.group(0))
            changed = True
    if not profile.get("full_name"):
        lines = [ln.strip() for ln in resume_text.splitlines() if ln.strip() and not ln.startswith("[")]
        if lines and len(lines[0].split()) <= 5:
            profile["full_name"] = lines[0].title()
            changed = True
    if not profile.get("skills"):
        found_skills = [
            s for s in ("Python", "Django", "REST APIs", "PostgreSQL", "JavaScript", "React", "Cybersecurity", "Git")
            if s.lower() in resume_text.lower()
        ]
        if found_skills:
            profile["skills"] = ", ".join(found_skills)
            changed = True
    if changed:
        _save_json(PROFILE_FILE, profile)
    return profile


def _get_resume_text(profile: dict) -> str:
    rpath = (profile.get("resume_path") or "").strip()
    path_obj = Path(rpath) if rpath else _discover_resume_file()
    if path_obj and path_obj.exists():
        profile["resume_path"] = str(path_obj)
        text = _read_single_file(path_obj, max_chars=4200)
        _auto_enrich_profile_from_resume(profile, text)
        _save_json(PROFILE_FILE, profile)
        return text
    return ""


def _search_remotive(query: str, limit: int = 8) -> list[dict]:
    results: list[dict] = []
    try:
        resp = requests.get("https://remotive.com/api/remote-jobs?limit=100", headers=_HEADERS, timeout=8)
        if resp.ok:
            q_terms = [t.lower() for t in re.split(r"\s+", query) if len(t) > 1]
            scored: list[tuple[int, dict]] = []
            for item in (resp.json().get("jobs") or []):
                title = str(item.get("title", "") or "")
                cat = str(item.get("category", "") or "")
                tags = " ".join(str(x) for x in (item.get("tags") or []))
                desc = re.sub(r"<[^>]+>", " ", item.get("description", "") or "")
                blob = f"{title} {cat} {tags} {desc[:600]}".lower()
                score = sum(3 if t in title.lower() else (2 if t in tags.lower() else (1 if t in blob else 0)) for t in q_terms)
                if score > 0:
                    scored.append((score, {
                        "title": title or "Role",
                        "company": item.get("company_name", "Company"),
                        "location": item.get("candidate_required_location", "Remote") or "Remote",
                        "url": item.get("url", ""),
                        "source": "Remotive",
                        "salary": item.get("salary", "") or "",
                        "description": desc[:1200],
                    }))
            scored.sort(key=lambda pair: pair[0], reverse=True)
            results = [pair[1] for pair in scored[:limit]]
    except Exception:
        pass
    return results


def _search_arbeitnow(query: str, limit: int = 8) -> list[dict]:
    results: list[dict] = []
    try:
        resp = requests.get("https://www.arbeitnow.com/api/job-board-api", headers=_HEADERS, timeout=7)
        if resp.ok:
            q_terms = [t.lower() for t in re.split(r"\s+", query) if len(t) > 1]
            for item in resp.json().get("data") or []:
                blob = f"{item.get('title', '')} {item.get('company_name', '')} {' '.join(item.get('tags') or [])}".lower()
                if all(t in blob for t in q_terms) or (q_terms and q_terms[0] in blob):
                    results.append({
                        "title": item.get("title", "Role"),
                        "company": item.get("company_name", "Company"),
                        "location": item.get("location", "Remote") or "Remote",
                        "url": item.get("url", ""),
                        "source": "Arbeitnow",
                        "salary": "",
                        "description": re.sub(r"<[^>]+>", " ", item.get("description", "") or "")[:1200],
                    })
                    if len(results) >= limit:
                        break
    except Exception:
        pass
    return results


def _search_ddgs_jobs(query: str, location: str = "", limit: int = 8) -> list[dict]:
    results: list[dict] = []
    try:
        from ddgs import DDGS  # type: ignore

        q = f"{query} remote job apply {location}".strip()
        for r in DDGS(timeout=6).text(q, max_results=limit):
            href = r.get("href") or ""
            if not href:
                continue
            results.append({
                "title": (r.get("title") or "Job Listing")[:95],
                "company": urllib.parse.urlparse(href).netloc.replace("www.", ""),
                "location": location or "Remote",
                "url": href,
                "source": "Web Search",
                "salary": "",
                "description": (r.get("body") or "")[:650],
            })
    except Exception:
        pass
    return results


def _generate_cover_letter(job: dict, profile: dict, resume_text: str) -> str:
    name = profile.get("full_name") or "Candidate"
    email = profile.get("email") or ""
    phone = profile.get("phone") or ""
    skills = profile.get("skills") or ""

    prompt = (
        "Write a concise, persuasive, tailored cover letter (150-210 words, plain text, no placeholders like [Your Name] "
        "when details are provided) for this job application:\n"
        f"Role: {job.get('title')} at {job.get('company')} ({job.get('location')})\n"
        f"Job Excerpt: {(job.get('description') or '')[:900]}\n"
        f"Applicant Name: {name}\nEmail: {email}\nPhone: {phone}\nKey Skills: {skills}\n"
        f"Resume/CV Content:\n{resume_text[:2000]}\n"
    )
    txt = _ask_gemini_text(prompt)
    if txt:
        return txt

    contact_line = " | ".join(x for x in (name, email, phone) if x)
    return (
        f"Dear Hiring Team at {job.get('company', 'your company')},\n\n"
        f"I am excited to apply for the {job.get('title', 'open')} position. "
        + (f"With hands-on experience across {skills}, " if skills else "With my technical background, ")
        + "I am confident I can contribute immediately to your team's goals and deliver high-quality results.\n\n"
        + "Thank you for your time and consideration.\n\n"
        + f"Best regards,\n{contact_line}"
    )


def _open_in_chrome(url: str) -> None:
    if not url:
        return
    try:
        from actions.browser import browser_control
        browser_control("open_url", url=url)
    except Exception:
        import webbrowser
        webbrowser.open(url)


def _format_quick_paste_sheet(profile: dict, cover_letter: str, job: dict) -> str:
    return (
        f"=== QUICK-PASTE APPLICATION SHEET ===\n"
        f"Target Role: {job.get('title')} @ {job.get('company')}\n"
        f"Apply URL: {job.get('url')}\n"
        f"Full Name: {profile.get('full_name', '')}\n"
        f"Email: {profile.get('email', '')}\n"
        f"Phone: {profile.get('phone', '')}\n"
        f"Key Skills: {profile.get('skills', '')}\n"
        f"CV File: {profile.get('resume_path', '')}\n\n"
        f"--- COVER LETTER / WHY THIS ROLE ---\n"
        f"{cover_letter}\n"
    )


def job_assistant(
    action: str = "search_jobs",
    query: str = "",
    location: str = "",
    resume_path: str = "",
    full_name: str = "",
    email: str = "",
    phone: str = "",
    skills: str = "",
    url: str = "",
    job_index: int = 1,
    confirmed: bool = False,
    batch_size: int = 5,
) -> str:
    """
    Job, Nigeria AI-Task Platforms & 20/Day Assisted Application Sprint Assistant:
    - ai_task_platforms: Vetted 2026-2027 AI training & coding task platforms paying well in Nigeria
    - search_jobs: Searches live remote/tech openings
    - daily_sprint: Builds a fresh deduplicated 20-job daily queue matched to your CV + saves tailored cover letters
    - open_batch: Opens up to `batch_size` (default 5) queued jobs in Chrome tabs + copies tailored pitch cleanly
    - save_profile / get_profile: Manages CV & contact info
    - prepare_application / apply_now: Tailors cover letter for clipboard and opens apply page for review
    - video_script: Writes a 60-90s follow-up / async video pitch from your CV
    - follow_up: Lists older applications needing follow-up + generates polite follow-up emails
    - list_applications: Shows prepared/opened job applications & daily progress count
    """
    act = (action or "search_jobs").strip().lower()

    if act in ("ai_task_platforms", "ai_tasks", "ai_jobs", "nigeria_ai_tasks", "platforms"):
        profile = _load_json(PROFILE_FILE, {})
        resume_text = _get_resume_text(profile)
        skills_note = profile.get("skills") or "Python, Django, REST APIs, PostgreSQL, Cybersecurity"

        lines = [
            "VETTED 2026-2027 AI TASK PLATFORMS PAYING NIGERIA WELL (Ranked for your profile):",
            f"Candidate Stack Detected: {skills_note}",
            "",
            "[TIER 1 — HIGH-PAYING CODING / TECH AI TASKS ($20 - $150+/hr)]",
        ]
        for idx, p in enumerate(AI_TASK_PLATFORMS_NG[:3] + [AI_TASK_PLATFORMS_NG[5], AI_TASK_PLATFORMS_NG[6]], start=1):
            lines.append(
                f"{idx}. {p['name']} ({p['url']}) — Pay: {p['pay']} | Payout: {p['payout']}\n"
                f"   Track: {p['track']} | Stress: {p['stress']}\n"
                f"   Nigeria Status: {p['ng_status']}\n"
                f"   Why: {p['fit']}"
            )
        lines.append("\n[TIER 2 — LOW-STRESS STEADY ENTRY TASKS ($4 - $16/hr, faster cashflow)]")
        for idx, p in enumerate(AI_TASK_PLATFORMS_NG[3:5], start=6):
            lines.append(
                f"{idx}. {p['name']} ({p['url']}) — Pay: {p['pay']} | Payout: {p['payout']}\n"
                f"   Track: {p['track']} | Stress: {p['stress']}\n"
                f"   Why: {p['fit']}"
            )
        lines.append(
            "\n[PAYOUT & SAFETY TIPS FOR NIGERIA]\n"
            "- Set up a verified Payoneer account + Grey/Raenest/Geegpay USD receiving account before onboarding.\n"
            "- Outlier AI / Scale AI: Nigeria eligibility is heavily project-restricted on general tracks; NEVER use a VPN or fake ID (causes instant permanent bans and forfeited payouts).\n"
            "- Mercor / Micro1 require an async technical/video screening: turn on R.O.S.E Silent Interview Mode before starting those!"
        )
        if confirmed:
            for p in AI_TASK_PLATFORMS_NG[:min(5, int(batch_size or 5))]:
                _open_in_chrome(p["url"])
                time.sleep(0.7)
            lines.append("\nOpened the top AI task platform signup pages in Chrome for you.")
        else:
            lines.append("\nSay 'open the top AI task websites in Chrome' (or run with confirmed=True) and I will open up to 5 tabs for you to sign up.")
        return "\n".join(lines)

    if act in ("save_profile", "update_profile", "set_profile"):
        profile = _load_json(PROFILE_FILE, {})
        if full_name:
            profile["full_name"] = full_name.strip()
        if email:
            profile["email"] = email.strip()
        if phone:
            profile["phone"] = phone.strip()
        if skills:
            profile["skills"] = skills.strip()
        if resume_path:
            p = Path(resume_path.strip().strip("\"'")).expanduser()
            if not p.exists():
                return f"Could not find resume file at '{resume_path}'. Please check the file path."
            profile["resume_path"] = str(p)
        elif not profile.get("resume_path"):
            disc = _discover_resume_file()
            if disc:
                profile["resume_path"] = str(disc)
        _save_json(PROFILE_FILE, profile)
        _get_resume_text(profile)
        return (
            f"Saved job profile for {profile.get('full_name') or 'User'}. "
            f"CV/Resume: {profile.get('resume_path') or 'Not linked yet'}."
        )

    if act in ("get_profile", "profile", "status"):
        profile = _load_json(PROFILE_FILE, {})
        if not profile.get("resume_path"):
            disc = _discover_resume_file()
            if disc:
                profile["resume_path"] = str(disc)
                _save_json(PROFILE_FILE, profile)
        _get_resume_text(profile)
        if not profile:
            return "No job profile saved yet. Tell me your full name, email, phone, key skills, or CV path."
        return (
            f"Job Profile: Name={profile.get('full_name', 'Not set')}, "
            f"Email={profile.get('email', 'Not set')}, "
            f"Phone={profile.get('phone', 'Not set')}, "
            f"Skills={profile.get('skills', 'Not set')}, "
            f"CV={profile.get('resume_path', 'Not found yet')}."
        )

    if act in ("daily_sprint", "queue_daily", "sprint"):
        profile = _load_json(PROFILE_FILE, {})
        resume_text = _get_resume_text(profile)
        queries = [query.strip()] if query.strip() else [
            "Python Django developer",
            "Backend developer",
            "Full stack developer",
            "AI trainer python",
        ]
        target_count = 20
        seen_urls = {a.get("url", "") for a in _load_json(APPS_FILE, []) if a.get("url")}
        collected: list[dict] = []

        for sub_q in queries:
            if len(collected) >= target_count:
                break
            batch = _search_remotive(sub_q, limit=8)
            batch.extend(_search_arbeitnow(sub_q, limit=8))
            if len(batch) < 5:
                batch.extend(_search_ddgs_jobs(sub_q, location=location or "remote worldwide Nigeria", limit=6))
            for item in batch:
                u = item.get("url", "")
                if not u or u in seen_urls:
                    continue
                seen_urls.add(u)
                item["status"] = "queued"
                collected.append(item)
                if len(collected) >= target_count:
                    break

        if not collected:
            return "Could not pull fresh remote roles right now; check internet or broaden search terms."

        today_str = dt.date.today().isoformat()
        queue_payload = {
            "date": today_str,
            "target": target_count,
            "cursor": 0,
            "jobs": collected,
        }
        _save_json(DAILY_QUEUE_FILE, queue_payload)
        _save_json(CACHE_FILE, collected[:10])

        lines = [
            f"Built today's assisted job queue ({len(collected)} / {target_count} fresh roles saved for {today_str}):"
        ]
        for idx, j in enumerate(collected[:10], start=1):
            lines.append(f"{idx}. {j['title']} at {j['company']} ({j['location']}) [{j['source']}]")
        if len(collected) > 10:
            lines.append(f"... plus {len(collected) - 10} more queued in memory/daily_queue.json.")
        lines.append(
            "IMPORTANT SAFETY & AUTH RULE: R.O.S.E will NOT bypass CAPTCHAs or auto-press final Submit across external sites. "
            "Instead, ask me to 'open batch of 5 jobs' and I will open 5 tabs at a time in your logged-in Chrome while preparing your tailored cover letters onto your clipboard!"
        )
        return "\n".join(lines)

    if act in ("open_batch", "batch_apply", "next_batch"):
        qdata = _load_json(DAILY_QUEUE_FILE, {})
        jobs = qdata.get("jobs") or []
        if not jobs:
            return "Your daily job queue is empty. Ask me to run a daily job sprint first (or search for jobs)."

        cursor = int(qdata.get("cursor", 0) or 0)
        count = max(1, min(int(batch_size or 5), 5))  # cap at 5 tabs per batch to keep Chrome responsive & safe
        pending = jobs[cursor : cursor + count]
        if not pending:
            return f"All {len(jobs)} queued jobs for {qdata.get('date', 'today')} have been opened! Run a new daily sprint to fetch more."

        profile = _load_json(PROFILE_FILE, {})
        resume_text = _get_resume_text(profile)
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        apps = _load_json(APPS_FILE, [])

        opened_lines: list[str] = []
        first_clipboard_text = ""
        for offset, job in enumerate(pending, start=1):
            cover = _generate_cover_letter(job, profile, resume_text)
            sheet = _format_quick_paste_sheet(profile, cover, job)
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", f"{job['company']}_{job['title']}")[:42].strip("_")
            draft_path = DRAFTS_DIR / f"{stamp}_{offset}_{slug}.txt"
            draft_path.write_text(sheet, encoding="utf-8")

            if offset == 1:
                first_clipboard_text = cover
            if confirmed:
                _open_in_chrome(job["url"])
                time.sleep(0.8)

            job["status"] = "opened_for_review" if confirmed else "prepared"
            apps.append({
                "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
                "title": job["title"],
                "company": job["company"],
                "url": job["url"],
                "draft_file": str(draft_path),
                "status": job["status"],
            })
            opened_lines.append(f"{cursor + offset}. {job['title']} at {job['company']} -> draft {draft_path.name}")

        if first_clipboard_text:
            _copy_to_clipboard(first_clipboard_text)
        qdata["cursor"] = cursor + len(pending)
        _save_json(DAILY_QUEUE_FILE, qdata)
        _save_json(APPS_FILE, apps[-80:])

        header = (
            f"Opened batch of {len(pending)} application(s) in Chrome ({qdata['cursor']} / {len(jobs)} queued):"
            if confirmed
            else f"Prepared batch of {len(pending)} tailored application drafts ({qdata['cursor']} / {len(jobs)} queued). Confirm with 'open batch in Chrome' to launch the 5 tabs:"
        )
        return (
            header + "\n" + "\n".join(opened_lines)
            + "\nCover letter #1 is already copied to your clipboard (press Ctrl+V). Review fields & click Submit yourself in each tab."
        )

    if act in ("video_script", "followup_video", "follow_up_video", "intro_video"):
        profile = _load_json(PROFILE_FILE, {})
        resume_text = _get_resume_text(profile)
        target_role = query.strip() or "Full Stack Python / Django & AI Engineer"
        prompt = (
            "Write a confident, natural 60-75 second spoken camera pitch / follow-up video script in first-person English "
            "(around 130-165 words, broken into short speakable cues) for:\n"
            f"Role / Context: {target_role}\n"
            f"Candidate Profile & Real CV Metrics:\n{resume_text[:2200]}\n"
            "Highlight real achievements cleanly without exaggerations or placeholders."
        )
        script = _ask_gemini_text(prompt)
        if not script:
            script = (
                f"Hi team, I'm {profile.get('full_name') or 'Mahmud Bashir'}, applying for the {target_role} opportunity.\n\n"
                "I bring hands-on experience building production REST APIs with Python, Django, and PostgreSQL — including "
                "optimizing slow database queries down from 3.2 seconds to under 50 milliseconds.\n\n"
                "I combine strong backend problem-solving with a cybersecurity mindset so code stays fast, tested, and reliable. "
                "I'd love to bring that speed and ownership to your team — thank you!"
            )
        copied = _copy_to_clipboard(script)
        return (
            f"FOLLOW-UP / INTRO VIDEO SCRIPT (60-75s{' — copied to clipboard' if copied else ''}):\n\n"
            f"{script}\n\n"
            "Tip: You can record yourself on Loom/Mercor/camera reading these lines cleanly. "
            "(R.O.S.E scripts & coaches you cleanly on screen — it will not deepfake your face/KYC.)"
        )

    if act in ("follow_up", "followup", "follow_up_check"):
        apps = _load_json(APPS_FILE, [])
        if not apps:
            return "No tracked applications yet to follow up on. Prepare or open applications first!"
        recent = apps[-5:]
        profile = _load_json(PROFILE_FILE, {})
        name = profile.get("full_name") or "Mahmud Bashir"
        sample_job = recent[-1]
        follow_msg = (
            f"Subject: Following up — {sample_job.get('title')} Application\n\n"
            f"Hello {sample_job.get('company')} Hiring Team,\n\n"
            f"I recently submitted my application for the {sample_job.get('title')} role and wanted to briefly reiterate "
            "my strong interest. With my experience in Python, Django REST Framework, and production PostgreSQL optimization, "
            "I'd love the chance to contribute to your team.\n\n"
            "Please let me know if any additional code samples or details would be helpful.\n\n"
            f"Best regards,\n{name}"
        )
        copied = _copy_to_clipboard(follow_msg)
        lines = [f"Recent applications to follow up on ({len(recent)}):"]
        for a in reversed(recent):
            lines.append(f"- {a.get('title')} at {a.get('company')} ({a.get('timestamp')}) — {a.get('url')}")
        lines.append(f"\nReady-to-paste follow-up draft for the latest application{' (copied to clipboard)' if copied else ''}:\n{follow_msg}")
        return "\n".join(lines)

    if act in ("search_jobs", "search", "find_jobs"):
        q = (query or "").strip()
        if not q:
            return "Please tell me what job role or keywords you want to search for (for example, 'Python developer' or 'AI trainer')."

        jobs = _search_remotive(q, limit=5)
        if len(jobs) < 5:
            jobs.extend(_search_arbeitnow(q, limit=5 - len(jobs)))
        if len(jobs) < 4:
            jobs.extend(_search_ddgs_jobs(q, location=location, limit=5 - len(jobs)))

        if location:
            loc_l = location.strip().lower()
            loc_matched = [j for j in jobs if loc_l in (j.get("location") or "").lower()]
            others = [j for j in jobs if j not in loc_matched]
            jobs = loc_matched + others

        if not jobs:
            return f"No live job listings found for '{q}' right now. Try broadening the role title or location."

        seen = set()
        unique_jobs: list[dict] = []
        for j in jobs:
            u = j.get("url", "")
            if u and u not in seen:
                seen.add(u)
                unique_jobs.append(j)
        unique_jobs = unique_jobs[:8]
        _save_json(CACHE_FILE, unique_jobs)

        lines = [f"Found {len(unique_jobs)} live job opening(s) for '{q}':"]
        for idx, j in enumerate(unique_jobs, start=1):
            sal = f" | {j['salary']}" if j.get("salary") else ""
            lines.append(f"{idx}. {j['title']} at {j['company']} ({j['location']}{sal}) — {j['url']}")
        lines.append("Say 'prepare application for number 1' or run 'daily_sprint' to prepare up to 20 applications/day in batches of 5.")
        return "\n".join(lines)

    if act in ("prepare_application", "prepare", "apply_now", "apply"):
        cached = _load_json(CACHE_FILE, [])
        job: dict | None = None
        if url and url.strip():
            job = {
                "title": query or "Target Position",
                "company": urllib.parse.urlparse(url.strip()).netloc.replace("www.", "") or "Company",
                "location": location or "Remote",
                "url": url.strip(),
                "source": "Direct URL",
                "description": "",
            }
        else:
            idx = max(1, int(job_index or 1)) - 1
            if 0 <= idx < len(cached):
                job = cached[idx]

        if not job:
            return "Please search for jobs first or provide the direct job URL so I can prepare your application."

        profile = _load_json(PROFILE_FILE, {})
        resume_text = _get_resume_text(profile)
        cover_letter = _generate_cover_letter(job, profile, resume_text)

        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", f"{job['company']}_{job['title']}")[:45].strip("_")
        draft_path = DRAFTS_DIR / f"{stamp}_{slug}.txt"
        draft_path.write_text(_format_quick_paste_sheet(profile, cover_letter, job), encoding="utf-8")

        copied = _copy_to_clipboard(cover_letter)

        apps = _load_json(APPS_FILE, [])
        apps.append({
            "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
            "title": job["title"],
            "company": job["company"],
            "url": job["url"],
            "draft_file": str(draft_path),
            "status": "opened_for_review" if act in ("apply_now", "apply") else "prepared",
        })
        _save_json(APPS_FILE, apps[-80:])

        if act in ("apply_now", "apply") or confirmed:
            _open_in_chrome(job["url"])
            clip_note = "Your tailored cover letter is already copied to your clipboard (just press Ctrl+V on the form)." if copied else ""
            cv_note = f" Use your CV at {profile['resume_path']} to upload your resume." if profile.get("resume_path") else " Attach your CV file on the application page."
            return (
                f"Opened the application page for {job['title']} at {job['company']} in Chrome and saved draft to {draft_path.name}. "
                f"{clip_note}{cv_note} Please review all form fields carefully before clicking Submit."
            )

        return (
            f"Prepared your tailored cover letter for {job['title']} at {job['company']} (saved to {draft_path.name}"
            + (", and copied to your clipboard" if copied else "")
            + f"). Would you like me to open {job['url']} in Chrome so you can review and submit it?"
        )

    if act in ("list_applications", "history", "list"):
        apps = _load_json(APPS_FILE, [])
        today_iso = dt.date.today().isoformat()
        today_count = sum(1 for a in apps if str(a.get("timestamp", "")).startswith(today_iso))
        if not apps:
            return "No job applications have been prepared or opened yet."
        lines = [f"Applications today: {today_count}/20 | Recent applications ({len(apps[-10:])}):"]
        for a in reversed(apps[-10:]):
            lines.append(f"- [{a.get('status', 'prepared')}] {a.get('title')} at {a.get('company')} ({a.get('timestamp', '')})")
        return "\n".join(lines)

    return f"Unknown job_assistant action: {action}"
