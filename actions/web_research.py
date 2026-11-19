"""
R.O.S.E (Project MIRROR) - Visual Web Search & Local Place Discovery Engine.
Searches live web sources (Google/Yahoo via ddgs + page scraping), coordinates
local restaurant / breakfast / date-spot discovery using the active location
(e.g., Gwagwalada Phase 2, Abuja), verifies per-card photography strictly
against each card's subject, generates a dark CartoDB local area map, and streams
live step-by-step updates to the pure PySide6 `VrResearchWindow`.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import json
import math
import os
import re
import threading
import time
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw
import requests

from actions.weather import _geocode_city, get_configured_location
from app_config import generate_gemini_content, get_app_config_value
from ui.vr_research import VrResearchController

try:
    from ddgs import DDGS
except Exception:
    try:
        from duckduckgo_search import DDGS  # type: ignore
    except Exception:
        DDGS = None  # type: ignore


_RESEARCH_CONTROLLER: VrResearchController | None = None
_ACTIVE_STEPS: list[str] = []
_USED_IMG_LOCK = threading.Lock()
RoseResearchBoard = VrResearchController  # Backwards-compatible alias

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_PLACE_KEYWORDS = {
    "restaurant", "restaurants", "eat", "food", "breakfast", "lunch", "dinner",
    "brunch", "cafe", "coffee", "bakery", "bistro", "kitchen", "eatery",
    "eateries", "suya", "grill", "pizza", "shawarma", "bukka", "amala",
    "hangout", "date", "girlfriend", "boyfriend", "romantic", "couple",
    "anniversary", "lounge", "rooftop", "bar", "cinema", "hotel", "mall",
    "park", "near me", "nearby", "around me", "around here", "spot", "spots",
    "places to", "where can i", "dine", "dining",
}

_DATE_KEYWORDS = {
    "date", "girlfriend", "boyfriend", "romantic", "couple", "anniversary",
    "valentine", "lounge", "rooftop",
}

_BAD_IMG_WORDS = (
    "logo", "icon", "favicon", "avatar", "sprite", "badge", "flag",
    "placeholder", "default", "1x1", "pixel", ".svg", ".gif",
    "shutterstock", "istockphoto", "gettyimages", "alamy", "dreamstime",
    "123rf", "depositphotos", "doubleclick", "tripadvisor-static",
    "finelib", "restaurantguru", "clipart", "vector",
)

_GENERIC_TITLE_STOPWORDS = {
    "the", "and", "for", "with", "from", "best", "top", "near", "here",
    "restaurant", "restaurants", "food", "place", "places", "spot", "spots",
    "eat", "eating", "abuja", "gwagwalada", "nigeria", "phase", "morning",
    "date", "romantic", "guide", "list", "review", "reviews",
}

_FOREIGN_CITY_NOISE = {
    "london", "delhi", "mumbai", "bangalore", "dubai", "paris", "tokyo",
    "manhattan", "brooklyn", "chicago", "los angeles", "toronto", "sydney",
    "singapore", "bangkok",
}

_IGNORED_PLACE_CAPTURE = {
    "me", "here", "this morning", "morning", "today", "tonight", "now",
    "my area", "my location", "a date", "date", "town", "city", "area",
    "last", "recent", "past", "months", "month", "weeks", "week", "days", "years",
}

_NEWS_KEYWORDS = {
    "news", "latest", "recent", "recently", "last month", "last 2 months",
    "last two months", "past month", "this week", "today", "trending",
    "scam", "scams", "hack", "hacks", "breach", "exploit", "incident",
    "happened", "update", "updates", "report", "reports", "2026", "rug pull",
}


def init_research_board(root: Any) -> VrResearchController:
    """Initializes the global PySide6 VR Research controller."""
    global _RESEARCH_CONTROLLER
    _RESEARCH_CONTROLLER = VrResearchController(
        root,
        on_search_cb=lambda q: web_research(q, mode="deep_search"),
    )
    return _RESEARCH_CONTROLLER


def get_research_controller() -> VrResearchController:
    """Returns active controller, lazily creating headless controller if needed."""
    global _RESEARCH_CONTROLLER
    if _RESEARCH_CONTROLLER is None:
        _RESEARCH_CONTROLLER = VrResearchController(
            None,
            on_search_cb=lambda q: web_research(q, mode="deep_search"),
        )
    return _RESEARCH_CONTROLLER


def _push_step(ctrl: VrResearchController, status_text: str, step_line: str) -> None:
    _ACTIVE_STEPS.append(step_line)
    ctrl.push_step(status_text, list(_ACTIVE_STEPS))


def _analyze_query(query: str, mode: str) -> dict[str, Any]:
    q = (query or "").strip()
    q_low = q.lower()
    mode_low = (mode or "deep_search").strip().lower()

    is_place = mode_low in ("places", "local") or any(k in q_low for k in _PLACE_KEYWORDS)
    is_date = any(k in q_low for k in _DATE_KEYWORDS)
    is_comparison = any(w in q_low for w in (" vs ", "compare ", "comparison", "difference between"))
    is_news = (not is_place) and any(k in q_low for k in _NEWS_KEYWORDS)

    cfg = get_configured_location()
    neighborhood = str(cfg.get("city") or "Abuja").strip()
    display_full = str(cfg.get("display") or neighborhood).strip()
    display_parts = [p.strip() for p in display_full.split(",") if p.strip()]
    metro = display_parts[1] if len(display_parts) >= 2 else neighborhood
    area_short = re.sub(r"\b(phase|estate|zone|area|district)\b.*$", "", neighborhood, flags=re.I).strip(" ,-") or neighborhood
    lat = cfg.get("lat")
    lon = cfg.get("lon")
    location_label = (
        (f"{neighborhood}, {metro}" if metro.lower() not in neighborhood.lower() else display_full)
        if is_place
        else "Global Web & News"
    )

    # Check if query names an explicit different district/city (only for place/local intents)
    if is_place:
        m_loc = re.search(r"\b(?:in|at|around|near)\s+([A-Za-z][A-Za-z0-9\s\-]{2,28})$", q.strip(), re.I)
        if not m_loc:
            m_loc = re.search(r"\b(?:in|at|around|near)\s+([A-Za-z][A-Za-z0-9\-]{2,20}(?:\s+[A-Za-z0-9]{1,12})?)\b", q.strip(), re.I)
        if m_loc:
            cand_loc = m_loc.group(1).strip(" .,?!")
            cand_low = cand_loc.lower()
            if cand_low not in _IGNORED_PLACE_CAPTURE and not any(bad in cand_low for bad in ("morning", "breakfast", "dinner", "lunch", "night", "date", "eat", "month", "week", "last")):
                geo = _geocode_city(cand_loc)
                if geo:
                    neighborhood = geo["city"]
                    area_short = geo["city"]
                    parts = [p.strip() for p in str(geo.get("display") or "").split(",") if p.strip()]
                    metro = parts[1] if len(parts) >= 2 else neighborhood
                    location_label = geo["display"]
                    lat, lon = geo["lat"], geo["lon"]

    mode_label = (
        "DATE & HANGOUT SPOTS" if is_date else (
            "LOCAL PLACES & DINING" if is_place else (
                "COMPARISON ANALYSIS" if is_comparison else (
                    "LATEST NEWS & BRIEFINGS" if is_news else "WEB INTELLIGENCE"
                )
            )
        )
    )

    return {
        "query": q,
        "query_low": q_low,
        "is_place": is_place,
        "is_date": is_date,
        "is_comparison": is_comparison,
        "is_news": is_news,
        "neighborhood": neighborhood,
        "area_short": area_short,
        "metro": metro,
        "location_label": location_label,
        "lat": float(lat) if lat is not None else 9.05785,
        "lon": float(lon) if lon is not None else 7.49508,
        "mode_label": mode_label,
    }


def _strip_time_filler(query: str) -> str:
    cleaned = re.sub(
        r"\b(?:in the |over the )?(?:last|past)\s+\d*\s*(?:months?|weeks?|days?|years?)\b",
        " ",
        query,
        flags=re.I,
    )
    cleaned = re.sub(r"\b(?:recently|latest news|news|breaking)\b", " ", cleaned, flags=re.I)
    return re.sub(r"\s+", " ", cleaned).strip()


def _build_search_queries(ctx: dict[str, Any]) -> list[str]:
    q = ctx["query"]
    q_low = ctx["query_low"]
    if ctx["is_news"]:
        core = _strip_time_filler(q) or q
        queries = [
            core,
            f"{core} major incidents scams 2026" if "crypto" in q_low or "scam" in q_low else f"{core} 2026 update",
            f"{core} October November 2026",
        ]
        out: list[str] = []
        for item in queries:
            norm = re.sub(r"\s+", " ", item).strip()
            if norm and norm.lower() not in [x.lower() for x in out]:
                out.append(norm)
        return out[:3]

    if not ctx["is_place"]:
        return [q, f"{q} details overview 2026"]

    nb = ctx["neighborhood"]
    area = ctx["area_short"]
    metro = ctx["metro"]

    if ctx["is_date"]:
        queries = [
            f"best romantic date night restaurants lounges {metro}",
            f"nice restaurants cafes chill spots {area} {metro}",
            f"top outdoor fine dining date spots {metro}",
        ]
    elif any(w in q_low for w in ("breakfast", "morning", "coffee", "bakery", "pastry", "brunch")):
        queries = [
            f"best breakfast restaurants cafes in {nb} {metro}",
            f"restaurants open morning eat in {area} {metro}",
            f"top breakfast brunch spots {metro}",
        ]
    else:
        clean_topic = re.sub(r"\b(near me|around me|around here|nearby)\b", "", q_low).strip()
        base = clean_topic if len(clean_topic) >= 4 else "restaurants places to eat"
        queries = [
            f"{base} in {nb} {metro}",
            f"best restaurants places to eat {area} {metro}",
            f"top rated dining spots {metro}",
        ]

    # Deduplicate cleanly
    out: list[str] = []
    for item in queries:
        norm = re.sub(r"\s+", " ", item).strip()
        if norm and norm.lower() not in [x.lower() for x in out]:
            out.append(norm)
    return out[:3]


def _search_single_query(q: str, max_per_query: int, is_news: bool = False) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    if is_news:
        try:
            news_rows = list(DDGS(timeout=6).news(q, max_results=max(8, max_per_query), timelimit="m") or [])
        except Exception:
            news_rows = []
        for r in news_rows:
            url = str(r.get("url") or r.get("href") or "").strip()
            title = str(r.get("title") or "").strip()
            snippet = str(r.get("body") or r.get("snippet") or "").strip()
            if not url or not title:
                continue
            domain = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
            raw_date = str(r.get("date") or "")[:10]
            hits.append({
                "title": title,
                "url": url,
                "snippet": snippet,
                "domain": str(r.get("source") or domain),
                "date": raw_date,
                "news_image": str(r.get("image") or "").strip(),
                "from_query": q,
            })

    for backend in ("yahoo", "google"):
        try:
            rows = list(DDGS(timeout=5).text(q, max_results=max_per_query, backend=backend) or [])
        except Exception:
            rows = []
        if not rows:
            continue
        for r in rows:
            url = str(r.get("href") or r.get("url") or "").strip()
            title = str(r.get("title") or "").strip()
            snippet = str(r.get("body") or r.get("snippet") or "").strip()
            if not url or not title:
                continue
            if any(bad in url.lower() for bad in ("facebook.com/login", "instagram.com/accounts", "doubleclick.net")):
                continue
            domain = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
            hits.append({
                "title": title,
                "url": url,
                "snippet": snippet,
                "domain": domain,
                "date": "",
                "news_image": "",
                "from_query": q,
            })
        if len(hits) >= max_per_query:
            break
    return hits


def _ddgs_text_search(queries: list[str], max_per_query: int = 6, is_news: bool = False) -> list[dict[str, str]]:
    if DDGS is None or not queries:
        return []

    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    with ThreadPoolExecutor(max_workers=min(3, len(queries))) as pool:
        futs = [pool.submit(_search_single_query, q, max_per_query, is_news and idx < 2) for idx, q in enumerate(queries)]
        for fut in futs:
            try:
                for item in fut.result():
                    if item["url"] not in seen_urls:
                        seen_urls.add(item["url"])
                        results.append(item)
            except Exception:
                pass
    # Sort dated recent stories first when news mode is active
    if is_news:
        results.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
    return results[:15]


def _scrape_one_page(url: str) -> dict[str, str]:
    info = {"url": url, "text": "", "og_image": ""}
    try:
        resp = requests.get(url, headers=_HTTP_HEADERS, timeout=4.5, allow_redirects=True)
        resp.raise_for_status()
        if "text/html" not in (resp.headers.get("Content-Type") or "").lower():
            return info
        soup = BeautifulSoup(resp.text, "html.parser")
        for meta_sel in (
            {"property": "og:image"},
            {"name": "twitter:image"},
            {"property": "og:image:url"},
        ):
            tag = soup.find("meta", attrs=meta_sel)
            if tag and tag.get("content"):
                cand = urllib.parse.urljoin(resp.url, str(tag["content"]).strip())
                if cand.startswith("http") and not any(b in cand.lower() for b in _BAD_IMG_WORDS):
                    info["og_image"] = cand
                    break

        for bad in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
            bad.decompose()
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in soup.get_text("\n").splitlines()]
        clean = "\n".join(ln for ln in lines if len(ln) >= 18)
        info["text"] = clean[:2400]
    except Exception:
        pass
    return info


def _scrape_pages_parallel(results: list[dict[str, str]], limit: int = 8) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    targets = [r["url"] for r in results[:limit] if r.get("url")]
    # Preserve news-feed thumbnail URLs directly alongside scraped og:images
    news_imgs = {r["url"]: r.get("news_image", "") for r in results if r.get("url") and r.get("news_image")}
    if not targets:
        return out
    pool = ThreadPoolExecutor(max_workers=min(6, len(targets)))
    try:
        futs = {pool.submit(_scrape_one_page, u): u for u in targets}
        for fut in as_completed(futs, timeout=7):
            try:
                res = fut.result()
                if not res.get("og_image") and news_imgs.get(res["url"]):
                    res["og_image"] = news_imgs[res["url"]]
                out[res["url"]] = res
            except Exception:
                pass
    except Exception:
        pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    for u, img in news_imgs.items():
        if u not in out and img:
            out[u] = {"url": u, "text": "", "og_image": img}
    return out


def _synthesize_with_gemini(
    ctx: dict[str, Any],
    results: list[dict[str, str]],
    scraped: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    api_key = get_app_config_value("gemini_api_key", "")
    if not api_key or not results:
        return None

    try:
        from google.genai import types  # type: ignore
    except Exception:
        return None

    evidence_blocks: list[str] = []
    for idx, r in enumerate(results[:12], 1):
        page_txt = (scraped.get(r["url"]) or {}).get("text", "")[:950]
        date_tag = f"Date: {r['date']}\n" if r.get("date") else ""
        evidence_blocks.append(
            f"[{idx}] Title: {r['title']}\n{date_tag}URL: {r['url']}\nSource: {r['domain']}\n"
            f"Snippet: {r['snippet']}\nPage Extract:\n{page_txt}"
        )

    if ctx["is_place"]:
        place_guidance = (
            f"The user is currently in {ctx['location_label']}. "
            f"Extract 5 to 6 REAL, specific named restaurants / eateries / breakfast spots / date venues found in the evidence "
            f"or well-verified around {ctx['area_short']} and {ctx['metro']}. "
            "Make sure each card is ONE specific real place, NOT a generic directory page title like '10 Best Restaurants'. "
            "Label `area` accurately (e.g. 'Gwagwalada, Abuja' vs 'Wuse II, Abuja (~40m drive)'), set `is_place: true`, and `show_chart: false`."
        )
    elif ctx["is_news"]:
        place_guidance = (
            "This is a RECENT NEWS & INCIDENT RESEARCH query. Return 6 DISTINCT cards (`items` length = 6) covering SEPARATE "
            "specific stories, attacks, arrests, hacks, scams, or reports found in the live news evidence! "
            "Do NOT collapse multiple stories into 2-3 generic cards! "
            "Prioritize recent dated stories (Sep/Oct/Nov 2026). For EACH card: put the exact date + source in `area` (e.g. 'Nov 12, 2026'), "
            "include concrete dollar amounts, platforms/actors named, and 3 informative detail bullets (`What happened`, `Key amounts & names involved`, `Security impact / takeaway`). "
            "MUST set `is_place: false` and `maps_query: \"\"` on all news cards! Set `show_chart` to true only if comparing real reported dollar/metric numbers from the stories."
        )
    else:
        place_guidance = (
            "Return 5 to 6 crisp, detailed cards grounded strictly in the live evidence with 3 specific bullets per card. "
            "MUST set `is_place: false` and `maps_query: \"\"` since this is not a physical venue lookup. "
            "Set `show_chart` to true ONLY if comparing genuine numbers/specs/prices."
        )

    sample_is_place = "true" if ctx["is_place"] else "false"
    sample_area = "Gwagwalada, Abuja" if ctx["is_place"] else "Nov 2026 • CoinCentral"
    prompt = (
        f"User Query: {ctx['query']}\n"
        f"User Active Location: {ctx['location_label']}\n"
        f"Category: {ctx['mode_label']}\n\n"
        f"Guidance: {place_guidance}\n\n"
        "Return strict JSON matching this exact schema (MUST include 5-6 distinct cards in `items` whenever evidence supports it):\n"
        "{\n"
        '  "answer": "Clear, warm, plain-English 2-4 sentence executive briefing answering the Boss with key names, numbers & takeaways.",\n'
        '  "highlights": ["Specific takeaway 1", "Specific takeaway 2", "Specific takeaway 3"],\n'
        '  "show_chart": false,\n'
        '  "chart_title": "",\n'
        '  "chart_bars": [{"label": "Name", "pct": 85, "display": "value"}],\n'
        '  "items": [\n'
        "    {\n"
        '      "title": "Exact Place Name or Specific News Headline / Event",\n'
        f'      "area": "{sample_area}",\n'
        '      "badge": "Breakfast / Quick Eat" (or "Crypto Hack" / "Fraud Alert" / "Report"),\n'
        '      "subtitle": "Key entity • Amount / Cuisine • Vibe or Core Topic",\n'
        '      "bullets": ["Detailed point 1 with exact facts/figures", "Detailed point 2", "Detailed point 3"],\n'
        '      "source": "domain.com",\n'
        '      "url": "exact supporting URL from evidence",\n'
        '      "image_query": "Specific photo search phrase for THIS exact story/place only",\n'
        '      "food_photo_query": "Authentic dish/vibe photo query (or topic visual for news)",\n'
        '      "maps_query": "",\n'
        f'      "is_place": {sample_is_place}\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "LIVE WEB & NEWS EVIDENCE:\n" + "\n\n---\n\n".join(evidence_blocks)
    )

    try:
        resp = generate_gemini_content(
            api_key,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
            timeout_ms=16000,
        )
        raw_txt = (getattr(resp, "text", "") or "").strip()
        if raw_txt.startswith("```"):
            raw_txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_txt, flags=re.S)
        data = json.loads(raw_txt)
        if not isinstance(data, dict) or not data.get("items"):
            return None
        if ctx["is_place"]:
            data["show_chart"] = False
        else:
            for it in data.get("items") or []:
                if isinstance(it, dict):
                    it["is_place"] = False
                    it["maps_query"] = ""
        return data
    except Exception:
        return None


def _build_fallback_results(ctx: dict[str, Any], results: list[dict[str, str]]) -> dict[str, Any]:
    """Honest structured fallback directly from live web results (never fake bars)."""
    items: list[dict[str, Any]] = []
    for r in results[:6]:
        title = r["title"].split(" - ")[0].split(" | ")[0].strip()[:64] or r["title"][:64]
        items.append({
            "title": title,
            "area": ctx["location_label"] if ctx["is_place"] else r["domain"],
            "badge": "Date / Dining Guide" if ctx["is_date"] else ("Local Food Source" if ctx["is_place"] else "Web Source"),
            "subtitle": r["domain"],
            "bullets": [r["snippet"][:180]] if r.get("snippet") else ["Verified search result"],
            "source": r["domain"],
            "url": r["url"],
            "image_query": f"{title} {ctx['area_short']} {'restaurant food' if ctx['is_place'] else ''}".strip(),
            "maps_query": f"{title}, {ctx['location_label']}",
            "is_place": bool(ctx["is_place"]),
        })

    answer = (
        f"Here are live web listings around {ctx['location_label']} for \"{ctx['query']}\". "
        "Open any card below to view full details or launch Google Maps directions."
        if results
        else f"No live web results returned right now for \"{ctx['query']}\" — check your connection or click 'Open in Chrome'."
    )
    return {
        "answer": answer,
        "highlights": [
            f"Searched around active location: {ctx['location_label']}",
            f"Found {len(results)} web sources",
        ],
        "show_chart": False,
        "chart_title": "",
        "chart_bars": [],
        "items": items,
    }


def _distinctive_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    return {w for w in words if w not in _GENERIC_TITLE_STOPWORDS}


def _score_image_candidate(
    item: dict[str, Any],
    ctx: dict[str, Any],
    cand_title: str,
    cand_url: str,
    *,
    allow_cuisine_fallback: bool = True,
) -> int:
    """Returns match score >= 1 for relevant photos, or 0 to reject mismatched images."""
    blob = f"{cand_title} {cand_url}".lower()
    if any(b in blob for b in _BAD_IMG_WORDS):
        return 0

    q_low = ctx["query_low"]
    for fc in _FOREIGN_CITY_NOISE:
        if fc in blob and fc not in q_low and fc not in ctx["location_label"].lower():
            return 0

    # Reject luxury fine-dining stock mismatches on local breakfast/casual food cards
    title_low = str(item.get("title") or "").lower()
    sub_low = str(item.get("subtitle") or "").lower()
    if any(w in (title_low + " " + sub_low) for w in ("akara", "yam", "suya", "bukka", "amala", "local", "confectioner", "bakery", "casual")):
        if any(bad in blob for bad in ("wine", "champagne", "cocktail", "candlelight", "wedding")):
            return 0

    title_tokens = _distinctive_tokens(title_low)
    matched_title = sum(1 for tok in title_tokens if tok in blob)
    if matched_title >= 1:
        return 10 + matched_title * 5

    # Dish / cuisine keyword fallback for places with zero named Google Images (e.g., neighborhood eateries)
    food_words = {
        "akara", "yam", "suya", "jollof", "amala", "pastry", "pastries", "bakery",
        "shawarma", "pizza", "breakfast", "rice", "soup", "pepper", "chicken",
        "grill", "bistro", "cafe", "confectionery", "dining", "nigerian", "egusi",
        "plantain", "spaghetti", "snack", "snacks", "meal", "food", "restaurant",
    }
    card_food_tokens = _distinctive_tokens(
        f"{item.get('subtitle', '')} {item.get('badge', '')} {item.get('food_photo_query', '')}"
    ) & food_words

    if allow_cuisine_fallback and ctx["is_place"]:
        if card_food_tokens and any(tok in blob for tok in card_food_tokens):
            return 6
        if any(w in blob for w in ("nigeria", "nigerian", "abuja", "jollof", "suya", "akara", "food", "meal", "dish", "plate", "restaurant", "dining", "cafe", "bakery")):
            return 3
        return 0

    if ctx["is_place"]:
        return 0

    q_tokens = _distinctive_tokens(f"{ctx['query']} {item.get('subtitle', '')}")
    return 5 if (q_tokens and any(tok in blob for tok in q_tokens)) else 0


def _is_image_relevant(item: dict[str, Any], ctx: dict[str, Any], cand_title: str, cand_url: str) -> bool:
    return _score_image_candidate(item, ctx, cand_title, cand_url, allow_cuisine_fallback=True) > 0


def _download_and_verify_photo(url: str) -> bytes | None:
    try:
        resp = requests.get(url, headers=_HTTP_HEADERS, timeout=4.5, stream=True)
        resp.raise_for_status()
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "svg" in ctype or ("image" not in ctype and ctype != "application/octet-stream"):
            return None
        chunks = []
        total = 0
        for ch in resp.iter_content(chunk_size=16384):
            chunks.append(ch)
            total += len(ch)
            if total > 2_500_000:
                break
        raw = b"".join(chunks)
        with Image.open(io.BytesIO(raw)) as im:
            w, h = im.size
            if w < 180 or h < 110:
                return None
            aspect = w / float(h)
            if aspect < 0.45 or aspect > 3.3:
                return None
        return raw
    except Exception:
        return None


def _resolve_one_card_image(
    idx: int,
    item: dict[str, Any],
    ctx: dict[str, Any],
    scraped: dict[str, dict[str, str]],
    used_urls: set[str],
) -> tuple[int, bytes | None]:
    # 1. Try card URL's og:image only when it's a specific venue page (never generic city directory pages)
    item_url = str(item.get("url") or "").strip()
    is_directory_page = any(
        d in item_url.lower()
        for d in ("restaurantguru", "finelib", "tripadvisor", "yelp", "wakaabuja", "best-", "top-", "restaurants-in", "instagram.com/popular")
    )
    og = "" if is_directory_page else (scraped.get(item_url) or {}).get("og_image", "")
    if og and not any(b in og.lower() for b in _BAD_IMG_WORDS):
        if _is_image_relevant(item, ctx, item.get("title", ""), og):
            with _USED_IMG_LOCK:
                already = og in used_urls
                if not already:
                    used_urls.add(og)
            if not already:
                raw = _download_and_verify_photo(og)
                if raw:
                    return idx, raw

    # 2. Dedicated DDGS image search scoped strictly to THIS card
    if DDGS is None:
        return idx, None

    title = str(item.get("title") or "").strip()
    area = str(item.get("area") or ctx["area_short"]).split("(")[0].strip()

    # Pass 1: Strict place/product search (high score when exact name appears)
    pass1_phrases: list[str] = []
    if item.get("image_query"):
        pass1_phrases.append(str(item["image_query"]).strip())
    if title:
        pass1_phrases.append(f"{title} {area}".strip())

    # Pass 2: Authentic dish/cuisine/vibe fallback for local joints with no indexed name photos
    pass2_phrases: list[str] = []
    if item.get("food_photo_query"):
        pass2_phrases.append(str(item["food_photo_query"]).strip())
    elif ctx["is_place"]:
        sub_clean = re.sub(r"[•|]+", " ", str(item.get("subtitle") or "")).strip()
        badge_clean = str(item.get("badge") or "").strip()
        fallback_q = f"Nigerian food {sub_clean} {badge_clean}".strip()
        pass2_phrases.append(re.sub(r"\s+", " ", fallback_q)[:70] or "Nigerian restaurant meal dish")
    elif title:
        pass2_phrases.append(title)

    def _try_phrases(phrases: list[str], allow_cuisine_fallback: bool) -> bytes | None:
        seen_p: set[str] = set()
        for phrase in phrases:
            norm_p = phrase.lower()
            if not phrase or norm_p in seen_p:
                continue
            seen_p.add(norm_p)
            try:
                imgs = list(DDGS(timeout=5).images(phrase, max_results=6) or [])
            except Exception:
                imgs = []
            scored: list[tuple[int, str]] = []
            for cand in imgs:
                img_url = str(cand.get("image") or "").strip()
                img_title = str(cand.get("title") or "").strip()
                sc = _score_image_candidate(
                    item, ctx, img_title, img_url, allow_cuisine_fallback=allow_cuisine_fallback
                )
                if img_url and sc > 0:
                    scored.append((sc, img_url))
            scored.sort(key=lambda x: x[0], reverse=True)
            for _sc, img_url in scored:
                with _USED_IMG_LOCK:
                    if img_url in used_urls:
                        continue
                    used_urls.add(img_url)
                raw = _download_and_verify_photo(img_url)
                if raw:
                    return raw
        return None

    raw = _try_phrases(pass1_phrases[:1], allow_cuisine_fallback=False)
    if raw:
        return idx, raw
    raw = _try_phrases(pass2_phrases[:1], allow_cuisine_fallback=True)
    return idx, raw


def _resolve_card_images_parallel(
    items: list[dict[str, Any]],
    ctx: dict[str, Any],
    scraped: dict[str, dict[str, str]],
    ctrl: VrResearchController | None = None,
) -> None:
    if not items:
        return
    used_urls: set[str] = set()
    pool = ThreadPoolExecutor(max_workers=min(6, len(items)))
    try:
        futs = [
            pool.submit(_resolve_one_card_image, idx, item, ctx, scraped, used_urls)
            for idx, item in enumerate(items)
        ]
        for fut in as_completed(futs, timeout=14):
            try:
                idx, raw = fut.result()
                if raw and 0 <= idx < len(items):
                    items[idx]["image_bytes"] = raw
                    if ctrl is not None:
                        ctrl.update_card_image(idx, raw)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _deg2num_float(lat_deg: float, lon_deg: float, zoom: int) -> tuple[float, float]:
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = (lon_deg + 180.0) / 360.0 * n
    ytile = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return xtile, ytile


def _fetch_local_map_bytes(lat: float, lon: float, area_label: str, zoom: int = 13) -> bytes | None:
    """Downloads a crisp dark CartoDB local map around (lat, lon) with glowing cyan pin."""
    try:
        xf, yf = _deg2num_float(lat, lon, zoom)
        cx, cy = int(math.floor(xf)), int(math.floor(yf))
        frac_x, frac_y = xf - cx, yf - cy

        # Stitch 3x2 grid around center tile so crop center aligns cleanly on pin
        canvas = Image.new("RGB", (768, 512), (4, 14, 30))
        offsets = [(-1, -1), (0, -1), (1, -1), (-1, 0), (0, 0), (1, 0)]
        tiles_ok = 0

        def _get_tile(ox: int, oy: int) -> tuple[int, int, Image.Image | None]:
            tx = (cx + ox) % (2 ** zoom)
            ty = max(0, min((2 ** zoom) - 1, cy + oy))
            base_url = f"https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{zoom}/{ty}/{tx}"
            ref_url = f"https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{zoom}/{ty}/{tx}"
            street_url = f"https://services.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{zoom}/{ty}/{tx}"
            try:
                resp = requests.get(base_url, headers={"User-Agent": "ROSE-Windows/2.0"}, timeout=5)
                if resp.status_code == 200 and resp.content:
                    base_im = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                    try:
                        ref_resp = requests.get(ref_url, headers={"User-Agent": "ROSE-Windows/2.0"}, timeout=4)
                        if ref_resp.status_code == 200 and ref_resp.content:
                            ref_im = Image.open(io.BytesIO(ref_resp.content)).convert("RGBA")
                            base_im = Image.alpha_composite(base_im, ref_im)
                    except Exception:
                        pass
                    return ox, oy, base_im.convert("RGB")
            except Exception:
                pass
            try:
                resp2 = requests.get(street_url, headers={"User-Agent": "ROSE-Windows/2.0"}, timeout=5)
                if resp2.status_code == 200 and resp2.content:
                    return ox, oy, Image.open(io.BytesIO(resp2.content)).convert("RGB")
            except Exception:
                pass
            return ox, oy, None

        with ThreadPoolExecutor(max_workers=6) as pool:
            for ox, oy, tile_im in pool.map(lambda xy: _get_tile(xy[0], xy[1]), offsets):
                if tile_im is not None:
                    canvas.paste(tile_im, ((ox + 1) * 256, (oy + 1) * 256))
                    tiles_ok += 1

        if tiles_ok == 0:
            return None

        pin_x = int((1 + frac_x) * 256)
        pin_y = int((1 + frac_y) * 256)
        crop_w, crop_h = 460, 270
        left = max(0, min(768 - crop_w, pin_x - crop_w // 2))
        top = max(0, min(512 - crop_h, pin_y - crop_h // 2))
        cropped = canvas.crop((left, top, left + crop_w, top + crop_h))

        local_px = pin_x - left
        local_py = pin_y - top
        draw = ImageDraw.Draw(cropped, "RGBA")
        # Outer radar ring & center pin
        draw.ellipse((local_px - 34, local_py - 34, local_px + 34, local_py + 34), outline=(38, 230, 255, 130), width=2)
        draw.ellipse((local_px - 18, local_py - 18, local_px + 18, local_py + 18), fill=(38, 230, 255, 55), outline=(38, 230, 255, 220), width=2)
        draw.ellipse((local_px - 6, local_py - 6, local_px + 6, local_py + 6), fill=(34, 240, 138, 255), outline=(255, 255, 255, 255), width=2)

        tag_txt = (area_label or "ACTIVE AREA")[:32]
        draw.rounded_rectangle((10, crop_h - 32, min(crop_w - 10, 22 + len(tag_txt) * 7), crop_h - 8), radius=6, fill=(4, 14, 30, 210), outline=(38, 230, 255, 180))
        draw.text((18, crop_h - 26), tag_txt, fill=(234, 247, 255, 255))

        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _read_url_direct(url: str, ctrl: VrResearchController) -> str:
    page = _scrape_one_page(url)
    title = urllib.parse.urlparse(url).netloc or url
    text = page.get("text") or "Could not extract readable text from that URL."
    img_bytes = _download_and_verify_photo(page["og_image"]) if page.get("og_image") else None
    item = {
        "title": title,
        "area": "Direct URL",
        "badge": "Web Page",
        "subtitle": url[:64],
        "bullets": [text[:240]],
        "source": title,
        "url": url,
        "image_bytes": img_bytes,
        "is_place": False,
    }
    ctrl.apply_results(
        {
            "answer": text[:420],
            "highlights": [f"Loaded page: {url}"],
            "show_chart": False,
        },
        [item],
        [f"✓ Loaded URL directly: {url}"],
    )
    return f"Loaded {title}: {text[:350]}"


def web_research(query: str = "", mode: str = "deep_search", url: str = "") -> str:
    """
    Opens R.O.S.E's interactive PySide6 Visual Search ('VR Search') HUD,
    displays live step-by-step searching, discovers local restaurants / date spots
    around the active neighborhood/city, matches per-item photos, and returns
    a crisp conversational summary for R.O.S.E to speak.
    """
    ctrl = get_research_controller()
    mode_norm = (mode or "deep_search").strip().lower()

    if mode_norm == "close":
        ctrl.close()
        return "Closed the Visual Search HUD."

    if mode_norm == "read_url" and (url or "").strip():
        return _read_url_direct(url.strip(), ctrl)

    effective_query = (query or "").strip() or "trending restaurants and hotspots"
    ctx = _analyze_query(effective_query, mode_norm)

    _ACTIVE_STEPS.clear()
    ctrl.start_research(effective_query, ctx["location_label"], ctx["mode_label"], ctx["is_place"])

    # Step 1: Location & Queries
    search_queries = _build_search_queries(ctx)
    _push_step(
        ctrl,
        f"● Step 2: Querying Google, Yahoo & News for {len(search_queries)} searches...",
        (
            f"✓ Step 1: Location locked → {ctx['location_label']} ({ctx['lat']:.3f}°N, {ctx['lon']:.3f}°E)"
            if ctx["is_place"]
            else f"✓ Step 1: Search mode → {ctx['mode_label']}"
        ),
    )
    _push_step(
        ctrl,
        "● Step 2: Scanning live web & recent news...",
        "● Step 2: Searching → " + " | ".join(f'"{sq}"' for sq in search_queries[:2]),
    )

    # Run web/news search + map generation concurrently
    map_holder: dict[str, bytes | None] = {"bytes": None}
    with ThreadPoolExecutor(max_workers=2) as pool:
        text_fut = pool.submit(_ddgs_text_search, search_queries, 6, bool(ctx["is_news"]))
        map_fut = (
            pool.submit(_fetch_local_map_bytes, ctx["lat"], ctx["lon"], ctx["location_label"], 13)
            if ctx["is_place"]
            else None
        )
        results = text_fut.result()
        if map_fut is not None:
            map_holder["bytes"] = map_fut.result()

    if ctx["is_place"]:
        ctrl.set_map(
            map_holder["bytes"],
            ctx["location_label"],
            f"{ctx['lat']:.3f}°N, {ctx['lon']:.3f}°E",
        )

    _push_step(
        ctrl,
        f"● Step 3: Found {len(results)} sources — reading articles & extracting details...",
        f"✓ Step 2: Retrieved {len(results)} live {'news & web' if ctx['is_news'] else 'web'} listings",
    )

    # Step 3: Scrape top 8 pages cleanly so stories & places have deep details
    scraped = _scrape_pages_parallel(results, limit=8)

    # Step 4: Structure clean cards & plain-English answer
    _push_step(
        ctrl,
        "● Step 4: Organizing top picks & matching verified photos per card...",
        f"✓ Step 3: Read {len(scraped)} web pages",
    )
    board_data = _synthesize_with_gemini(ctx, results, scraped)
    if not board_data:
        board_data = _build_fallback_results(ctx, results)

    items = list(board_data.get("items") or [])[:6]
    base_steps = list(_ACTIVE_STEPS)

    # Show cards + answer + map IMMEDIATELY so Boss sees results at ~7-11s
    # while per-card photos stream live in a background thread (never hitting 15s tool timeout!).
    base_steps.append(f"● Step 4: Loading verified photos for {len(items)} cards...")
    ctrl.apply_results(board_data, items, base_steps)

    def _stream_photos_worker() -> None:
        try:
            _resolve_card_images_parallel(items, ctx, scraped, ctrl=ctrl)
        except Exception:
            pass
        matched_cnt = sum(1 for it in items if it.get("image_bytes"))
        done_steps = list(base_steps[:-1]) + [
            f"✓ Ready: Showing {len(items)} results ({matched_cnt} matched photos)"
            + (f" in {ctx['location_label']}" if ctx["is_place"] else " across live web & news")
        ]
        ctrl.push_step(
            f"✓ Search Complete — {len(items)} results ({matched_cnt} verified photos)",
            done_steps,
        )

    img_thread = threading.Thread(target=_stream_photos_worker, name="ROSE-VRPhotoLoader", daemon=True)
    img_thread.start()
    # Under live PySide6 GUI, give fast photos 3.0s to settle before Gemini speaks, then stream the rest live;
    # under headless/CLI tests, wait cleanly for photo completion.
    wait_budget = 3.0 if ctrl._can_use_qt() else 16.0
    img_thread.join(timeout=wait_budget)

    # Build concise spoken summary for ROSE
    names = [str(it.get("title") or "").strip() for it in items[:5] if it.get("title")]
    answer_txt = str(board_data.get("answer") or "").strip()
    names_line = f" Top picks shown on screen: {', '.join(names)}." if names else ""
    return f"{answer_txt}{names_line}".strip()
