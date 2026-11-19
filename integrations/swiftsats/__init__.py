"""
R.O.S.E (Project Mirror) - SwiftSats Read-Only Connector Package (Phase 7)
"""

from integrations.swiftsats.client import (
    NOT_CONNECTED_MSG,
    SwiftSatsConfig,
    active_users,
    error_rate,
    failed_logins,
    failed_or_stuck_payments,
    fetch_all_metrics,
    load_swiftsats_config,
    logins_per_day,
    service_uptime,
    signups_per_day,
    transactions_count_and_volume,
    validate_readonly_sql,
)
from integrations.swiftsats.reports import (
    REPORTS_DIR,
    generate_swiftsats_report,
    parse_swiftsats_request,
)
from integrations.swiftsats.security import (
    analyze_failed_login_spike,
    audit_local_repo_dependencies,
    check_http_security_headers,
    check_tls_certificate,
    security_check,
)

__all__ = [
    "NOT_CONNECTED_MSG",
    "SwiftSatsConfig",
    "load_swiftsats_config",
    "validate_readonly_sql",
    "signups_per_day",
    "active_users",
    "logins_per_day",
    "failed_logins",
    "transactions_count_and_volume",
    "failed_or_stuck_payments",
    "error_rate",
    "service_uptime",
    "fetch_all_metrics",
    "analyze_failed_login_spike",
    "check_http_security_headers",
    "check_tls_certificate",
    "audit_local_repo_dependencies",
    "security_check",
    "generate_swiftsats_report",
    "parse_swiftsats_request",
    "REPORTS_DIR",
]
