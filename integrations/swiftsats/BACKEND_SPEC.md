# SwiftSats Backend Read-Only Metrics Endpoint Spec (`/api/internal/rose-metrics/`)

> Purpose: Allow R.O.S.E (`integrations/swiftsats/client.py`) to inspect high-level operational health and aggregate business counts cleanly **without exposing personal user data (PII), touching `/admin`, or enabling any write actions**.

---

## 1. Endpoint Overview

- **Method:** `GET` only (`405 Method Not Allowed` for `POST`, `PUT`, `PATCH`, `DELETE`)
- **Path:** `/api/internal/rose-metrics/`
- **Authentication:** `Authorization: Bearer <SWIFTSATS_API_TOKEN>` (validated using constant-time `hmac.compare_digest` against Django settings / env)
- **Network / Hardening:**
  - Serve over **HTTPS only**.
  - Optional allowlist / VPN or strict Django rate limit (e.g., max 60 req/min).
  - Read-only database router or explicit `.using("readonly")` / `SELECT` aggregations only.

---

## 2. Query Parameters

| Parameter | Allowed Values | Default | Description |
|---|---|---|---|
| `window` | `24h`, `7d`, `30d` | `7d` | Lookback aggregation window (`24h` returns hourly buckets; `7d`/`30d` return daily buckets) |
| `metric` | `all` \| any of the 8 metric names below | `all` | Return either the full bundle or a single aggregate metric object |

Valid `metric` keys:
1. `signups_per_day`
2. `active_users`
3. `logins_per_day`
4. `failed_logins`
5. `transactions_count_and_volume`
6. `failed_or_stuck_payments`
7. `error_rate`
8. `service_uptime`

---

## 3. Strict Privacy & Read-Only Rules

1. **NO PII allowed anywhere in response**: NEVER include emails, phone numbers, user IDs, full names, IP addresses, session tokens, card BINs, bank account numbers, or raw request payloads.
2. **Aggregate numbers only**: Counts, sums, percentages, millisecond percentiles, and UTC period labels (`YYYY-MM-DD` or `YYYY-MM-DD HH:00`).
3. **Never touch payments/wallet state**: Read aggregate statuses from existing transaction tables; zero mutations.

---

## 4. Exact JSON Response Schema (`200 OK`)

```json
{
  "ok": true,
  "window": "7d",
  "generated_at": "2026-11-14T00:00:00Z",
  "currency": "NGN",
  "metrics": {
    "signups_per_day": {
      "total": 148,
      "average": 21.1,
      "latest": 24,
      "series": [
        { "period": "2026-11-08", "value": 18 },
        { "period": "2026-11-14", "value": 24 }
      ]
    },
    "active_users": {
      "dau": 312,
      "wau": 1140,
      "mau": 3420,
      "stickiness_pct": 9.1
    },
    "logins_per_day": {
      "total": 2015,
      "average": 287.9,
      "latest": 310,
      "series": [
        { "period": "2026-11-08", "value": 255 },
        { "period": "2026-11-14", "value": 310 }
      ]
    },
    "failed_logins": {
      "total": 82,
      "baseline_avg": 9.5,
      "latest": 11,
      "series": [
        { "period": "2026-11-08", "value": 8 },
        { "period": "2026-11-14", "value": 11 }
      ]
    },
    "transactions_count_and_volume": {
      "total_count": 1240,
      "total_volume_ngn": 18500000,
      "avg_ticket_ngn": 14919.35,
      "series": [
        { "period": "2026-11-08", "count": 162, "volume_ngn": 2410000 },
        { "period": "2026-11-14", "count": 195, "volume_ngn": 2980000 }
      ]
    },
    "failed_or_stuck_payments": {
      "failed_count": 6,
      "stuck_pending_count": 1,
      "at_risk_volume_ngn": 32500,
      "failure_rate_pct": 0.48
    },
    "error_rate": {
      "current_pct": 0.22,
      "average_pct": 0.28,
      "p95_latency_ms": 215,
      "series": [
        { "period": "2026-11-08", "value": 0.30 },
        { "period": "2026-11-14", "value": 0.22 }
      ]
    },
    "service_uptime": {
      "uptime_pct": 99.96,
      "incidents_count": 0,
      "status": "healthy"
    }
  }
}
```

---

## 5. Reference Django Implementation (Drop into SwiftSats when Ready)

```python
import hmac
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate, TruncHour
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET


def _authorize(request) -> bool:
    expected = getattr(settings, "ROSE_METRICS_TOKEN", "")
    auth = request.headers.get("Authorization", "")
    if not expected or not auth.startswith("Bearer "):
        return False
    provided = auth[len("Bearer "):].strip()
    return hmac.compare_digest(provided.encode(), expected.encode())


@require_GET
def rose_metrics_view(request):
    if not _authorize(request):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=401)

    window = request.GET.get("window", "7d")
    if window not in {"24h", "7d", "30d"}:
        window = "7d"

    now = timezone.now()
    delta = timedelta(hours=24) if window == "24h" else timedelta(days=7 if window == "7d" else 30)
    since = now - delta
    trunc_fn = TruncHour if window == "24h" else TruncDate

    User = get_user_model()
    signups_qs = (
        User.objects.filter(date_joined__gte=since)
        .annotate(period=trunc_fn("date_joined"))
        .values("period")
        .annotate(value=Count("id"))
        .order_by("period")
    )
    # Repeat identically for LoginAudit / Transaction models using aggregate Counts & Sums ONLY.

    return JsonResponse({
        "ok": True,
        "window": window,
        "generated_at": now.isoformat(),
        "currency": "NGN",
        "metrics": {
            "signups_per_day": {
                "total": sum(r["value"] for r in signups_qs),
                "average": round(sum(r["value"] for r in signups_qs) / max(1, len(signups_qs)), 1),
                "latest": signups_qs[-1]["value"] if signups_qs else 0,
                "series": [{"period": r["period"].isoformat(), "value": r["value"]} for r in signups_qs],
            },
            # ... populate active_users, logins_per_day, failed_logins,
            # transactions_count_and_volume, failed_or_stuck_payments, error_rate, service_uptime
        },
    })
```

And wire in `.env` inside R.O.S.E once deployed:
```env
SWIFTSATS_MODE=live
SWIFTSATS_BASE_URL=https://your-swiftsats-domain.com
SWIFTSATS_API_TOKEN=your_strong_random_bearer_token
```

