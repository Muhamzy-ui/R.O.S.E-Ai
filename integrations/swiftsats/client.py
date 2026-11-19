"""
R.O.S.E (Project Mirror) - SwiftSats Read-Only Metrics Client (Phase 7)
Strict Rules:
- Default mode is `disabled` -> returns `"SwiftSats isn't connected yet"`, NEVER fakes numbers.
- `mock` mode generates realistic synthetic time series strictly labeled `MOCK DATA`.
- `live` mode performs HTTPS GET requests ONLY (`read-only`, bearer token, no admin automation, no writes).
- If a read-only Postgres DSN is ever configured separately, SQL guard strictly permits SELECT/WITH read-only queries only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request

from dotenv import load_dotenv

from core.audit import audit_log, redact_secrets
from core.logging_setup import get_logger
from core.permissions import Tier
from core.result import Result

logger = get_logger("swiftsats.client")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"

VALID_MODES = {"disabled", "mock", "live"}
VALID_WINDOWS = {"24h", "7d", "30d"}
NOT_CONNECTED_MSG = "SwiftSats isn't connected yet"

_DISALLOWED_SQL_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|merge|copy|execute|call|do)\b",
    re.IGNORECASE,
)


@dataclass
class SwiftSatsConfig:
    mode: str = "disabled"
    base_url: str = ""
    api_token: str = ""
    readonly_dsn: str = ""

    @property
    def is_mock(self) -> bool:
        return self.mode == "mock"

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def is_disabled(self) -> bool:
        return self.mode == "disabled"


def normalize_window(window: str = "7d") -> str:
    raw = str(window or "7d").strip().lower()
    if raw in ("today", "1d", "24hr", "24h", "day"):
        return "24h"
    if raw in ("week", "7d", "7days", "weekly"):
        return "7d"
    if raw in ("month", "30d", "30days", "monthly"):
        return "30d"
    return "7d"


def validate_readonly_sql(sql: str) -> bool:
    """Strictly permits single-statement read-only SELECT/WITH queries only."""
    cleaned = str(sql or "").strip()
    if not cleaned:
        return False
    # Strip trailing single semicolon safely; forbid stacked statements
    body = cleaned[:-1].strip() if cleaned.endswith(";") else cleaned
    if ";" in body:
        return False
    low = body.lstrip("(").lower()
    if not (low.startswith("select") or low.startswith("with")):
        return False
    if _DISALLOWED_SQL_RE.search(body):
        return False
    return True


def load_swiftsats_config(env_path: Optional[Path] = None) -> SwiftSatsConfig:
    """Loads SWIFTSATS_* config cleanly from `.env` + environment without logging credentials."""
    target = env_path or DEFAULT_ENV_PATH
    if target.exists():
        load_dotenv(dotenv_path=target, override=False)

    raw_mode = os.environ.get("SWIFTSATS_MODE", "disabled").strip().lower()
    mode = raw_mode if raw_mode in VALID_MODES else "disabled"
    base_url = os.environ.get("SWIFTSATS_BASE_URL", "").strip().rstrip("/")
    api_token = os.environ.get("SWIFTSATS_API_TOKEN", "").strip()
    readonly_dsn = os.environ.get("SWIFTSATS_READONLY_DSN", "").strip()
    return SwiftSatsConfig(
        mode=mode,
        base_url=base_url,
        api_token=api_token,
        readonly_dsn=readonly_dsn,
    )


def _window_dates(window: str, reference_dt: Optional[datetime] = None) -> List[str]:
    win = normalize_window(window)
    now = reference_dt or datetime.now(timezone.utc)
    if win == "24h":
        # 24 hourly buckets labeled YYYY-MM-DDTHH:00Z
        return [
            (now - timedelta(hours=23 - i)).strftime("%Y-%m-%d %H:00")
            for i in range(24)
        ]
    days = 7 if win == "7d" else 30
    return [
        (now - timedelta(days=(days - 1) - i)).strftime("%Y-%m-%d")
        for i in range(days)
    ]


def _build_mock_dataset(window: str) -> Dict[str, Any]:
    """
    Generates deterministic, realistic synthetic SwiftSats business metrics for testing/preview.
    Every returned item includes explicit MOCK DATA labeling.
    """
    win = normalize_window(window)
    buckets = _window_dates(win)
    n = len(buckets)

    if win == "24h":
        signups_vals = [1 + (i % 3) for i in range(n)]
        logins_vals = [18 + ((i * 5) % 27) for i in range(n)]
        # Baseline 2-4 failed logins/hr with a realistic spike on the last hour
        failed_vals = [2 + (i % 3) for i in range(n - 1)] + [26]
        tx_counts = [14 + ((i * 3) % 19) for i in range(n)]
        tx_volumes_ngn = [c * 12500 for c in tx_counts]
        err_rates = [round(0.2 + (0.05 if i % 6 == 0 else 0.0), 2) for i in range(n)]
        stuck_count = 2
        stuck_val = 48500
        dau, wau, mau = 312, 1140, 3420
    else:
        signups_vals = [18 + ((i * 4) % 15) + (i // 4) for i in range(n)]
        logins_vals = [240 + ((i * 23) % 90) + (i * 5) for i in range(n)]
        # Baseline ~8-14 failed logins/day with a sharp abnormal spike on the final day
        failed_vals = [9 + (i % 5) for i in range(n - 1)] + [54]
        tx_counts = [155 + ((i * 19) % 60) + (i * 4) for i in range(n)]
        tx_volumes_ngn = [c * 14200 for c in tx_counts]
        err_rates = [round(0.24 + (0.08 if i == n - 1 else 0.02 * (i % 4)), 2) for i in range(n)]
        stuck_count = 3 if win == "7d" else 7
        stuck_val = 92000 if win == "7d" else 241500
        dau, wau, mau = 312, 1140, 3420

    def _series(vals: List[float]) -> List[Dict[str, Any]]:
        return [{"period": buckets[i], "value": vals[i]} for i in range(n)]

    return {
        "mode": "mock",
        "is_mock": True,
        "label": "MOCK DATA",
        "window": win,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "currency": "NGN",
        "metrics": {
            "signups_per_day": {
                "total": sum(signups_vals),
                "average": round(sum(signups_vals) / n, 1),
                "latest": signups_vals[-1],
                "series": _series(signups_vals),
            },
            "active_users": {
                "dau": dau,
                "wau": wau,
                "mau": mau,
                "stickiness_pct": round((dau / mau) * 100.0, 1),
            },
            "logins_per_day": {
                "total": sum(logins_vals),
                "average": round(sum(logins_vals) / n, 1),
                "latest": logins_vals[-1],
                "series": _series(logins_vals),
            },
            "failed_logins": {
                "total": sum(failed_vals),
                "baseline_avg": round(sum(failed_vals[:-1]) / max(1, n - 1), 1),
                "latest": failed_vals[-1],
                "series": _series(failed_vals),
            },
            "transactions_count_and_volume": {
                "total_count": sum(tx_counts),
                "total_volume_ngn": sum(tx_volumes_ngn),
                "avg_ticket_ngn": round(sum(tx_volumes_ngn) / max(1, sum(tx_counts)), 2),
                "series": [
                    {"period": buckets[i], "count": tx_counts[i], "volume_ngn": tx_volumes_ngn[i]}
                    for i in range(n)
                ],
            },
            "failed_or_stuck_payments": {
                "failed_count": 4 if win == "24h" else (11 if win == "7d" else 34),
                "stuck_pending_count": stuck_count,
                "at_risk_volume_ngn": stuck_val,
                "failure_rate_pct": 1.65 if win != "24h" else 1.10,
            },
            "error_rate": {
                "current_pct": err_rates[-1],
                "average_pct": round(sum(err_rates) / n, 2),
                "p95_latency_ms": 248,
                "series": _series(err_rates),
            },
            "service_uptime": {
                "uptime_pct": 99.94,
                "incidents_count": 0 if win == "24h" else 1,
                "status": "degraded_minor" if stuck_count > 0 else "healthy",
            },
        },
    }


def _http_get_json_readonly(url: str, token: str, timeout: float = 10.0) -> Dict[str, Any]:
    """Strictly read-only HTTPS/HTTP GET client to `/api/internal/rose-metrics/`."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise ValueError("Invalid SWIFTSATS_BASE_URL (must start with https:// or http://).")

    headers = {
        "Accept": "application/json",
        "User-Agent": "ROSE-SwiftSats-Connector/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _fetch_metric(
    metric_name: str,
    window: str = "7d",
    *,
    config: Optional[SwiftSatsConfig] = None,
    http_get_fn: Optional[Callable[[str, str, float], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    cfg = config or load_swiftsats_config()
    win = normalize_window(window)

    if cfg.is_disabled:
        audit_log(
            tool=f"swiftsats.{metric_name}",
            args={"window": win, "mode": "disabled"},
            tier=int(Tier.TIER_0_READ_ONLY),
            result=Result.unverified(message=NOT_CONNECTED_MSG),
            source="swiftsats",
        )
        return {
            "ok": False,
            "mode": "disabled",
            "is_mock": False,
            "label": "NOT_CONNECTED",
            "window": win,
            "metric": metric_name,
            "message": NOT_CONNECTED_MSG,
            "data": None,
        }

    if cfg.is_mock:
        dataset = _build_mock_dataset(win)
        payload = dataset["metrics"].get(metric_name)
        audit_log(
            tool=f"swiftsats.{metric_name}",
            args={"window": win, "mode": "mock"},
            tier=int(Tier.TIER_0_READ_ONLY),
            result=Result.success(data={"is_mock": True}, verified=True, message="MOCK DATA"),
            source="swiftsats",
        )
        return {
            "ok": True,
            "mode": "mock",
            "is_mock": True,
            "label": "MOCK DATA",
            "window": win,
            "metric": metric_name,
            "data": payload,
        }

    # Live read-only mode
    if not cfg.base_url:
        return {
            "ok": False,
            "mode": "live",
            "is_mock": False,
            "label": "LIVE_UNCONFIGURED",
            "window": win,
            "metric": metric_name,
            "message": "SWIFTSATS_BASE_URL is required when SWIFTSATS_MODE=live.",
            "data": None,
        }
    if not cfg.api_token:
        return {
            "ok": False,
            "mode": "live",
            "is_mock": False,
            "label": "LIVE_UNCONFIGURED",
            "window": win,
            "metric": metric_name,
            "message": "SWIFTSATS_API_TOKEN is required when SWIFTSATS_MODE=live.",
            "data": None,
        }

    qs = urllib.parse.urlencode({"window": win, "metric": metric_name})
    endpoint = f"{cfg.base_url}/api/internal/rose-metrics/?{qs}"
    fetcher = http_get_fn or _http_get_json_readonly
    try:
        raw_resp = fetcher(endpoint, cfg.api_token, 10.0)
        metric_data = (
            raw_resp.get("metrics", {}).get(metric_name)
            if isinstance(raw_resp.get("metrics"), dict)
            else raw_resp.get("data", raw_resp)
        )
        audit_log(
            tool=f"swiftsats.{metric_name}",
            args=redact_secrets({"window": win, "mode": "live"}),
            tier=int(Tier.TIER_0_READ_ONLY),
            result=Result.success(verified=True),
            source="swiftsats",
        )
        return {
            "ok": True,
            "mode": "live",
            "is_mock": False,
            "label": "LIVE",
            "window": win,
            "metric": metric_name,
            "data": metric_data,
        }
    except Exception as exc:
        logger.warning(f"SwiftSats live metric fetch error ({metric_name}): {type(exc).__name__}")
        audit_log(
            tool=f"swiftsats.{metric_name}",
            args={"window": win, "mode": "live"},
            tier=int(Tier.TIER_0_READ_ONLY),
            result=Result.failure(f"Live SwiftSats GET failed: {type(exc).__name__}"),
            source="swiftsats",
        )
        return {
            "ok": False,
            "mode": "live",
            "is_mock": False,
            "label": "LIVE_ERROR",
            "window": win,
            "metric": metric_name,
            "message": f"SwiftSats live connection error ({type(exc).__name__}: {exc}). Never inventing numbers.",
            "data": None,
        }


# ── Required 8 Public Read-Only Metric Functions ─────────────────────────────

def signups_per_day(window: str = "7d", **kwargs) -> Dict[str, Any]:
    return _fetch_metric("signups_per_day", window, **kwargs)


def active_users(window: str = "7d", **kwargs) -> Dict[str, Any]:
    return _fetch_metric("active_users", window, **kwargs)


def logins_per_day(window: str = "7d", **kwargs) -> Dict[str, Any]:
    return _fetch_metric("logins_per_day", window, **kwargs)


def failed_logins(window: str = "7d", **kwargs) -> Dict[str, Any]:
    return _fetch_metric("failed_logins", window, **kwargs)


def transactions_count_and_volume(window: str = "7d", **kwargs) -> Dict[str, Any]:
    return _fetch_metric("transactions_count_and_volume", window, **kwargs)


def failed_or_stuck_payments(window: str = "7d", **kwargs) -> Dict[str, Any]:
    return _fetch_metric("failed_or_stuck_payments", window, **kwargs)


def error_rate(window: str = "7d", **kwargs) -> Dict[str, Any]:
    return _fetch_metric("error_rate", window, **kwargs)


def service_uptime(window: str = "7d", **kwargs) -> Dict[str, Any]:
    return _fetch_metric("service_uptime", window, **kwargs)


def fetch_all_metrics(
    window: str = "7d",
    *,
    config: Optional[SwiftSatsConfig] = None,
    http_get_fn: Optional[Callable[[str, str, float], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Fetches all 8 aggregate SwiftSats metrics cleanly in a single call (or returns disabled honestly)."""
    cfg = config or load_swiftsats_config()
    win = normalize_window(window)

    if cfg.is_disabled:
        return {
            "ok": False,
            "mode": "disabled",
            "is_mock": False,
            "label": "NOT_CONNECTED",
            "window": win,
            "message": NOT_CONNECTED_MSG,
            "metrics": {},
        }

    if cfg.is_mock:
        return {"ok": True, **_build_mock_dataset(win)}

    if not cfg.base_url or not cfg.api_token:
        return {
            "ok": False,
            "mode": "live",
            "is_mock": False,
            "label": "LIVE_UNCONFIGURED",
            "window": win,
            "message": "SWIFTSATS_BASE_URL and SWIFTSATS_API_TOKEN must be configured for SWIFTSATS_MODE=live.",
            "metrics": {},
        }

    qs = urllib.parse.urlencode({"window": win, "metric": "all"})
    endpoint = f"{cfg.base_url}/api/internal/rose-metrics/?{qs}"
    fetcher = http_get_fn or _http_get_json_readonly
    try:
        raw_resp = fetcher(endpoint, cfg.api_token, 10.0)
        metrics = raw_resp.get("metrics") or {}
        return {
            "ok": True,
            "mode": "live",
            "is_mock": False,
            "label": "LIVE",
            "window": win,
            "generated_at": raw_resp.get("generated_at", datetime.now(timezone.utc).isoformat()),
            "currency": raw_resp.get("currency", "NGN"),
            "metrics": metrics,
        }
    except Exception as exc:
        return {
            "ok": False,
            "mode": "live",
            "is_mock": False,
            "label": "LIVE_ERROR",
            "window": win,
            "message": f"SwiftSats live endpoint unreachable ({type(exc).__name__}: {exc}). Never inventing numbers.",
            "metrics": {},
        }
