"""R.O.S.E (Project Mirror) UI Package.

Defaults to the classic UI (`legacy.ui_legacy.JarvisUI`) per user preference while the
next modern UI design is being chosen. Pass `--modern` or set `ROSE_MODERN_UI=1` to
preview the PySide6 UI (`ui.main_window.JarvisUI`).
"""

from __future__ import annotations

import os
import sys
from typing import Any


def is_modern_ui_requested() -> bool:
    if "--legacy" in sys.argv:
        return False
    if str(os.environ.get("ROSE_LEGACY_UI", "")).strip() in ("1", "true", "TRUE", "yes"):
        return False
    try:
        import PySide6  # noqa: F401
    except Exception:
        return False
    return True


def is_legacy_ui_requested() -> bool:
    return not is_modern_ui_requested()


def __getattr__(name: str) -> Any:
    if name in ("JarvisUI", "SoundManager", "setup_tray"):
        if is_legacy_ui_requested():
            from legacy import ui_legacy

            return getattr(ui_legacy, name)
        if name == "JarvisUI":
            from ui.main_window import JarvisUI

            return JarvisUI
        if name == "SoundManager":
            from ui.sound import SoundManager

            return SoundManager
        return None
    raise AttributeError(f"module 'ui' has no attribute {name!r}")


__all__ = ["JarvisUI", "is_legacy_ui_requested", "is_modern_ui_requested"]
