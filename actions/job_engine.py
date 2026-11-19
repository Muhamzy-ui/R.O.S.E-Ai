"""
R.O.S.E Job Command Center - Honest Data, Cleaning, Eligibility & Ranking Engine (Phase 3)

Capabilities:
- Loads user-editable configuration from `jobs_profile.yaml`
- HTML/Entity sanitization via BeautifulSoup + double html.unescape + `&nbsp;` removal
- Repeated title/metadata junk stripping ("Job Title:" etc.)
- Real job-type classification from source fields/text (never fakes "QUICK FIX")
- Nigeria (WAT, UTC+1) eligibility parser (US/UK/EU-only exclusions override RSS tags)
- Configurable 0-100 scoring (title skill matches weighted higher than description)
- Honest freshness tracking (marks missing timestamps as "date unknown")
- Deduplication by normalized company + title
- Official-source fetching with User-Agent, backoff retries, caching, and Source Health
- Reddit strictly gated on PRAW credentials in `.env` (shows "disabled" otherwise)
"""

from __future__ import annotations

import datetime as dt
import email.utils
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import requests
import yaml
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_YAML_PATH = BASE_DIR / "jobs_profile.yaml"
ENV_PATH = BASE_DIR / ".env"
SOURCE_CACHE_PATH = BASE_DIR / "data" / "jobs_source_cache.json"

USER_AGENT = "ROSE-JobRadar/3.0 (+https://github.com/Muhamzy-ui; Python/Requests)"
HTTP_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
}

