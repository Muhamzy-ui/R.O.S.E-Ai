"""
R.O.S.E Phase 4 — Chrome Extension Autofill Copilot Manager.

Replaces the legacy `javascript:` bookmarklet installer with the Manifest V3
Chrome extension in `rose_autofill_extension/` and profile export `rose_profile.json`.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from core.result import Result

BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
PROFILE_FILE = MEMORY_DIR / "job_profile.json"
ROSE_PROFILE_JSON = BASE_DIR / "rose_profile.json"
EXTENSION_DIR = BASE_DIR / "rose_autofill_extension"
EXTENSION_MANIFEST = EXTENSION_DIR / "manifest.json"
EXTENSION_README = EXTENSION_DIR / "README.md"
LEGACY_INSTALLER = BASE_DIR / "rose_autofill_installer.html"

DEFAULT_CANDIDATE_PROFILE: dict[str, Any] = {
    "schema_version": 1,
    "first_name": "Mahmud",
    "last_name": "Bashir",
    "full_name": "Mahmud Bashir Olasunkanmi",
    "email": "mahmudolasunkami895@gmail.com",
    "phone": "+2348072410373",
    "location": "Abuja, Nigeria",
    "city": "Abuja",
    "country": "Nigeria",
    "github": "https://github.com/Muhamzy-ui",
    "linkedin": "https://linkedin.com/in/mahmud-olasunkanmi",
    "portfolio": "https://github.com/Muhamzy-ui",
    "website": "https://github.com/Muhamzy-ui",
    "years_experience": "3",
    "work_authorization": {
        "authorized_to_work": "Yes",
        "requires_sponsorship": "No",
        "relocation_required": "No",
        "remote_eligible": "Yes",
        "summary": (
            "Based in Abuja, Nigeria (WAT, UTC+1); authorized to work remotely as an "
            "independent contractor or full-time remote employee without visa sponsorship."
        ),
    },
    "project_highlights": (
        "1. R.O.S.E AI Desktop Assistant: Real-time multimodal Windows assistant "
        "(Python, Gemini Live API, PySide6, Node/Baileys sidecar).\n"
        "2. Full-Stack E-Commerce & Operations Platforms: Django REST Framework, JWT auth, "
        "Redis caching, React, PostgreSQL optimization (3.2s -> <50ms across 50,000+ rows).\n"
        "3. M.B.O Portfolio Platform: Django backend + responsive React frontend."
    ),
    "salary_expectation": "Competitive USD",
    "autofill_cover_letter": False,
    "cover_letter": "",
}


def load_profile() -> dict[str, Any]:
    """Loads the canonical autofill profile merged with `memory/job_profile.json`."""
    prof = dict(DEFAULT_CANDIDATE_PROFILE)
    if ROSE_PROFILE_JSON.exists():
        try:
            saved = json.loads(ROSE_PROFILE_JSON.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                for k, v in saved.items():
                    if v not in (None, ""):
                        prof[k] = v
        except Exception:
            pass

    if PROFILE_FILE.exists():
        try:
            mem = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
            if isinstance(mem, dict):
                for key in ("first_name", "last_name", "full_name", "email", "phone", "location", "github", "linkedin", "website"):
                    if mem.get(key):
                        prof[key] = mem[key]
                if mem.get("website") and not prof.get("portfolio"):
                    prof["portfolio"] = mem["website"]
        except Exception:
            pass

    return prof


def export_rose_profile(output_path: Path | None = None) -> Result:
    """Writes `rose_profile.json` cleanly and verifies disk persistence."""
    target = output_path or ROSE_PROFILE_JSON
    prof = load_profile()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(prof, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if target.exists() and target.stat().st_size > 0:
            return Result.success(
                data={"path": str(target), "fields": len(prof)},
                verified=True,
                message=f"Exported rose_profile.json ({target}).",
            )
        return Result.failure(f"Failed to verify exported profile at {target}.")
    except Exception as exc:
        return Result.failure(f"Could not export rose_profile.json: {exc}")


def verify_extension_bundle() -> Result:
    """Checks that the Manifest V3 Chrome extension bundle is intact on disk."""
    required = [
        EXTENSION_MANIFEST,
        EXTENSION_DIR / "background.js",
        EXTENSION_DIR / "content.js",
        EXTENSION_DIR / "popup.html",
        EXTENSION_DIR / "popup.js",
        EXTENSION_DIR / "src" / "dom_utils.js",
        EXTENSION_DIR / "src" / "adapters.js",
        EXTENSION_DIR / "src" / "autofill_engine.js",
        EXTENSION_DIR / "styles" / "overlay.css",
        EXTENSION_README,
    ]
    missing = [str(p.relative_to(BASE_DIR)) for p in required if not p.exists()]
    if missing:
        return Result.failure(
            f"Missing extension files: {', '.join(missing)}",
            data={"missing": missing},
        )
    try:
        manifest = json.loads(EXTENSION_MANIFEST.read_text(encoding="utf-8"))
        if manifest.get("manifest_version") != 3:
            return Result.failure("manifest.json must declare manifest_version == 3.")
    except Exception as exc:
        return Result.failure(f"Invalid extension manifest.json: {exc}")

    return Result.success(
        data={"extension_dir": str(EXTENSION_DIR), "version": manifest.get("version")},
        verified=True,
        message=f"Manifest V3 extension verified at {EXTENSION_DIR}.",
    )


def _find_chrome_exe() -> str | None:
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for cand in candidates:
        if cand and os.path.exists(cand):
            return cand
    return None


def open_extension_setup() -> str:
    """
    Syncs `rose_profile.json`, verifies `rose_autofill_extension/`, and opens Chrome's
    extensions page (and/or local setup readme) without broken bookmarklets.
    """
    prof_res = export_rose_profile()
    ext_res = verify_extension_bundle()
    if not ext_res.ok:
        return f"Extension bundle check failed: {ext_res.error}"

    chrome_exe = _find_chrome_exe()
    opened_target = "chrome://extensions"
    if chrome_exe:
        try:
            subprocess.Popen(
                [chrome_exe, "chrome://extensions"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            opened_target = str(EXTENSION_README)
    else:
        readme_url = f"file:///{EXTENSION_README.as_posix()}"
        try:
            from actions.browser import browser_control
            browser_control("open_url", url=readme_url)
            opened_target = readme_url
        except Exception:
            opened_target = str(EXTENSION_README)

    return (
        f"R.O.S.E Manifest V3 Chrome Extension ready at {EXTENSION_DIR} "
        f"(profile synced to {prof_res.data.get('path', ROSE_PROFILE_JSON)}). "
        f"Opened {opened_target}. Load unpacked from {EXTENSION_DIR}, import rose_profile.json "
        "in the popup, and press Alt+Shift+F or click 'ROSE Fill' on any job application."
    )


# Legacy name alias kept so callers in main.py and job_command_center.py keep working cleanly
def open_installer_in_chrome() -> str:
    return open_extension_setup()
