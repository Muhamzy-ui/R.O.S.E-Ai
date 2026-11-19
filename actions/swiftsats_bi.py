"""
SwiftSats Business Intelligence Module for R.O.S.E. (Updated Phase 7)
Delegates cleanly to `integrations/swiftsats/` read-only connector:
- Default `SWIFTSATS_MODE=disabled` -> returns "SwiftSats isn't connected yet" (NEVER fakes metrics).
- `SWIFTSATS_MODE=mock` -> generates synthetic metrics labeled "MOCK DATA" and saves markdown in `reports/`.
- `SWIFTSATS_MODE=live` -> queries read-only `/api/internal/rose-metrics/` with bearer token; NEVER touches `/admin` or writes state.
"""

from __future__ import annotations

from typing import Optional

from integrations.swiftsats.client import NOT_CONNECTED_MSG, load_swiftsats_config
from integrations.swiftsats.reports import generate_swiftsats_report
from integrations.swiftsats.security import security_check


def swiftsats_intelligence(
    query: str = "daily_report",
    window: str = "",
    repo_path: str = "",
) -> str:
    """
    Queries authorised SwiftSats business metrics (read-only), runs security checks if requested,
    and saves timestamped markdown reports in `reports/`.
    """
    cfg = load_swiftsats_config()
    low = str(query or "").strip().lower()

    if cfg.is_disabled:
        return (
            f"{NOT_CONNECTED_MSG} (SWIFTSATS_MODE=disabled). "
            "No fake numbers will be invented. Set SWIFTSATS_MODE=mock in .env for preview data, "
            "or SWIFTSATS_MODE=live with SWIFTSATS_BASE_URL and SWIFTSATS_API_TOKEN when ready."
        )

    if low in ("security", "security_check", "audit") or "security check" in low:
        sec = security_check(repo_path=repo_path, window=window or "7d", config=cfg)
        hdrs = sec.get("http_headers") or {}
        tls = sec.get("tls_certificate") or {}
        spike = sec.get("failed_login_spike") or {}
        tag = "[MOCK DATA] " if sec.get("is_mock") else ""
        lines = [
            f"{tag}SwiftSats Security Check (mode={cfg.mode}):",
            f"- Failed-login spike check: {spike.get('message', 'N/A')}",
            f"- HTTP security headers: {hdrs.get('score', hdrs.get('reason', hdrs.get('error', 'skipped')))}",
            f"- TLS certificate: {tls.get('status', tls.get('reason', tls.get('error', 'skipped')))}"
            + (f" ({tls.get('days_remaining')}d left)" if tls.get("days_remaining") is not None else ""),
        ]
        dep = sec.get("dependency_audit") or {}
        if not dep.get("skipped"):
            lines.append(f"- Local dependency audit: {dep.get('audits', dep.get('error', 'checked'))}")
        return "\n".join(lines)

    rep = generate_swiftsats_report(
        query=query or "daily_report",
        window=window or None,
        save_markdown=True,
        repo_path=repo_path,
        config=cfg,
    )
    if not rep.get("ok"):
        return str(rep.get("summary_text") or NOT_CONNECTED_MSG)

    return str(rep["summary_text"])
