"""
PC Performance, Memory Optimizer & Diagnostics for R.O.S.E (Windows).
- health_report / top_apps: CPU, RAM, disk, battery, and grouped top RAM-consuming apps
- trim_ram: Safely flushes idle working-set pages via Win32 EmptyWorkingSet without closing apps
- close_heavy_app: Terminates a specific heavy non-system app (requires confirmed=True)
- clean_temp: Clears old files in Windows %TEMP% (requires confirmed=True)
- network_check: Checks internet connectivity and HTTP/DNS latency
"""

from __future__ import annotations

import ctypes
import os
import shutil
import time
from collections import defaultdict
from pathlib import Path

import psutil
import requests

_CRITICAL_PROCESSES = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe",
    "wininit.exe", "services.exe", "lsass.exe", "svchost.exe", "winlogon.exe",
    "dwm.exe", "explorer.exe", "taskmgr.exe", "audiodg.exe", "fontdrvhost.exe",
    "memory compression", "sihost.exe", "ctfmon.exe", "conhost.exe",
    "python.exe", "pythonw.exe",
}

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_SET_QUOTA = 0x0100


def _grouped_memory_usage(limit: int = 8) -> list[tuple[str, float, int]]:
    totals_mb: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for proc in psutil.process_iter(["name", "memory_info"]):
        try:
            name = (proc.info.get("name") or "").strip()
            mi = proc.info.get("memory_info")
            if not name or not mi:
                continue
            mb = mi.rss / (1024 * 1024)
            totals_mb[name] += mb
            counts[name] += 1
        except Exception:
            continue
    ranked = sorted(totals_mb.items(), key=lambda x: x[1], reverse=True)
    return [(name, mb, counts[name]) for name, mb in ranked[:limit]]


def _trim_working_sets() -> tuple[int, int]:
    """Calls Win32 EmptyWorkingSet on accessible user processes cleanly."""
    k32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    trimmed = 0
    before_mb = psutil.virtual_memory().used / (1024 * 1024)
    my_pid = os.getpid()

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pid = int(proc.info["pid"])
            name = (proc.info.get("name") or "").lower()
            if pid <= 4 or pid == my_pid or name in _CRITICAL_PROCESSES:
                continue
            handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA, False, pid)
            if handle:
                try:
                    if psapi.EmptyWorkingSet(handle):
                        trimmed += 1
                finally:
                    k32.CloseHandle(handle)
        except Exception:
            continue

    after_mb = psutil.virtual_memory().used / (1024 * 1024)
    freed_mb = max(0, int(before_mb - after_mb))
    return trimmed, freed_mb


def pc_optimizer(
    action: str = "health_report",
    target: str = "",
    confirmed: bool = False,
) -> str:
    act = (action or "health_report").strip().lower()
    tgt = (target or "").strip()

    if act in ("health_report", "top_apps", "status", "check_ram", "slow"):
        vm = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.25)
        disk = psutil.disk_usage("C:\\")
        hogs = _grouped_memory_usage(limit=6)
        hog_str = ", ".join(
            f"{name} ({mb:.0f} MB" + (f" across {cnt} procs)" if cnt > 1 else ")")
            for name, mb, cnt in hogs
        )
        batt = psutil.sensors_battery()
        batt_str = (
            f"{batt.percent:.0f}% ({'charging' if batt.power_plugged else 'on battery'})"
            if batt else "AC/Unknown"
        )
        return (
            f"PC Performance Report: CPU {cpu:.0f}% | RAM {vm.percent:.0f}% "
            f"({vm.used / (1024**3):.1f} GB / {vm.total / (1024**3):.1f} GB used) | "
            f"C: Free {disk.free / (1024**3):.1f} GB | Battery {batt_str}. "
            f"Top RAM consumers: {hog_str}."
        )

    if act in ("trim_ram", "free_memory", "boost_ram", "optimize_ram", "free_ram"):
        vm_before = psutil.virtual_memory()
        trimmed, freed_mb = _trim_working_sets()
        vm_after = psutil.virtual_memory()
        return (
            f"RAM optimization complete: trimmed idle memory across {trimmed} background processes. "
            f"Memory usage went from {vm_before.percent:.0f}% to {vm_after.percent:.0f}% "
            f"(~{freed_mb} MB immediately reclaimed, {vm_after.available / (1024**3):.1f} GB available)."
        )

    if act in ("close_heavy_app", "kill_app", "close_app"):
        if not tgt:
            hogs = _grouped_memory_usage(limit=5)
            hog_list = ", ".join(f"{n} ({m:.0f} MB)" for n, m, _ in hogs)
            return f"Specify which heavy app to close. Top consumers right now: {hog_list}."
        needle = tgt.lower().replace(".exe", "")
        if needle in _CRITICAL_PROCESSES or f"{needle}.exe" in _CRITICAL_PROCESSES or "antigravity" in needle:
            return f"Safety block: '{tgt}' is a protected system or active workspace process."
        if not confirmed:
            return (
                f"CONFIRMATION REQUIRED: Closing all processes matching '{tgt}' may lose unsaved work in that app. "
                f"Ask the user to confirm first, then call pc_optimizer(action='close_heavy_app', target='{tgt}', confirmed=True)."
            )
        closed = 0
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pname = (proc.info.get("name") or "").lower()
                if needle in pname and pname not in _CRITICAL_PROCESSES:
                    proc.terminate()
                    closed += 1
            except Exception:
                continue
        return (
            f"Terminated {closed} process(es) matching '{tgt}'."
            if closed else f"No running non-system processes matched '{tgt}'."
        )

    if act in ("clean_temp", "cleanup_temp", "clear_cache"):
        if not confirmed:
            return (
                "CONFIRMATION REQUIRED: Cleaning Windows temporary files will delete old temporary files older than 2 hours "
                "in your Temp folder. Ask the user to confirm, then call pc_optimizer(action='clean_temp', confirmed=True)."
            )
        temp_dir = Path(os.environ.get("TEMP", ""))
        if not temp_dir.exists():
            return "Windows Temp directory could not be located."
        cutoff = time.time() - 7200
        removed_files = 0
        freed_bytes = 0
        for entry in list(temp_dir.iterdir()):
            try:
                st = entry.stat()
                if st.st_mtime > cutoff:
                    continue
                if entry.is_file() or entry.is_symlink():
                    size = st.st_size
                    entry.unlink()
                    removed_files += 1
                    freed_bytes += size
                elif entry.is_dir():
                    dir_size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                    shutil.rmtree(entry, ignore_errors=False)
                    removed_files += 1
                    freed_bytes += dir_size
            except Exception:
                continue
        return f"Temp cleanup complete: removed {removed_files} old items and freed {freed_bytes / (1024 * 1024):.1f} MB."

    if act in ("network_check", "speed_check", "ping"):
        t0 = time.perf_counter()
        try:
            r = requests.get("https://www.google.com/generate_204", timeout=5)
            ms = int((time.perf_counter() - t0) * 1000)
            return f"Internet connection is online (HTTP {r.status_code}, latency ~{ms} ms)."
        except Exception as exc:
            return f"Internet check failed: {exc}"

    return f"Unknown pc_optimizer action: {action}"

