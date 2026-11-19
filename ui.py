"""Compatibility entrypoint for root `ui` imports.

Routes to classic `legacy.ui_legacy.JarvisUI` by default, or PySide6 `ui.main_window.JarvisUI`
when launched with `--modern` or `ROSE_MODERN_UI=1`.
"""

from __future__ import annotations

from ui import JarvisUI, is_legacy_ui_requested, is_modern_ui_requested  # noqa: F401
from ui.sound import SoundManager  # noqa: F401

__all__ = ["JarvisUI", "SoundManager", "is_legacy_ui_requested", "is_modern_ui_requested"]