DEFAULT_PROFILE: dict[str, Any] = {
    "profile": {
        "location": "Abuja, Nigeria",
        "country": "Nigeria",
        "timezone": "WAT",
        "utc_offset_hours": 1,
        "seniority_level": "junior-to-mid",
        "seniority_years": 3,
        "preferred_types": ["remote_contract", "full_time_remote"],
        "min_pay_monthly_usd": None,
    },
    "skills": {
        "Python": 10,
        "Django": 10,
        "FastAPI": 8,
        "React": 9,
        "JavaScript": 7,
        "TypeScript": 7,
        "PostgreSQL": 8,
        "REST APIs": 8,
        "Celery": 5,
        "Redis": 5,
        "WebSockets": 5,
        "Paystack": 5,
    },
    "skill_aliases": {
        "Django": ["drf", "django rest framework"],
        "REST APIs": ["rest api", "restful", "restful api", "rest apis"],
        "PostgreSQL": ["postgres", "pgvector"],
        "React": ["react.js", "reactjs", "next.js", "nextjs"],
        "TypeScript": ["typescript"],
        "JavaScript": ["javascript", "es6"],
        "FastAPI": ["fastapi"],
        "WebSockets": ["websocket", "websockets", "socket.io"],
    },
    "blacklist_words": [
        "sales", "procurement", "military", "hr", "human resources", "wholesale",
        "robotics", "industrial", "data engineer", "data platform architect",
        "blockchain", "web3", "solidity", "smart contract", "nursing", "nurse",
        "medical", "telemarketing", "recruiter", "talent acquisition",
        "account executive", "business development", "bdr", "sdr", "realtor",
        "real estate", "warehouse", "driver",
    ],
    "enabled_blacklist_overrides": [],
    "eligibility": {
        "eligible_keywords": [
            "worldwide", "work from anywhere", "anywhere in the world", "remote anywhere",
            "anywhere", "global", "international", "africa", "nigeria", "emea", "wat",
            "utc+1", "gmt+1", "no location restrictions", "remote - global",
        ],
        "excluded_keywords": [
            "us only", "u.s. only", "united states only",
            "must reside in the us", "must reside in the united states",
            "must be located in the us", "must be located in the united states",
            "us citizen", "u.s. citizen", "us citizenship",
            "authorized to work in the us", "authorized to work in the united states",
            "w2 only", "w-2 only", "1099 us only", "us residents", "residents of us only",
            "within the united states", "remote in u.s.", "remote in the us",
            "remote (us)", "remote - us", "location: united states", "us-based only",
            "uk only", "united kingdom only", "right to work in the uk", "must reside in the uk",
            "eu only", "european union only", "must reside in the eu", "eu work permit",
            "canada only", "australia only", "us/canada only", "north america only",
            "americas only", "latam only", "india only", "philippines only",
            "security clearance required", "active secret clearance",
            "us timezones only", "pst/est only", "est/pst only", "pacific time only",
        ],
    },
    "scoring_weights": {
        "skill_match": 40,
        "seniority_fit": 20,
        "eligibility": 20,
        "pay_fit": 10,
        "freshness": 10,
    },
    "ranking": {
        "top_picks_count": 5,
        "top_picks_min_score": 70,
        "hide_below_score": 40,
        "hide_excluded_by_default": True,
        "refresh_interval_minutes": 30,
        "cache_ttl_seconds": 1800,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_jobs_profile(yaml_path: Path | None = None) -> dict[str, Any]:
    """Loads `jobs_profile.yaml` and merges safely with sensible defaults."""
    target = yaml_path or PROFILE_YAML_PATH
    loaded: dict[str, Any] = {}
    if target.exists():
        try:
            raw = yaml.safe_load(target.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                loaded = raw
        except Exception:
            loaded = {}
    return _deep_merge(DEFAULT_PROFILE, loaded)


def _read_env_vars() -> dict[str, str]:
    """Reads `.env` if present without mutating process environment."""
    vals: dict[str, str] = {}
    if ENV_PATH.exists():
        try:
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass
    for k in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"):
        if os.environ.get(k):
            vals[k] = os.environ[k]
    return vals


# ── Cleaning & Sanitization ──────────────────────────────────────────────────

def clean_html_and_text(raw: Any) -> str:
    """Unescapes HTML entities cleanly, strips tags via BeautifulSoup, collapses whitespace."""
    if raw is None:
        return ""
    text = str(raw)
    # Unescape twice in case RSS feeds double-encoded entities like &amp;nbsp;
    text = html.unescape(html.unescape(text))
    text = re.sub(r"&nbsp;?", " ", text, flags=re.IGNORECASE)
    if "<" in text and ">" in text:
        try:
            soup = BeautifulSoup(text, "html.parser")
            text = soup.get_text(separator=" ")
        except Exception:
            text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_job_title(raw_title: Any) -> str:
    """Removes repeated 'Job Title:' junk, hiring tags, and excess whitespace."""
    t = clean_html_and_text(raw_title)
    if not t:
        return "Untitled Role"
    # Strip leading prefixes like [Hiring], Job Title:, Role:, Position:
    t = re.sub(r"^(\[?\s*hiring\s*\]?:?\s*)", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^(job\s+title|role|position)\s*:\s*", "", t, flags=re.IGNORECASE)
    # Remove trailing repeated metadata like "Robotics Software Engineer  Job Title: Robotics Software Engineer"
    m_dup = re.search(r"\s+(job\s+title|role|position)\s*:\s*(.+)$", t, flags=re.IGNORECASE)
    if m_dup:
        prefix = t[:m_dup.start()].strip(" -|:,")
        suffix = m_dup.group(2).strip(" -|:,")
        if prefix and suffix:
            # Keep cleaner prefix if they overlap or repeat
            if suffix.lower() in prefix.lower() or prefix.lower() in suffix.lower():
                t = prefix
            else:
                t = prefix
    # Collapse exact doubled half-titles ("Software Engineer Software Engineer")
    words = t.split()
    if len(words) >= 4 and len(words) % 2 == 0:
        half = len(words) // 2
        if [w.lower() for w in words[:half]] == [w.lower() for w in words[half:]]:
            t = " ".join(words[:half])
    return re.sub(r"\s+", " ", t).strip(" -|")


def clean_company_name(raw_company: Any) -> str:
    c = clean_html_and_text(raw_company)
    c = re.sub(r"^(company|employer)\s*:\s*", "", c, flags=re.IGNORECASE)
    return c[:90] if c else "Unknown Company"


def normalize_company_title_key(company: str, title: str) -> str:
    """Normalized key used to dedupe identical postings across sources."""
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    return f"{norm(company)}||{norm(title)}"


# ── Pay, Job Type & Freshness Parsers ────────────────────────────────────────

def _fmt_k(val: float) -> str:
    if val >= 1000 and val % 1000 == 0:
        return f"${int(val // 1000)}k"
    if val >= 1000:
        return f"${val / 1000:.1f}k" if (val % 1000) >= 100 else f"${int(val // 1000)}k"
    return f"${int(round(val))}"


def parse_pay_info(
    raw_pay: str = "",
    salary_min: float | int | None = None,
    salary_max: float | int | None = None,
    period: str = "",
    currency: str = "USD",
    full_text: str = "",
) -> dict[str, Any]:
    """Extracts honest USD compensation and monthly equivalent without inventing pay."""
    curr = (currency or "USD").upper()
    s_min = float(salary_min) if isinstance(salary_min, (int, float)) and float(salary_min) > 0 else None
    s_max = float(salary_max) if isinstance(salary_max, (int, float)) and float(salary_max) > 0 else None
    per = (period or "").lower().strip()

    if s_min or s_max:
        lo = s_min or s_max
        hi = s_max or s_min
        assert lo is not None and hi is not None
        if not per:
            # Infer period responsibly from numeric magnitude
            if hi <= 300:
                per = "hourly"
            elif hi <= 20000:
                per = "monthly"
            else:
                per = "annual"
        if "hour" in per or per == "hr":
            monthly = ((lo + hi) / 2.0) * 160.0 if curr == "USD" else None
            disp = f"${int(lo)}–${int(hi)}/hr" if lo != hi else f"${int(lo)}/hr"
            return {"pay_known": True, "pay_display": disp, "monthly_usd": monthly}
        if "month" in per or per == "mo":
            monthly = ((lo + hi) / 2.0) if curr == "USD" else None
            disp = f"{_fmt_k(lo)}–{_fmt_k(hi)}/mo" if lo != hi else f"{_fmt_k(lo)}/mo"
            return {"pay_known": True, "pay_display": disp, "monthly_usd": monthly}
        # Annual
        monthly = (((lo + hi) / 2.0) / 12.0) if curr == "USD" else None
        disp = f"{_fmt_k(lo)}–{_fmt_k(hi)}/yr" if lo != hi else f"{_fmt_k(lo)}/yr"
        return {"pay_known": True, "pay_display": disp, "monthly_usd": monthly}

    blob = f"{raw_pay} {full_text}".strip()
    if blob:
        # Match explicit ranges with period like "$2,800 - $4,500 per month", "$100k-$175k/yr", "$40-$60/hr"
        m = re.search(
            r"\$\s*(\d[\d,]*k?)\s*(?:[-–to]+\s*\$?\s*(\d[\d,]*k?))?\s*(?:/|per\s+)?\s*"
            r"(hr|hour|hourly|mo|month|monthly|yr|year|yearly|annual|annually)\b",
            blob,
            flags=re.IGNORECASE,
        )
        if m:
            def _to_num(tok: str) -> float:
                t = tok.lower().replace(",", "").strip()
                if t.endswith("k"):
                    return float(t[:-1]) * 1000.0
                return float(t)

            try:
                lo = _to_num(m.group(1))
                hi = _to_num(m.group(2)) if m.group(2) else lo
                if lo > 0 and hi > 0:
                    unit = m.group(3).lower()
                    if unit.startswith("hr") or "hour" in unit:
                        disp = f"${int(lo)}–${int(hi)}/hr" if lo != hi else f"${int(lo)}/hr"
                        return {"pay_known": True, "pay_display": disp, "monthly_usd": ((lo + hi) / 2.0) * 160.0}
                    if unit.startswith("mo") or "month" in unit:
                        disp = f"{_fmt_k(lo)}–{_fmt_k(hi)}/mo" if lo != hi else f"{_fmt_k(lo)}/mo"
                        return {"pay_known": True, "pay_display": disp, "monthly_usd": (lo + hi) / 2.0}
                    disp = f"{_fmt_k(lo)}–{_fmt_k(hi)}/yr" if lo != hi else f"{_fmt_k(lo)}/yr"
                    return {"pay_known": True, "pay_display": disp, "monthly_usd": ((lo + hi) / 2.0) / 12.0}
            except ValueError:
                pass

    return {"pay_known": False, "pay_display": "pay unlisted", "monthly_usd": None}


def classify_job_type(source_type: str = "", categories: str = "", full_text: str = "") -> str:
    """
    Honest classification strictly from source job type / explicit metadata.
    Returns "" (empty string / no badge) if unknown. Never invents "QUICK FIX".
    """
    primary = f"{source_type} {categories}".lower()
    explicit = full_text.lower()

    def _match(blob: str) -> str:
        if re.search(r"\bfull[\s-]*time\b", blob):
            return "Full-Time"
        if re.search(r"\bpart[\s-]*time\b", blob):
            return "Part-Time"
        if re.search(r"\b(contract|contractor|b2b)\b", blob):
            return "Contract"
        if re.search(r"\bfreelance\b", blob):
            return "Freelance"
        if re.search(r"\bintern(ship)?\b", blob):
            return "Internship"
        return ""

    res = _match(primary)
    if res:
        return res

    # Only match body when framed explicitly as Employment/Job Type
    m_lbl = re.search(
        r"(?:employment\s*type|job\s*type|position\s*type|schedule)\s*:\s*([^\n.;|]{2,40})",
        explicit,
    )
    if m_lbl:
        res = _match(m_lbl.group(1))
        if res:
            return res

    return ""


def parse_freshness(
    pub_ts: float | int | None = None,
    pub_iso: str | None = None,
    pub_rfc: str | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    """Computes real age_hours strictly from actual timestamps; never invents freshness."""
    now_val = float(now_ts if now_ts is not None else time.time())
    parsed_ts: float | None = None

    if isinstance(pub_ts, (int, float)) and float(pub_ts) > 1_000_000_000:
        parsed_ts = float(pub_ts)
    elif pub_iso:
        try:
            iso = str(pub_iso).strip().replace("Z", "+00:00")
            parsed_ts = dt.datetime.fromisoformat(iso).timestamp()
        except Exception:
            parsed_ts = None
    elif pub_rfc:
        try:
            parsed_dt = email.utils.parsedate_to_datetime(str(pub_rfc).strip())
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=dt.timezone.utc)
            parsed_ts = parsed_dt.timestamp()
        except Exception:
            parsed_ts = None

    if parsed_ts is None:
        return {"freshness_known": False, "age_hours": None, "posted_label": "date unknown"}

    age_h = max(0.0, (now_val - parsed_ts) / 3600.0)
    if age_h < 1.0:
        mins = max(1, int(round(age_h * 60)))
        lbl = f"{mins}m ago"
    elif age_h < 48.0:
        lbl = f"{int(round(age_h))}h ago"
    else:
        days = int(round(age_h / 24.0))
        lbl = f"{days}d ago"

    return {"freshness_known": True, "age_hours": round(age_h, 1), "posted_label": lbl}


# ── Blacklist & Eligibility ──────────────────────────────────────────────────

def check_blacklist(title: str, description: str = "", tags: str = "", profile: dict | None = None) -> tuple[bool, str]:
    """Returns (is_blacklisted, matched_keyword)."""
    prof = profile or load_jobs_profile()
    raw_words = [str(w).strip().lower() for w in prof.get("blacklist_words", []) if str(w).strip()]
    overrides = {str(w).strip().lower() for w in prof.get("enabled_blacklist_overrides", []) if str(w).strip()}
    active_blacklist = [w for w in raw_words if w not in overrides]

    title_and_tags = f"{title} {tags}".lower()
    desc_low = (description or "").lower()

    for term in active_blacklist:
        pattern = r"\b" + re.escape(term) + r"\b"
        if re.search(pattern, title_and_tags):
            return True, term
        # Multi-word non-dev phrases in title or opening 400 chars
        if " " in term and re.search(pattern, desc_low[:400]):
            return True, term
    return False, ""


def assess_eligibility(
    title: str,
    location: str,
    description: str,
    location_restrictions: list[str] | None = None,
    timezone_restrictions: list[Any] | None = None,
    profile: dict | None = None,
) -> dict[str, str]:
    """
    Determines whether Mahmud (based in Nigeria, WAT UTC+1) is eligible.
    Exclusions inside description override generic feed tags like "Worldwide Remote".
    Returns {status: 'eligible'|'unclear'|'excluded', label: str, reason: str}.
    """
    prof = profile or load_jobs_profile()
    elig_cfg = prof.get("eligibility", {})
    eligible_kws = [str(x).lower() for x in elig_cfg.get("eligible_keywords", [])]
    excluded_kws = [str(x).lower() for x in elig_cfg.get("excluded_keywords", [])]

    loc_clean = clean_html_and_text(location)
    loc_low = loc_clean.lower()
    blob = f"{title} {loc_clean} {description}".lower()

    # 1. Structured API locationRestrictions (e.g. Himalayas)
    if location_restrictions:
        norm_locs = [clean_html_and_text(x).lower() for x in location_restrictions if str(x).strip()]
        if norm_locs:
            allowed_here = any(
                any(k in r for k in ("worldwide", "anywhere", "global", "nigeria", "africa", "emea"))
                for r in norm_locs
            )
            if not allowed_here:
                joined = ", ".join(norm_locs[:3])
                return {
                    "status": "excluded",
                    "label": f"excluded ({joined} only)",
                    "reason": f"Restricted to {joined}",
                }

    # 2. Structured timezoneRestrictions (Nigeria = UTC+1)
    if timezone_restrictions:
        tz_list = []
        for tz in timezone_restrictions:
            if isinstance(tz, (int, float)):
                tz_list.append(float(tz))
            elif isinstance(tz, dict) and "offset" in tz:
                try:
                    tz_list.append(float(tz["offset"]))
                except Exception:
                    pass
        if tz_list and 1.0 not in tz_list and not any(-2.0 <= z <= 4.0 for z in tz_list):
            return {
                "status": "excluded",
                "label": "excluded (timezone mismatch)",
                "reason": f"Timezone restriction excludes WAT (UTC+1): {tz_list}",
            }

    # 3. Hard country/visa exclusions in title, location, or description (override generic RSS tags!)
    for kw in excluded_kws:
        pattern = r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])"
        if re.search(pattern, blob):
            return {
                "status": "excluded",
                "label": f"excluded ({kw})",
                "reason": f"Matched restriction: '{kw}'",
            }

    # Regex patterns for structured onshore/US/EU/onsite/hybrid phrasing missed by flat list
    onshore_patterns = (
        (r"must\s+(?:be\s+)?(?:located|reside|live|based)\s+in\s+(?:the\s+)?(u\.?s\.?|united states|uk|canada|europe|eu|australia|india|germany)\b", "location restriction"),
        (r"\bremote\s*[-–/(]\s*(?:u\.?s\.?|usa|uk|eu|europe|canada|north america|americas|latam)\b", "region-restricted remote"),
        (r"\b(?:est|pst|cst|mst)\s*(?:or\s*(?:est|pst|cst|mst)\s*)?only\b", "US timezone only"),
        (r"\bus\s*(?:&|/|or)\s*canada\s*only\b", "US/Canada only"),
        (r"\b(?:hybrid\s*\(\s*\d+|\d+\s*days?\s*(?:in[- ]?office|onsite|on[- ]?site)|onsite\s+only|in[- ]?office\s+only)\b", "hybrid/onsite required"),
    )
    for pat, label in onshore_patterns:
        m = re.search(pat, blob)
        if m:
            return {
                "status": "excluded",
                "label": f"excluded ({label})",
                "reason": f"Matched '{m.group(0)}'",
            }

    # Specific city/country location header without remote/worldwide/anywhere -> onshore/excluded
    non_remote_city_markers = (
        "san francisco", "new york", "nyc", "menlo park", "austin", "seattle", "boston",
        "chicago", "los angeles", "london", "berlin", "toronto", "bangalore", "india",
        "united states", "bay area",
    )
    if loc_low and "worldwide" not in loc_low and "anywhere" not in loc_low and "remote" not in loc_low:
        for city in non_remote_city_markers:
            if re.search(r"(?<![a-z0-9])" + re.escape(city) + r"(?![a-z0-9])", loc_low):
                return {
                    "status": "excluded",
                    "label": f"excluded ({loc_clean})",
                    "reason": f"Specific non-remote location: {loc_clean}",
                }

    # 4. Explicit global / Nigeria / WAT markers
    for kw in eligible_kws:
        if re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", blob):
            tag = "Nigeria/Africa" if kw in ("nigeria", "africa") else ("WAT/EMEA" if kw in ("wat", "utc+1", "gmt+1", "emea") else "worldwide")
            return {
                "status": "eligible",
                "label": tag,
                "reason": f"Matched open location indicator '{kw}'",
            }

    return {
        "status": "unclear",
        "label": "remote (check location)",
        "reason": "Says Remote without explicit country eligibility or exclusion",
    }


# ── Scoring & Ranking Engine (0-100) ─────────────────────────────────────────

_DEV_TITLE_HINTS = (
    "developer", "engineer", "programmer", "full stack", "fullstack", "full-stack",
    "frontend", "front-end", "backend", "back-end", "python", "django", "fastapi",
    "react", "javascript", "typescript", "software", "web dev", "api", "postgres",
)


def score_and_rank_jobs(raw_jobs: list[dict[str, Any]], profile: dict | None = None) -> list[dict[str, Any]]:
    """
    Cleans, deduplicates, checks eligibility & blacklist, scores 0-100, and ranks jobs.
    Every returned item contains clean fields (NO &nbsp;, NO fake pay/date/type),
    `score` (0-100), `match_reason`, `eligibility_status`, `is_top_pick`, `blacklisted`, etc.
    """
    prof = profile or load_jobs_profile()
    weights = prof.get("scoring_weights", {})
    w_skill = float(weights.get("skill_match", 40))
    w_senior = float(weights.get("seniority_fit", 20))
    w_elig = float(weights.get("eligibility", 20))
    w_pay = float(weights.get("pay_fit", 10))
    w_fresh = float(weights.get("freshness", 10))

    skills_cfg: dict[str, float] = {str(k): float(v) for k, v in (prof.get("skills") or {}).items()}
    aliases_cfg: dict[str, list[str]] = prof.get("skill_aliases") or {}
    min_pay = prof.get("profile", {}).get("min_pay_monthly_usd")
    ranking_cfg = prof.get("ranking", {})
    top_count = int(ranking_cfg.get("top_picks_count", 5))
    top_min_score = int(ranking_cfg.get("top_picks_min_score", 70))
    hide_below = int(ranking_cfg.get("hide_below_score", 40))

    processed_map: dict[str, dict[str, Any]] = {}

    for raw in raw_jobs:
        title = clean_job_title(raw.get("title") or raw.get("position") or "")
        company = clean_company_name(raw.get("company") or raw.get("companyName") or "")
        location = clean_html_and_text(raw.get("location") or "Remote")
        desc = clean_html_and_text(raw.get("description") or raw.get("snippet") or raw.get("excerpt") or "")
        tags_list = raw.get("tags") or raw.get("categories") or []
        if isinstance(tags_list, str):
            tags_str = clean_html_and_text(tags_list)
        else:
            tags_str = clean_html_and_text(" ".join(str(t) for t in tags_list))

        blacklisted, bl_reason = check_blacklist(title, desc, tags_str, prof)

        elig = assess_eligibility(
            title=title,
            location=location,
            description=desc,
            location_restrictions=raw.get("locationRestrictions"),
            timezone_restrictions=raw.get("timezoneRestrictions"),
            profile=prof,
        )

        # Pay parsing (uses structured fields first, then description text)
        pay_info = parse_pay_info(
            raw_pay=clean_html_and_text(raw.get("pay") or raw.get("salary") or ""),
            salary_min=raw.get("salary_min") or raw.get("minSalary"),
            salary_max=raw.get("salary_max") or raw.get("maxSalary"),
            period=str(raw.get("salaryPeriod") or raw.get("salary_period") or ""),
            currency=str(raw.get("currency") or "USD"),
            full_text=desc,
        )

        # Freshness parsing
        if "freshness_known" in raw and "age_hours" in raw and "posted_label" in raw:
            fresh_info = {
                "freshness_known": bool(raw["freshness_known"]),
                "age_hours": raw["age_hours"],
                "posted_label": raw["posted_label"],
            }
        else:
            fresh_info = parse_freshness(
                pub_ts=raw.get("epoch") or raw.get("pubDate") if isinstance(raw.get("pubDate"), (int, float)) else None,
                pub_iso=raw.get("date") or raw.get("created_at"),
                pub_rfc=raw.get("pubDate") if isinstance(raw.get("pubDate"), str) else None,
            )

        # Real job type classification
        job_type = classify_job_type(
            source_type=clean_html_and_text(raw.get("employmentType") or raw.get("job_type") or ""),
            categories=tags_str,
            full_text=desc,
        )

        # ── 1. Skill Score (title matches weigh 2.0x body, tags 1.3x) ────────
        title_low = title.lower()
        tags_low = tags_str.lower()
        desc_low = desc.lower()

        matched_skills: list[tuple[float, str]] = []
        raw_skill_points = 0.0

        for skill_name, weight in skills_cfg.items():
            terms = [skill_name.lower()] + [str(a).lower() for a in aliases_cfg.get(skill_name, [])]
            in_title = any(re.search(r"(?<![a-z0-9])" + re.escape(tm) + r"(?![a-z0-9])", title_low) for tm in terms)
            in_tags = any(re.search(r"(?<![a-z0-9])" + re.escape(tm) + r"(?![a-z0-9])", tags_low) for tm in terms)
            in_desc = any(re.search(r"(?<![a-z0-9])" + re.escape(tm) + r"(?![a-z0-9])", desc_low) for tm in terms)

            if in_title:
                pts = weight * 2.0
                raw_skill_points += pts
                matched_skills.append((pts, skill_name))
            elif in_tags:
                pts = weight * 1.3
                raw_skill_points += pts
                matched_skills.append((pts, skill_name))
            elif in_desc:
                pts = weight * 1.0
                raw_skill_points += pts
                matched_skills.append((pts, skill_name))

        matched_skills.sort(key=lambda x: x[0], reverse=True)
        matched_names = [s[1] for s in matched_skills]

        # Normalize against realistic top target (e.g. ~38 weighted pts = 100% of w_skill)
        target_cap = 36.0
        has_dev_title = any(h in title_low for h in _DEV_TITLE_HINTS)
        if not matched_names and not has_dev_title:
            s_skill = 0.0
        else:
            s_skill = min(w_skill, (raw_skill_points / target_cap) * w_skill)
            if not matched_names and has_dev_title:
                s_skill = w_skill * 0.20

        # ── 2. Seniority Fit (target: junior-to-mid ~3 years) ───────────────
        seniority_list = " ".join(str(x) for x in (raw.get("seniority") or [])).lower()
        sen_blob = f"{title_low} {seniority_list} {desc_low[:500]}"
        if any(k in title_low for k in ("staff ", "principal ", "head of", "director", "vp ", "architect")):
            s_senior = w_senior * 0.10
        elif re.search(r"\b(?:senior|sr\.?|lead)\b", title_low):
            s_senior = w_senior * 0.35
        elif any(k in sen_blob for k in ("7+ years", "8+ years", "10+ years")):
            s_senior = w_senior * 0.25
        elif any(k in sen_blob for k in ("junior", "mid-level", "mid level", "intermediate", "associate", "2+ years", "3+ years", "1-3 years", "2-4 years")):
            s_senior = w_senior * 1.00
        elif "intern" in title_low:
            s_senior = w_senior * 0.30
        else:
            # Standard developer title with no executive bloat -> strong fit
            s_senior = w_senior * 0.80

        # ── 3. Eligibility Score ────────────────────────────────────────────
        if elig["status"] == "eligible":
            s_elig = w_elig
        elif elig["status"] == "unclear":
            s_elig = w_elig * 0.50
        else:
            s_elig = 0.0

        # ── 4. Pay Fit Score ────────────────────────────────────────────────
        if pay_info["pay_known"]:
            m_usd = pay_info["monthly_usd"]
            if min_pay and isinstance(min_pay, (int, float)) and m_usd is not None:
                s_pay = w_pay if m_usd >= float(min_pay) else (w_pay * 0.20)
            else:
                s_pay = w_pay * 0.90
        else:
            s_pay = w_pay * 0.40

        # ── 5. Freshness Score ──────────────────────────────────────────────
        if fresh_info["freshness_known"] and fresh_info["age_hours"] is not None:
            ah = float(fresh_info["age_hours"])
            if ah <= 6.0:
                s_fresh = w_fresh
            elif ah <= 24.0:
                s_fresh = w_fresh * 0.85
            elif ah <= 48.0:
                s_fresh = w_fresh * 0.65
            elif ah <= 168.0:
                s_fresh = w_fresh * 0.35
            else:
                s_fresh = w_fresh * 0.15
        else:
            s_fresh = w_fresh * 0.30

        raw_total = s_skill + s_senior + s_elig + s_pay + s_fresh
        if blacklisted:
            raw_total = 0.0
        elif elig["status"] == "excluded":
            raw_total = min(raw_total, 25.0)

        final_score = int(round(max(0.0, min(100.0, raw_total))))

        # Build concise 1-line explanation
        skill_part = f"Matches {' + '.join(matched_names[:4])}" if matched_names else ("Role match (no core stack keyword)" if has_dev_title else "Low stack match")
        if blacklisted:
            skill_part = f"Filtered ({bl_reason})"
        reason_line = f"{skill_part} · {elig['label']} · {pay_info['pay_display']} · {fresh_info['posted_label']}"

        job_id = str(raw.get("id") or f"job-{abs(hash(raw.get('url') or f'{company}:{title}')) % 10_000_000}")
        clean_item = {
            "id": job_id,
            "title": title,
            "company": company,
            "location": location or "Remote",
            "platform": str(raw.get("platform") or raw.get("source") or "Radar"),
            "url": str(raw.get("url") or "").strip(),
            "job_type": job_type,  # "" when unknown -> UI shows no badge!
            "pay": pay_info["pay_display"],
            "pay_known": pay_info["pay_known"],
            "monthly_usd": pay_info["monthly_usd"],
            "age_hours": fresh_info["age_hours"],
            "freshness_known": fresh_info["freshness_known"],
            "posted_desc": fresh_info["posted_label"],
            "eligibility_status": elig["status"],
            "eligibility_label": elig["label"],
            "eligibility_reason": elig["reason"],
            "blacklisted": blacklisted,
            "blacklist_reason": bl_reason,
            "matched_skills": matched_names,
            "score": final_score,
            "match_reason": reason_line,
            "is_top_pick": False,
            "hidden_low_score": final_score < hide_below,
            "snippet": desc[:320],
            "score_breakdown": {
                "skill": round(s_skill, 1),
                "seniority": round(s_senior, 1),
                "eligibility": round(s_elig, 1),
                "pay": round(s_pay, 1),
                "freshness": round(s_fresh, 1),
            },
        }

        dedupe_key = normalize_company_title_key(company, title)
        existing = processed_map.get(dedupe_key)
        if existing is None or clean_item["score"] > existing["score"]:
            processed_map[dedupe_key] = clean_item

    all_clean = list(processed_map.values())
    all_clean.sort(
        key=lambda x: (
            1 if x["blacklisted"] else 0,
            1 if x["eligibility_status"] == "excluded" else 0,
            -x["score"],
            x["age_hours"] if x["age_hours"] is not None else 99999.0,
        )
    )

    # Pin Top Picks: best N (default 5) non-blacklisted, non-excluded jobs with score >= 70
    pinned = 0
    for item in all_clean:
        if (
            not item["blacklisted"]
            and item["eligibility_status"] != "excluded"
            and item["score"] >= top_min_score
            and pinned < top_count
        ):
            item["is_top_pick"] = True
            pinned += 1

    return all_clean


# ── Official Sources Fetchers with Caching, Backoff & Health ─────────────────

def _http_get_with_backoff(url: str, timeout: float = 8.0, retries: int = 2) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < retries:
                    time.sleep(0.8 * (2 ** attempt))
                    continue
            return resp
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(0.8 * (2 ** attempt))
    if last_exc:
        raise last_exc
    raise RuntimeError(f"Request failed: {url}")


def _fetch_remoteok_raw() -> list[dict[str, Any]]:
    r = _http_get_with_backoff("https://remoteok.com/api?tag=dev", timeout=9.0)
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code}")
    data = r.json()
    raw_jobs: list[dict[str, Any]] = []
    for it in data:
        if not isinstance(it, dict) or not it.get("position"):
            continue
        tags = it.get("tags") or []
        raw_jobs.append({
            "id": f"rok-{it.get('id', abs(hash(it.get('url', ''))) % 10_000_000)}",
            "title": it.get("position", ""),
            "company": it.get("company", ""),
            "location": it.get("location") or "Worldwide Remote",
            "platform": "RemoteOK",
            "url": it.get("apply_url") or it.get("url") or f"https://remoteok.com/remote-jobs/{it.get('id', '')}",
            "description": it.get("description") or "",
            "tags": tags,
            "salary_min": it.get("salary_min"),
            "salary_max": it.get("salary_max"),
            "epoch": it.get("epoch"),
            "date": it.get("date"),
            "employmentType": it.get("job_type") or "",
        })
    return raw_jobs


def _fetch_wwr_raw() -> list[dict[str, Any]]:
    endpoints = (
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    )
    raw_jobs: list[dict[str, Any]] = []
    seen_links: set[str] = set()
    for ep in endpoints:
        r = _http_get_with_backoff(ep, timeout=9.0)
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code}")
        tree = ET.fromstring(r.content)
        for item in tree.findall(".//item"):
            link = (item.findtext("link") or "").strip()
            if link and link in seen_links:
                continue
            if link:
                seen_links.add(link)
            raw_title = item.findtext("title") or ""
            company = "WeWorkRemotely Employer"
            title = raw_title
            if ":" in raw_title:
                p0, p1 = raw_title.split(":", 1)
                company, title = p0.strip(), p1.strip()
            region = item.findtext("region") or "Remote"
            cat = item.findtext("category") or ""
            jtype = item.findtext("type") or ""
            desc = item.findtext("description") or ""
            pub_str = item.findtext("pubDate") or ""
            raw_jobs.append({
                "id": f"wwr-{abs(hash(link or raw_title)) % 10_000_000}",
                "title": title,
                "company": company,
                "location": region,
                "platform": "WeWorkRemotely",
                "url": link,
                "description": desc,
                "tags": [cat] if cat else [],
                "employmentType": jtype or cat,
                "pubDate": pub_str,
            })
    return raw_jobs


