"""
R.O.S.E (Project Mirror) - Business Brain Loader & Candid Business Advisor (Phase 7)
- Maintains starter templates in `business_brain/` (never overwrites user-filled files).
- Persona: Candid and analytical — challenges weak assumptions, points out risks and missing
  numbers, asks sharp questions, and proposes concrete next steps. Never acts like a cheerleader.
- Channel handling:
  * Text chat panel & Telegram receive the full detailed analytical breakdown.
  * Voice channel receives a short, punchy spoken summary while posting full details to the text panel.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional

from core.audit import audit_log
from core.logging_setup import get_logger
from core.permissions import Tier
from core.result import Result

logger = get_logger("business_advisor")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUSINESS_BRAIN_DIR = PROJECT_ROOT / "business_brain"

VENTURE_TEMPLATES: Dict[str, str] = {
    "me_and_goals.md": (
        "# Me & Goals (Founder Operating Brief)\n\n"
        "## Founder Constraints & Runway\n"
        "- **Primary Location / Timezone:** Abuja / Lagos, Nigeria (WAT, UTC+1)\n"
        "- **Available Weekly Hours Across Ventures:** TBD\n"
        "- **Monthly Personal Burn Rate (NGN / USD):** TBD\n"
        "- **Cash Runway (Months):** TBD\n\n"
        "## 12-Month Targets\n"
        "- **Monthly Net Cashflow Target:** TBD\n"
        "- **Priority #1 Revenue Engine:** TBD\n\n"
        "## Immediate 30-Day Priorities\n1. TBD\n2. TBD\n"
    ),
    "swiftsats.md": "# SwiftSats\n\n## Problem\n- TBD\n\n## Customers\n- TBD\n\n## Revenue Model\n- TBD\n\n## Status\n- TBD\n\n## Risks\n- TBD\n\n## Numbers\n- TBD\n\n## Next Steps\n1. TBD\n",
    "neareat.md": "# NearEat\n\n## Problem\n- TBD\n\n## Customers\n- TBD\n\n## Revenue Model\n- TBD\n\n## Status\n- TBD\n\n## Risks\n- TBD\n\n## Numbers\n- TBD\n\n## Next Steps\n1. TBD\n",
    "bountytrust.md": "# BountyTrust\n\n## Problem\n- TBD\n\n## Customers\n- TBD\n\n## Revenue Model\n- TBD\n\n## Status\n- TBD\n\n## Risks\n- TBD\n\n## Numbers\n- TBD\n\n## Next Steps\n1. TBD\n",
    "autonomous_governance_engine.md": "# Autonomous Governance Engine\n\n## Problem\n- TBD\n\n## Customers\n- TBD\n\n## Revenue Model\n- TBD\n\n## Status\n- TBD\n\n## Risks\n- TBD\n\n## Numbers\n- TBD\n\n## Next Steps\n1. TBD\n",
    "petroleum_supply_chain.md": "# Petroleum Supply Chain system\n\n## Problem\n- TBD\n\n## Customers\n- TBD\n\n## Revenue Model\n- TBD\n\n## Status\n- TBD\n\n## Risks\n- TBD\n\n## Numbers\n- TBD\n\n## Next Steps\n1. TBD\n",
    "mbo_webdev.md": "# M.B.O WebDev\n\n## Problem\n- TBD\n\n## Customers\n- TBD\n\n## Revenue Model\n- TBD\n\n## Status\n- TBD\n\n## Risks\n- TBD\n\n## Numbers\n- TBD\n\n## Next Steps\n1. TBD\n",
}

BUSINESS_ADVISOR_SYSTEM_PROMPT = (
    "You are R.O.S.E in Business Advisor Mode acting as a sharp, pragmatic COO/investor advisor.\n"
    "MANDATORY PERSONA:\n"
    "- Candid, analytical, direct, and grounded strictly in the provided business_brain/*.md files and real connector data.\n"
    "- Challenge weak assumptions (especially premature building without paying customers, multi-venture distraction, or unpriced unit economics).\n"
    "- Explicitly point out operational/financial/market risks and list concrete missing numbers (`TBD`, unverified demand, missing CAC/burn/conversion metrics).\n"
    "- Ask 2 to 4 sharp, uncomfortable questions that force clarity.\n"
    "- Suggest prioritized, verifiable next steps.\n"
    "- NEVER flatter, NEVER act like an empty cheerleader, and NEVER invent numbers not present in the files or metrics.\n"
)


def ensure_business_brain_templates(brain_dir: Optional[Path] = None) -> List[Path]:
    """Ensures `business_brain/` exists with starter templates cleanly without overwriting existing user files."""
    target_dir = brain_dir or BUSINESS_BRAIN_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    created: List[Path] = []
    for filename, default_body in VENTURE_TEMPLATES.items():
        fp = target_dir / filename
        if not fp.exists():
            fp.write_text(default_body, encoding="utf-8")
            created.append(fp)
    return created


def load_business_brain_context(
    venture: str = "",
    brain_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Reads all venture markdown files cleanly and computes missing-number (`TBD`) diagnostics."""
    target_dir = brain_dir or BUSINESS_BRAIN_DIR
    ensure_business_brain_templates(target_dir)

    files = sorted(target_dir.glob("*.md"))
    venture_norm = re.sub(r"[^a-z0-9]+", "", str(venture or "").lower())
    docs: List[Dict[str, Any]] = []
    total_tbd = 0

    for fp in files:
        content = fp.read_text(encoding="utf-8", errors="ignore").strip()
        stem_norm = re.sub(r"[^a-z0-9]+", "", fp.stem.lower())
        tbd_count = len(re.findall(r"\bTBD\b", content, flags=re.IGNORECASE))
        total_tbd += tbd_count
        title_match = re.match(r"^#\s+(.+)$", content, flags=re.MULTILINE)
        title = title_match.group(1).strip() if title_match else fp.stem
        docs.append({
            "filename": fp.name,
            "stem": fp.stem,
            "stem_norm": stem_norm,
            "title": title,
            "tbd_count": tbd_count,
            "content": content,
            "matched": bool(venture_norm and venture_norm in stem_norm),
        })

    # Reorder so matched venture + me_and_goals come first when focused on one venture
    if venture_norm:
        docs.sort(key=lambda d: (0 if d["matched"] else (1 if d["filename"] == "me_and_goals.md" else 2), d["filename"]))

    return {
        "directory": str(target_dir),
        "files_loaded": [d["filename"] for d in docs],
        "docs": docs,
        "total_tbd_count": total_tbd,
    }


