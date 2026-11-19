"""
R.O.S.E (Project Mirror) - SwiftSats Read-Only Security & Anomaly Audit (Phase 7)
Covers:
1. Public HTTP security headers inspection (`HEAD`/`GET`, non-intrusive)
2. TLS certificate expiry check
3. Optional local-repo dependency audit (`pip-audit` / `npm audit`) ONLY when user provides `repo_path`
4. Failed-login spike detector
NEVER touches `/admin` login or executes destructive security scans.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
from typing import Any, Callable, Dict, List, Optional
import urllib.parse
import urllib.request

from integrations.swiftsats.client import (
    NOT_CONNECTED_MSG,
    SwiftSatsConfig,
    failed_logins,
    load_swiftsats_config,
    normalize_window,
)

REQUIRED_SECURITY_HEADERS = (
    "Strict-Transport-Security",
    "Content-Security-Policy",
    "X-Frame-Options",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "Permissions-Policy",
)


def check_http_security_headers(
    base_url: str,
    *,
    header_probe: Optional[Callable[[str], Dict[str, str]]] = None,
    timeout: float = 8.0,
) -> Dict[str, Any]:
    """Checks public URL response for essential HTTP security headers cleanly."""
    clean_url = str(base_url or "").strip()
    if not clean_url:
        return {"ok": False, "error": "No SWIFTSATS_BASE_URL provided for header check.", "missing": list(REQUIRED_SECURITY_HEADERS), "present": {}}

    try:
        if header_probe is not None:
            raw_headers = header_probe(clean_url)
        else:
            parsed = urllib.parse.urlparse(clean_url)
            if parsed.scheme not in ("https", "http") or not parsed.netloc:
                return {"ok": False, "error": "Invalid URL format.", "missing": [], "present": {}}
            req = urllib.request.Request(
                clean_url,
                headers={"User-Agent": "ROSE-SecurityCheck/1.0"},
                method="HEAD",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_headers = dict(resp.headers.items())
    except Exception as exc:
        return {
            "ok": False,
            "url": clean_url,
            "error": f"Header check failed: {type(exc).__name__}: {exc}",
            "missing": [],
            "present": {},
        }

    lower_map = {str(k).lower(): str(v) for k, v in (raw_headers or {}).items()}
    present: Dict[str, str] = {}
    missing: List[str] = []
    for hdr in REQUIRED_SECURITY_HEADERS:
        val = lower_map.get(hdr.lower())
        if val:
            present[hdr] = val
        else:
            missing.append(hdr)

    return {
        "ok": True,
        "url": clean_url,
        "present": present,
        "missing": missing,
        "score": f"{len(present)}/{len(REQUIRED_SECURITY_HEADERS)}",
    }


def check_tls_certificate(
    base_url_or_host: str,
    port: int = 443,
    *,
    cert_probe: Optional[Callable[[str, int], Dict[str, Any]]] = None,
    timeout: float = 8.0,
) -> Dict[str, Any]:
    """Inspects TLS certificate expiry date without bypassing verification."""
    raw = str(base_url_or_host or "").strip()
    if not raw:
        return {"ok": False, "error": "No hostname/URL provided for TLS check."}

    parsed = urllib.parse.urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.hostname or raw
    target_port = parsed.port or port

    try:
        if cert_probe is not None:
            cert_info = cert_probe(host, target_port)
            not_after_str = str(cert_info.get("notAfter") or "")
            days_left = cert_info.get("days_remaining")
            if days_left is None and not_after_str:
                exp_dt = datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
                days_left = (exp_dt - datetime.now(timezone.utc)).days
                expires_iso = exp_dt.isoformat()
            else:
                expires_iso = str(cert_info.get("expires_at") or not_after_str)
            issuer = str(cert_info.get("issuer") or "Verified CA")
        else:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, target_port), timeout=timeout) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                    cert = ssock.getpeercert()
            not_after_str = str(cert.get("notAfter") or "")
            exp_dt = datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            days_left = (exp_dt - datetime.now(timezone.utc)).days
            expires_iso = exp_dt.isoformat()
            issuer_tuples = cert.get("issuer") or ()
            issuer = ", ".join(f"{k}={v}" for rdn in issuer_tuples for k, v in rdn if k in ("organizationName", "commonName"))

        status = "ok"
        if days_left is not None:
            if days_left <= 0:
                status = "expired"
            elif days_left <= 14:
                status = "expiring_soon"

        return {
            "ok": True,
            "host": host,
            "port": target_port,
            "days_remaining": int(days_left) if days_left is not None else None,
            "expires_at": expires_iso,
            "issuer": issuer,
            "status": status,
        }
    except Exception as exc:
        return {
            "ok": False,
            "host": host,
            "error": f"TLS handshake failed: {type(exc).__name__}: {exc}",
            "status": "error",
        }


def analyze_failed_login_spike(
    failed_series: List[Dict[str, Any]],
    *,
    multiplier_threshold: float = 2.5,
    min_absolute_threshold: int = 15,
) -> Dict[str, Any]:
    """Detects brute-force / abnormal failed-login spikes against recent baseline cleanly."""
    if not failed_series:
        return {
            "ok": False,
            "spike_detected": False,
            "severity": "unknown",
            "message": "No failed-login time series supplied.",
        }

    values = [float(item.get("value", 0)) for item in failed_series]
    latest = values[-1]
    latest_period = str(failed_series[-1].get("period") or "latest")
    history = values[:-1] if len(values) > 1 else values
    baseline = sum(history) / max(1, len(history))
    ratio = (latest / baseline) if baseline > 0 else (latest if latest > 0 else 1.0)

    spike = (latest >= min_absolute_threshold) and (ratio >= multiplier_threshold)
    severity = "normal"
    if spike:
        severity = "high" if ratio >= 4.0 or latest >= 40 else "medium"

    return {
        "ok": True,
        "spike_detected": spike,
        "severity": severity,
        "latest_period": latest_period,
        "latest_failed_logins": int(latest),
        "baseline_avg": round(baseline, 2),
        "ratio": round(ratio, 2),
        "message": (
            f"Abnormal failed-login spike on {latest_period}: {int(latest)} attempts vs {baseline:.1f} baseline ({ratio:.1f}x)."
            if spike
            else f"Failed logins normal on {latest_period}: {int(latest)} attempts (baseline {baseline:.1f})."
        ),
    }


def audit_local_repo_dependencies(
    repo_path: str,
    *,
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
) -> Dict[str, Any]:
    """
    Runs non-destructive dependency vulnerability audit (`pip-audit` / `npm audit`) ONLY when given `repo_path`.
    Never modifies any file inside the target repo.
    """
    if not repo_path:
        return {"skipped": True, "reason": "No local repo_path supplied."}

    target = Path(repo_path).resolve()
    if not target.exists() or not target.is_dir():
        return {"ok": False, "skipped": False, "error": f"Path not found or not a directory: {target}"}

    exec_fn = runner or subprocess.run
    findings: List[Dict[str, Any]] = []

    req_file = target / "requirements.txt"
    pyproject_file = target / "pyproject.toml"
    if req_file.exists() or pyproject_file.exists():
        pip_audit_bin = shutil.which("pip-audit")
        if pip_audit_bin:
            cmd = [pip_audit_bin, "--format", "json"]
            if req_file.exists():
                cmd.extend(["-r", str(req_file)])
            try:
                proc = exec_fn(cmd, cwd=str(target), capture_output=True, text=True, timeout=45, check=False)
                raw = proc.stdout or ""
                try:
                    parsed = json.loads(raw) if raw.strip() else {}
                    deps = parsed.get("dependencies", []) if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])
                    vuln_cnt = sum(len(d.get("vulns", [])) for d in deps if isinstance(d, dict))
                except Exception:
                    vuln_cnt = -1
                findings.append({"ecosystem": "python", "tool": "pip-audit", "status": "checked", "vulnerabilities": vuln_cnt})
            except Exception as exc:
                findings.append({"ecosystem": "python", "tool": "pip-audit", "status": "error", "error": str(exc)})
        else:
            findings.append({"ecosystem": "python", "tool": "pip-audit", "status": "unavailable", "message": "pip-audit CLI not installed"})

    pkg_lock = target / "package-lock.json"
    pkg_json = target / "package.json"
    if pkg_lock.exists() or pkg_json.exists():
        npm_bin = shutil.which("npm")
        if npm_bin and pkg_lock.exists():
            try:
                proc = exec_fn([npm_bin, "audit", "--json"], cwd=str(target), capture_output=True, text=True, timeout=45, check=False)
                try:
                    parsed = json.loads(proc.stdout or "{}")
                    vulns_meta = parsed.get("metadata", {}).get("vulnerabilities", {})
                    vuln_cnt = sum(int(vulns_meta.get(k, 0)) for k in ("low", "moderate", "high", "critical"))
                except Exception:
                    vuln_cnt = -1
                findings.append({"ecosystem": "node", "tool": "npm audit", "status": "checked", "vulnerabilities": vuln_cnt})
            except Exception as exc:
                findings.append({"ecosystem": "node", "tool": "npm audit", "status": "error", "error": str(exc)})
        else:
            findings.append({
                "ecosystem": "node",
                "tool": "npm audit",
                "status": "skipped",
                "message": "Requires npm and package-lock.json cleanly.",
            })

    if not findings:
        return {"ok": True, "skipped": False, "repo_path": str(target), "audits": [], "message": "No requirements.txt, pyproject.toml, or package.json found."}
    return {"ok": True, "skipped": False, "repo_path": str(target), "audits": findings}


def security_check(
    *,
    base_url: Optional[str] = None,
    repo_path: str = "",
    window: str = "7d",
    config: Optional[SwiftSatsConfig] = None,
    header_probe: Optional[Callable[[str], Dict[str, str]]] = None,
    cert_probe: Optional[Callable[[str, int], Dict[str, Any]]] = None,
    http_get_fn: Optional[Callable[[str, str, float], Dict[str, Any]]] = None,
    dep_runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
) -> Dict[str, Any]:
    """
    Comprehensive read-only SwiftSats security audit:
    - HTTP security headers on public URL
    - TLS certificate expiry
    - Optional local repo dependency audit (pip-audit / npm audit)
    - Failed-login spike detector
    """
    cfg = config or load_swiftsats_config()
    target_url = (base_url if base_url is not None else cfg.base_url).strip()

    headers_res = (
        check_http_security_headers(target_url, header_probe=header_probe)
        if target_url
        else {"ok": False, "skipped": True, "reason": "SWIFTSATS_BASE_URL not configured"}
    )
    tls_res = (
        check_tls_certificate(target_url, cert_probe=cert_probe)
        if target_url
        else {"ok": False, "skipped": True, "reason": "SWIFTSATS_BASE_URL not configured"}
    )
    dep_res = audit_local_repo_dependencies(repo_path, runner=dep_runner) if repo_path else {"skipped": True, "reason": "No repo_path provided"}

    failed_res = failed_logins(normalize_window(window), config=cfg, http_get_fn=http_get_fn)
    if failed_res.get("ok") and isinstance(failed_res.get("data"), dict):
        spike_res = analyze_failed_login_spike(failed_res["data"].get("series", []))
        spike_res["is_mock"] = bool(failed_res.get("is_mock"))
    else:
        spike_res = {
            "ok": False,
            "spike_detected": False,
            "mode": cfg.mode,
            "message": NOT_CONNECTED_MSG if cfg.is_disabled else str(failed_res.get("message") or "Unavailable"),
        }

    return {
        "ok": True,
        "mode": cfg.mode,
        "is_mock": cfg.is_mock,
        "http_headers": headers_res,
        "tls_certificate": tls_res,
        "dependency_audit": dep_res,
        "failed_login_spike": spike_res,
    }