def _fetch_hackernews_raw() -> list[dict[str, Any]]:
    story_url = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=3"
    r_story = _http_get_with_backoff(story_url, timeout=9.0)
    if not r_story.ok:
        raise RuntimeError(f"HTTP {r_story.status_code}")
    stories = r_story.json().get("hits") or []
    hiring_story_id = None
    for st in stories:
        if "who is hiring" in str(st.get("title") or "").lower():
            hiring_story_id = st.get("objectID") or st.get("story_id")
            break
    if not hiring_story_id:
        return []

    c_url = f"https://hn.algolia.com/api/v1/search_by_date?tags=comment,story_{hiring_story_id}&hitsPerPage=60"
    r_c = _http_get_with_backoff(c_url, timeout=9.0)
    if not r_c.ok:
        raise RuntimeError(f"HTTP {r_c.status_code}")
    hits = r_c.json().get("hits") or []
    raw_jobs: list[dict[str, Any]] = []
    for h in hits:
        # Top-level hiring comments only (parent_id equals story_id)
        if str(h.get("parent_id") or "") != str(hiring_story_id):
            continue
        comment_html = h.get("comment_text") or ""
        clean_txt = clean_html_and_text(comment_html)
        if len(clean_txt) < 40:
            continue
        low_txt = clean_txt.lower()
        # Filter out misplaced "Who wants to be hired?" / self-promo candidate profiles
        if (
            "willing to relocate" in low_txt
            or "seeking:" in low_txt[:180]
            or "happy to shift my hours" in low_txt
            or re.search(r"\bi(?:'m| am)\s+a\s+(?:software|backend|frontend|full[- ]?stack)\s+(?:engineer|developer)\b", low_txt[:350])
        ):
            continue
        first_line = clean_html_and_text(re.split(r"<p>|\n", comment_html)[0])[:120]
        company = "HN Startup"
        role_title = first_line
        loc = "Remote"
        if "|" in first_line:
            parts = [p.strip() for p in first_line.split("|") if p.strip()]
            if len(parts) >= 2:
                company = parts[0]
                role_title = parts[1]
            if len(parts) >= 3:
                loc = " · ".join(parts[2:])
        elif re.match(r"^(?:location|technologies|résumé|resume|email)\s*:", first_line, re.I):
            continue
        cid = h.get("objectID")
        raw_jobs.append({
            "id": f"hn-{cid}",
            "title": role_title,
            "company": company,
            "location": loc,
            "platform": "HackerNews",
            "url": f"https://news.ycombinator.com/item?id={cid}",
            "description": clean_txt,
            "created_at": h.get("created_at"),
        })
    return raw_jobs