def _summarize_spoken(full_analysis: str, venture_label: str = "") -> str:
    """Produces a crisp 2-3 sentence spoken summary without drowning voice audio in long bullet lists."""
    clean_lines = [
        re.sub(r"^#+\s*|^\s*[-*]\s*|\*\*", "", ln).strip()
        for ln in str(full_analysis or "").splitlines()
        if ln.strip() and not ln.strip().startswith(">")
    ]
    core_points: List[str] = []
    for ln in clean_lines:
        if any(k in ln.lower() for k in ("risk", "missing", "tbd", "question", "assumption", "next step", "priority", "swiftsats", "unconnected", "focus")):
            core_points.append(ln)
        if len(core_points) >= 2:
            break
    if not core_points and clean_lines:
        core_points = clean_lines[:2]

    prefix = f"Business Advisor brief{f' on {venture_label}' if venture_label else ''}: "
    body = " ".join(core_points)[:340].rstrip(".")
    return f"{prefix}{body}. I posted the full risk breakdown and questions in the text panel."


def _offline_analytical_critique(
    query: str,
    context: Dict[str, Any],
    swiftsats_note: str,
    venture: str = "",
) -> str:
    """Deterministic candid Business Advisor response used when Gemini key is absent/offline."""
    docs = context.get("docs") or []
    tbd_total = int(context.get("total_tbd_count") or 0)
    missing_rows = [f"`{d['title']}` ({d['tbd_count']} TBD placeholders)" for d in docs if d["tbd_count"] > 0]

    lines = [
        f"## Business Advisor Analysis{f' — {venture}' if venture else ''}",
        f"*Prompt examined:* \"{query.strip() or 'Portfolio & strategy review'}\"",
        "",
        "### Weak Assumptions & Strategy Challenge",
    ]
    if len(docs) >= 6 and any(d["filename"] == "me_and_goals.md" and d["tbd_count"] >= 4 for d in docs):
        lines.append(
            "- **Attention fragmentation hazard:** Your brain directory tracks 6 separate ventures plus job hunting, "
            "while `me_and_goals.md` still lacks weekly hour limits, monthly burn rate, and cash runway. Trying to push all six without hard kill criteria guarantees slow execution."
        )
    else:
        lines.append(
            "- **Assumption check:** Any milestone or build step not backed by paying users, signed letters of intent, or live unit economics should be treated as unvalidated hypothesis."
        )

    matched_docs = [d for d in docs if d["matched"]]
    focus_docs = matched_docs or docs[:3]
    for d in focus_docs:
        lines.append(f"- **{d['title']} focus check:** Verify customer willingness to pay before adding infrastructure complexity.")

    lines.extend([
        "",
        "### Risks & Missing Numbers",
        f"- **Unfilled metrics across templates:** {tbd_total} `TBD` gaps across {len(missing_rows)} files ({', '.join(missing_rows[:5])}). Missing unit economics, CAC, conversion, and churn blind strategic decisions.",
        f"- **SwiftSats Live Telemetry:** {swiftsats_note}",
        "",
        "### Sharp Questions to Answer Right Now",
        "1. Which ONE venture puts real cash into your bank account in the next 30 days (`SwiftSats` vs `M.B.O WebDev` retainers vs `NearEat`), and what gets explicitly paused until then?",
        "2. What is your exact monthly personal burn and remaining cash runway in months?",
        "3. How many real target customers paid or committed money this past 14 days — not compliments, actual payments?",
        "",
        "### Next Steps",
        "1. Open `business_brain/me_and_goals.md` and replace every `TBD` with hard numbers (burn, runway, hours/week, #1 revenue engine).",
        "2. Fill in real customer counts, transaction margins, and kill criteria in your active venture markdown file(s).",
        "3. Validate payment conversion with 5 real customers/prospects before writing any non-essential features.",
    ])
    return "\n".join(lines)


