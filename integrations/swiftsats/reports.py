"""
R.O.S.E (Project Mirror) - SwiftSats Plain-Language & Markdown Report Generator (Phase 7)
Supports 24h / 7d / 30d windows, trend & anomaly analysis, security checks, and `reports/` persistence.
Strict Honesty Rule:
- If `SWIFTSATS_MODE=disabled` -> returns `"SwiftSats isn't connected yet"`, NEVER fakes metrics.
- If `SWIFTSATS_MODE=mock` -> explicitly labels `"MOCK DATA"` in every heading and output.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from integrations.swiftsats.client import (
    NOT_CONNECTED_MSG,
    SwiftSatsConfig,
    fetch_all_metrics,
    load_swiftsats_config,
    normalize_window,
)
from integrations.swiftsats.security import analyze_failed_login_spike, security_check

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REPORTS_DIR = PROJECT_ROOT / "reports"


def parse_swiftsats_request(query: str = "daily_report") -> Tuple[str, str, bool]:
    """Extracts `(window, focus_metric, include_security)` from natural language or command text."""
    low = str(query or "").lower()

    window = "7d"
    if re.search(r"\b(24\s*h|today|daily|last\s+24)\b", low):
        window = "24h"
    elif re.search(r"\b(30\s*d|month|monthly|30\s*days)\b", low):
        window = "30d"
    elif re.search(r"\b(7\s*d|week|weekly|7\s*days)\b", low):
        window = "7d"

    focus = "daily_report"
    if "signup" in low or "registr" in low:
        focus = "signups_per_day"
    elif "active user" in low or "dau" in low or "mau" in low:
        focus = "active_users"
    elif "failed login" in low or "brute" in low:
        focus = "failed_logins"
    elif "login" in low:
        focus = "logins_per_day"
    elif "stuck" in low or "failed payment" in low:
        focus = "failed_or_stuck_payments"
    elif "tx" in low or "transaction" in low or "volume" in low or "revenue" in low:
        focus = "transactions_count_and_volume"
    elif "error" in low or "latency" in low:
        focus = "error_rate"
    elif "uptime" in low:
        focus = "service_uptime"

    include_sec = ("security" in low or "header" in low or "tls" in low or "ssl" in low or focus == "daily_report")
    return window, focus, include_sec


def _compute_half_trend_pct(series: List[Dict[str, Any]], key: str = "value") -> Optional[float]:
    if not series or len(series) < 2:
        return None
    mid = max(1, len(series) // 2)
    first_avg = sum(float(x.get(key, 0)) for x in series[:mid]) / mid
    second_avg = sum(float(x.get(key, 0)) for x in series[mid:]) / max(1, len(series) - mid)
    if first_avg <= 0:
        return None
    return round(((second_avg - first_avg) / first_avg) * 100.0, 1)


def _fmt_trend(pct: Optional[float]) -> str:
    if pct is None:
        return "trend N/A"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}% vs early period"


def generate_swiftsats_report(
    *,
    query: str = "daily_report",
    window: Optional[str] = None,
    save_markdown: bool = True,
    include_security: Optional[bool] = None,
    repo_path: str = "",
    config: Optional[SwiftSatsConfig] = None,
    http_get_fn: Optional[Callable[[str, str, float], Dict[str, Any]]] = None,
    header_probe: Optional[Callable[[str], Dict[str, str]]] = None,
    cert_probe: Optional[Callable[[str, int], Dict[str, Any]]] = None,
    reports_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Generates an executive SwiftSats summary + saves timestamped markdown into `reports/`.
    Never generates or writes synthetic numbers unless `SWIFTSATS_MODE=mock`.
    """
    cfg = config or load_swiftsats_config()
    parsed_win, focus, parsed_sec = parse_swiftsats_request(query)
    win = normalize_window(window or parsed_win)
    want_sec = parsed_sec if include_security is None else include_security

    if cfg.is_disabled:
        return {
            "ok": False,
            "mode": "disabled",
            "is_mock": False,
            "window": win,
            "focus": focus,
            "summary_text": f"{NOT_CONNECTED_MSG} Configure SWIFTSATS_MODE=mock or live in .env.",
            "markdown": "",
            "report_path": None,
        }

    bundle = fetch_all_metrics(win, config=cfg, http_get_fn=http_get_fn)
    if not bundle.get("ok"):
        err = str(bundle.get("message") or "Could not fetch SwiftSats metrics.")
        return {
            "ok": False,
            "mode": cfg.mode,
            "is_mock": cfg.is_mock,
            "window": win,
            "focus": focus,
            "summary_text": err,
            "markdown": "",
            "report_path": None,
        }

    m = bundle.get("metrics") or {}
    signups = m.get("signups_per_day") or {}
    users = m.get("active_users") or {}
    logins = m.get("logins_per_day") or {}
    failed_l = m.get("failed_logins") or {}
    tx = m.get("transactions_count_and_volume") or {}
    stuck = m.get("failed_or_stuck_payments") or {}
    err = m.get("error_rate") or {}
    up = m.get("service_uptime") or {}

    signup_trend = _compute_half_trend_pct(signups.get("series", []))
    login_trend = _compute_half_trend_pct(logins.get("series", []))
    tx_trend = _compute_half_trend_pct(tx.get("series", []), key="count")
    spike_info = analyze_failed_login_spike(failed_l.get("series", []))

    anomalies: List[str] = []
    if spike_info.get("spike_detected"):
        anomalies.append(spike_info["message"])
    if int(stuck.get("stuck_pending_count") or 0) > 0:
        anomalies.append(
            f"{stuck.get('stuck_pending_count')} payment(s) stuck/pending (NGN {int(stuck.get('at_risk_volume_ngn') or 0):,} at risk)."
        )
    if float(err.get("current_pct") or 0.0) >= 1.0:
        anomalies.append(f"Elevated error rate: {err.get('current_pct')}%.")

    sec_data: Optional[Dict[str, Any]] = None
    if want_sec:
        sec_data = security_check(
            window=win,
            repo_path=repo_path,
            config=cfg,
            header_probe=header_probe,
            cert_probe=cert_probe,
            http_get_fn=http_get_fn,
        )
        hdrs = sec_data.get("http_headers") or {}
        if hdrs.get("ok") and hdrs.get("missing"):
            anomalies.append(f"Missing HTTP security headers: {', '.join(hdrs['missing'])}.")
        tls = sec_data.get("tls_certificate") or {}
        if tls.get("ok") and tls.get("status") in ("expiring_soon", "expired"):
            anomalies.append(f"TLS certificate status={tls['status']} ({tls.get('days_remaining')}d left).")

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    mock_tag = " [MOCK DATA]" if cfg.is_mock else ""
    banner = (
        "> **MOCK DATA** — Synthetic metrics generated by `SWIFTSATS_MODE=mock` for testing.\n\n"
        if cfg.is_mock
        else ""
    )

    vol_ngn = int(tx.get("total_volume_ngn") or 0)
    md_lines = [
        f"# SwiftSats Health & Business Report ({win}){mock_tag}",
        f"*Generated: {now_iso} | Mode: `{cfg.mode}`*",
        "",
    ]
    if banner:
        md_lines.append(banner.rstrip())
        md_lines.append("")

    md_lines.extend([
        "## Key Metrics",
        f"- **Signups ({win}):** {signups.get('total', 'N/A')} total | avg {signups.get('average', 'N/A')}/bucket | latest {signups.get('latest', 'N/A')} ({_fmt_trend(signup_trend)})",
        f"- **Active Users:** DAU {users.get('dau', 'N/A')} | WAU {users.get('wau', 'N/A')} | MAU {users.get('mau', 'N/A')} (Stickiness {users.get('stickiness_pct', 'N/A')}%)",
        f"- **Logins ({win}):** {logins.get('total', 'N/A')} total | avg {logins.get('average', 'N/A')}/bucket | latest {logins.get('latest', 'N/A')} ({_fmt_trend(login_trend)})",
        f"- **Failed Logins ({win}):** {failed_l.get('total', 'N/A')} total | baseline {failed_l.get('baseline_avg', 'N/A')} | latest {failed_l.get('latest', 'N/A')}",
        f"- **Transactions ({win}):** {tx.get('total_count', 'N/A')} tx | NGN {vol_ngn:,} volume ({_fmt_trend(tx_trend)})",
        f"- **Failed / Stuck Payments:** {stuck.get('failed_count', 0)} failed, {stuck.get('stuck_pending_count', 0)} stuck pending (NGN {int(stuck.get('at_risk_volume_ngn') or 0):,})",
        f"- **Error Rate & Latency:** {err.get('current_pct', 'N/A')}% current (avg {err.get('average_pct', 'N/A')}%) | p95 {err.get('p95_latency_ms', 'N/A')} ms",
        f"- **Service Uptime:** {up.get('uptime_pct', 'N/A')}% ({up.get('status', 'unknown')}, incidents: {up.get('incidents_count', 0)})",
        "",
        "## Abnormalities & Risks Detected",
    ])
    if anomalies:
        for a in anomalies:
            md_lines.append(f"- ⚠️ {a}")
    else:
        md_lines.append("- None detected across thresholds cleanly.")

    if sec_data is not None:
        md_lines.extend(["", "## Security Audit Snapshot"])
        hdrs = sec_data.get("http_headers") or {}
        tls = sec_data.get("tls_certificate") or {}
        dep = sec_data.get("dependency_audit") or {}
        if hdrs.get("skipped"):
            md_lines.append("- **HTTP Security Headers:** Skipped (SWIFTSATS_BASE_URL not configured)")
        elif hdrs.get("ok"):
            md_lines.append(f"- **HTTP Security Headers:** {hdrs.get('score')} present (missing: {', '.join(hdrs.get('missing') or ['none'])})")
        else:
            md_lines.append(f"- **HTTP Security Headers:** {hdrs.get('error')}")

        if tls.get("skipped"):
            md_lines.append("- **TLS Certificate:** Skipped (SWIFTSATS_BASE_URL not configured)")
        elif tls.get("ok"):
            md_lines.append(f"- **TLS Certificate:** {tls.get('status')} — {tls.get('days_remaining')} days remaining (expires {tls.get('expires_at')})")
        else:
            md_lines.append(f"- **TLS Certificate:** {tls.get('error')}")

        if not dep.get("skipped"):
            md_lines.append(f"- **Dependency Audit ({dep.get('repo_path', '')}):** {dep.get('audits', dep.get('error', 'checked'))}")

    markdown_text = "\n".join(md_lines) + "\n"

    saved_path: Optional[str] = None
    if save_markdown:
        out_dir = reports_dir or REPORTS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_file = out_dir / f"swiftsats_report_{win}_{stamp}.md"
        out_file.write_text(markdown_text, encoding="utf-8")
        if out_file.exists():
            saved_path = str(out_file)

    # Concise plain-language summary focusing on question if specific metric asked
    if focus == "logins_per_day":
        headline = (
            f"SwiftSats Logins ({win}{mock_tag}): {logins.get('total')} total logins "
            f"(latest bucket: {logins.get('latest')}, {_fmt_trend(login_trend)}). "
            f"Failed logins latest: {failed_l.get('latest')}."
        )
    elif focus == "signups_per_day":
        headline = (
            f"SwiftSats Signups ({win}{mock_tag}): {signups.get('total')} total signups "
            f"(avg {signups.get('average')}, {_fmt_trend(signup_trend)})."
        )
    else:
        headline = (
            f"SwiftSats Report ({win}{mock_tag}): {tx.get('total_count')} transactions (NGN {vol_ngn:,}), "
            f"{signups.get('total')} signups, {logins.get('total')} logins, DAU {users.get('dau')}, "
            f"uptime {up.get('uptime_pct')}%. "
            + ("Alerts: " + " | ".join(anomalies) if anomalies else "No abnormal spikes detected.")
        )
    if saved_path:
        headline += f"\nSaved markdown report: {saved_path}"

    return {
        "ok": True,
        "mode": cfg.mode,
        "is_mock": cfg.is_mock,
        "window": win,
        "focus": focus,
        "summary_text": headline,
        "anomalies": anomalies,
        "markdown": markdown_text,
        "report_path": saved_path,
        "data": bundle,
    }