def _fetch_himalayas_raw() -> list[dict[str, Any]]:
    url = "https://himalayas.app/jobs/api?limit=50"
    r = _http_get_with_backoff(url, timeout=9.0)
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code}")
    jobs = r.json().get("jobs") or []
    raw_jobs: list[dict[str, Any]] = []
    for it in jobs:
        locs = it.get("locationRestrictions") or []
        loc_str = ", ".join(str(x) for x in locs) if locs else "Worldwide Remote"
        raw_jobs.append({
            "id": f"him-{abs(hash(it.get('applicationLink') or it.get('guid') or it.get('title', ''))) % 10_000_000}",
            "title": it.get("title") or "",
            "company": it.get("companyName") or "",
            "location": loc_str,
            "locationRestrictions": locs,
            "timezoneRestrictions": it.get("timezoneRestrictions") or [],
            "platform": "Himalayas",
            "url": it.get("applicationLink") or it.get("guid") or "",
            "description": it.get("description") or it.get("excerpt") or "",
            "tags": it.get("categories") or [],
            "employmentType": it.get("employmentType") or "",
            "minSalary": it.get("minSalary"),
            "maxSalary": it.get("maxSalary"),
            "salaryPeriod": it.get("salaryPeriod") or "",
            "currency": it.get("currency") or "USD",
            "seniority": it.get("seniority") or [],
            "pubDate": it.get("pubDate"),
        })
    return raw_jobs