def advise_business(
    query: str,
    venture: str = "",
    channel: str = "chat",
    *,
    brain_dir: Optional[Path] = None,
    llm_fn: Optional[Callable[[str, str], str]] = None,
) -> Dict[str, Any]:
    """
    Runs Business Advisor Mode across `business_brain/*.md` + SwiftSats status.
    Returns `full_analysis` (detailed markdown for chat panel/Telegram) and `spoken_summary` (for voice).
    """
    ctx = load_business_brain_context(venture=venture, brain_dir=brain_dir)

    try:
        from integrations.swiftsats.client import NOT_CONNECTED_MSG, load_swiftsats_config

        ss_cfg = load_swiftsats_config()
        if ss_cfg.is_disabled:
            swiftsats_note = f"{NOT_CONNECTED_MSG} (`SWIFTSATS_MODE=disabled`)."
        elif ss_cfg.is_mock:
            swiftsats_note = "Connected in MOCK DATA mode (`SWIFTSATS_MODE=mock`; preview metrics only, not real customer revenue)."
        else:
            swiftsats_note = f"Configured in LIVE mode ({ss_cfg.base_url})."
    except Exception:
        swiftsats_note = "SwiftSats connector status unavailable."

    context_blocks = [
        f"### File: {d['filename']} (TBD count: {d['tbd_count']})\n{d['content'][:2200]}"
        for d in ctx["docs"]
    ]
    user_payload = (
        f"FOUNDER QUESTION ({channel}): {query}\n"
        f"SWIFTSATS CONNECTOR STATUS: {swiftsats_note}\n\n"
        f"BUSINESS BRAIN MARKDOWN FILES:\n\n" + "\n\n---\n\n".join(context_blocks)
    )

    full_analysis = ""
    if llm_fn is not None:
        full_analysis = str(llm_fn(BUSINESS_ADVISOR_SYSTEM_PROMPT, user_payload) or "").strip()
    else:
        try:
            from tools.declarations import get_api_key

            api_key = get_api_key()
            if api_key:
                from app_config import generate_gemini_content
                from google.genai import types

                resp = generate_gemini_content(
                    api_key,
                    contents=user_payload,
                    config=types.GenerateContentConfig(
                        system_instruction=BUSINESS_ADVISOR_SYSTEM_PROMPT,
                        temperature=0.3,
                    ),
                )
                full_analysis = (getattr(resp, "text", None) or "").strip()
        except Exception as exc:
            logger.warning(f"Business Advisor Gemini fallback ({type(exc).__name__}): using offline audit.")

    if not full_analysis:
        full_analysis = _offline_analytical_critique(query, ctx, swiftsats_note, venture=venture)

    spoken_summary = _summarize_spoken(full_analysis, venture_label=venture)

    audit_log(
        tool="business_advisor",
        args={"query": str(query)[:80], "venture": venture, "channel": channel, "files": len(ctx["files_loaded"])},
        tier=int(Tier.TIER_0_READ_ONLY),
        result=Result.success(verified=True, message="Business Advisor analysis delivered"),
        source=channel,
    )

    return {
        "ok": True,
        "channel": channel,
        "venture": venture,
        "files_loaded": ctx["files_loaded"],
        "total_tbd_count": ctx["total_tbd_count"],
        "swiftsats_note": swiftsats_note,
        "full_analysis": full_analysis,
        "spoken_summary": spoken_summary,
    }