def _fetch_jobspresso_raw() -> list[dict[str, Any]]:
    url = "https://jobspresso.co/feed/?post_type=job_listing"
    r = _http_get_with_backoff(url, timeout=9.0)
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code}")
    tree = ET.fromstring(r.content)
    raw_jobs: list[dict[str, Any]] = []
    for item in tree.findall(".//item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub_str = item.findtext("pubDate") or ""
        desc = item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or item.findtext("description") or ""
        raw_jobs.append({
            "id": f"jpr-{abs(hash(link or title)) % 10_000_000}",
            "title": title,
            "company": "Jobspresso Partner",
            "location": "Remote",
            "platform": "Jobspresso",
            "url": link,
            "description": desc,
            "pubDate": pub_str,
        })
    return raw_jobs


def _fetch_reddit_praw_raw() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Only queries Reddit if valid PRAW credentials exist in .env; skips silently otherwise."""
    env = _read_env_vars()
    cid = env.get("REDDIT_CLIENT_ID", "").strip()
    csec = env.get("REDDIT_CLIENT_SECRET", "").strip()
    if not cid or not csec:
        return [], {
            "source": "Reddit",
            "status": "disabled",
            "raw_count": 0,
            "kept_count": 0,
            "last_success": None,
            "message": "disabled (no PRAW credentials)",
        }

    try:
        import praw  # type: ignore
    except ImportError:
        return [], {
            "source": "Reddit",
            "status": "disabled",
            "raw_count": 0,
            "kept_count": 0,
            "last_success": None,
            "message": "disabled (praw not installed)",
        }

    try:
        reddit = praw.Reddit(
            client_id=cid,
            client_secret=csec,
            user_agent=env.get("REDDIT_USER_AGENT") or USER_AGENT,
        )
        raw_jobs: list[dict[str, Any]] = []
        for sub in ("forhire", "freelance_forhire", "jobbit", "pythonjobs"):
            for post in reddit.subreddit(sub).new(limit=15):
                if "[hiring]" not in post.title.lower() and "hiring" not in post.title.lower():
                    continue
                raw_jobs.append({
                    "id": f"red-{post.id}",
                    "title": post.title,
                    "company": f"Reddit u/{getattr(post.author, 'name', 'client')}",
                    "location": f"Remote (r/{sub})",
                    "platform": "Reddit",
                    "url": f"https://www.reddit.com{post.permalink}",
                    "description": post.selftext or "",
                    "epoch": float(post.created_utc),
                })
        status = "ok" if raw_jobs else "empty"
        msg = f"{len(raw_jobs)} jobs" if raw_jobs else "0 — check feed"
        return raw_jobs, {
            "source": "Reddit",
            "status": status,
            "raw_count": len(raw_jobs),
            "kept_count": 0,
            "last_success": dt.datetime.now().isoformat(),
            "message": msg,
        }
    except Exception as exc:
        return [], {
            "source": "Reddit",
            "status": "error",
            "raw_count": 0,
            "kept_count": 0,
            "last_success": None,
            "message": f"error: {str(exc)[:40]}",
        }


def scan_all_sources(force: bool = False, profile: dict | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Fetches official sources (respecting cache TTL when force=False), cleans,
    checks eligibility, scores 0-100, ranks, and computes Source Health.
    Returns (ranked_jobs, source_health_list).
    """
    prof = profile or load_jobs_profile()
    ttl = int(prof.get("ranking", {}).get("cache_ttl_seconds", 1800))

    # Return warm cached response unless forced or expired
    if not force and SOURCE_CACHE_PATH.exists():
        try:
            cache = json.loads(SOURCE_CACHE_PATH.read_text(encoding="utf-8"))
            cached_ts = float(cache.get("fetched_ts", 0.0))
            if (time.time() - cached_ts) < ttl and isinstance(cache.get("ranked_jobs"), list):
                health = cache.get("source_health") or []
                for h in health:
                    if h.get("status") == "ok":
                        h["cached"] = True
                return cache["ranked_jobs"], health
        except Exception:
            pass

    all_raw: list[dict[str, Any]] = []
    health_list: list[dict[str, Any]] = []

    sources = [
        ("RemoteOK", _fetch_remoteok_raw),
        ("WeWorkRemotely", _fetch_wwr_raw),
        ("HackerNews", _fetch_hackernews_raw),
        ("Himalayas", _fetch_himalayas_raw),
        ("Jobspresso", _fetch_jobspresso_raw),
    ]

    for idx, (src_name, fetcher) in enumerate(sources):
        if idx > 0:
            time.sleep(0.25)  # Gentle spacing across feeds
        try:
            batch = fetcher()
            raw_cnt = len(batch)
            all_raw.extend(batch)
            health_list.append({
                "source": src_name,
                "status": "ok" if raw_cnt > 0 else "empty",
                "raw_count": raw_cnt,
                "kept_count": 0,
                "last_success": dt.datetime.now().isoformat(),
                "message": f"{raw_cnt} raw" if raw_cnt > 0 else "0 — check feed",
            })
        except Exception as exc:
            health_list.append({
                "source": src_name,
                "status": "error",
                "raw_count": 0,
                "kept_count": 0,
                "last_success": None,
                "message": f"error ({str(exc)[:36]})",
            })

    red_jobs, red_health = _fetch_reddit_praw_raw()
    all_raw.extend(red_jobs)
    health_list.append(red_health)

    ranked = score_and_rank_jobs(all_raw, profile=prof)

    # Update kept counts per source (non-blacklisted & non-excluded)
    kept_by_src: dict[str, int] = {}
    for j in ranked:
        if not j["blacklisted"] and j["eligibility_status"] != "excluded":
            p = j.get("platform", "")
            kept_by_src[p] = kept_by_src.get(p, 0) + 1

    for h in health_list:
        src = h["source"]
        kept = kept_by_src.get(src, 0)
        h["kept_count"] = kept
        if h["status"] == "ok":
            h["message"] = f"{kept}/{h['raw_count']} eligible"

    # Persist cache cleanly
    try:
        SOURCE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SOURCE_CACHE_PATH.write_text(
            json.dumps(
                {
                    "fetched_ts": time.time(),
                    "fetched_at": dt.datetime.now().isoformat(),
                    "source_health": health_list,
                    "ranked_jobs": ranked,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

    return ranked, health_list
