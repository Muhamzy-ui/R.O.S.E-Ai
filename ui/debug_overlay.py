"""Hidden Ctrl+Shift+D performance debug overlay for R.O.S.E PySide6 UI."""

from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from ui.theme import PALETTE


class DebugOverlayWidget(QFrame):
    """Lightweight floating telemetry card measuring FPS, p95 frame time, CPU, RAM, & queues."""

    def __init__(
        self,
        stats_provider: Optional[Callable[[], dict[str, Any]]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._stats_provider = stats_provider
        self.setObjectName("DebugOverlay")
        self.setFixedWidth(286)
        self.setStyleSheet(
            "QFrame#DebugOverlay {"
            "  background-color: rgba(8, 12, 22, 0.90);"
            "  border: 1px solid rgba(34, 211, 238, 0.36);"
            "  border-radius: 12px;"
            "}"
        )

        self._labels: dict[str, QLabel] = {}
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh_now)
        self.setVisible(False)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        hdr = QLabel("PERF DEBUG OVERLAY (Ctrl+Shift+D)", self)
        hdr.setStyleSheet(
            f"font-size: 9px; font-weight: 700; letter-spacing: 1.2px; color: {PALETTE.accent_cyan};"
        )
        root.addWidget(hdr)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(3)

        rows = [
            ("backend", "Backend"),
            ("state", "State"),
            ("fps", "FPS / Target"),
            ("frame_ms", "Frame avg / p95"),
            ("cpu_pct", "UI CPU%"),
            ("ram_mb", "ROSE RAM"),
            ("sys_ram", "System RAM"),
            ("audio_hz", "Audio signal"),
            ("queues", "Queues (msg/msgs)"),
        ]
        for row_idx, (key, title) in enumerate(rows):
            k_lbl = QLabel(title, self)
            k_lbl.setStyleSheet(f"font-size: 10px; color: {PALETTE.text_muted};")
            v_lbl = QLabel("--", self)
            v_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            v_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 600; color: {PALETTE.text_primary};"
                'font-family: "Cascadia Mono", "Consolas", monospace;'
            )
            grid.addWidget(k_lbl, row_idx, 0)
            grid.addWidget(v_lbl, row_idx, 1)
            self._labels[key] = v_lbl

        root.addLayout(grid)

    def set_stats_provider(self, provider: Callable[[], dict[str, Any]]) -> None:
        self._stats_provider = provider

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_now()
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if self._timer.isActive():
            self._timer.stop()

    def toggle(self) -> bool:
        nxt = not self.isVisible()
        self.setVisible(nxt)
        if nxt:
            self.raise_()
        return nxt

    def refresh_now(self) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        if self._stats_provider is not None:
            try:
                stats = dict(self._stats_provider() or {})
            except Exception:
                stats = {}

        self._labels["backend"].setText(str(stats.get("backend", "--")))
        self._labels["state"].setText(str(stats.get("state", "IDLE")))

        actual_fps = float(stats.get("actual_fps", 0.0) or 0.0)
        target_fps = int(stats.get("target_fps", 0) or 0)
        self._labels["fps"].setText(f"{actual_fps:.1f} / {target_fps} fps")

        avg_ms = float(stats.get("avg_frame_ms", 0.0) or 0.0)
        p95_ms = float(stats.get("p95_frame_ms", 0.0) or 0.0)
        self._labels["frame_ms"].setText(f"{avg_ms:.2f} / {p95_ms:.2f} ms")
        p95_color = PALETTE.success if p95_ms < 16.0 else (PALETTE.warning if p95_ms < 24.0 else PALETTE.danger)
        self._labels["frame_ms"].setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {p95_color};"
            'font-family: "Cascadia Mono", "Consolas", monospace;'
        )

        ui_cpu = float(stats.get("ui_cpu_pct", 0.0) or 0.0)
        self._labels["cpu_pct"].setText(f"{ui_cpu:.2f}%")

        ram_mb = float(stats.get("proc_ram_mb", 0.0) or 0.0)
        self._labels["ram_mb"].setText(f"{ram_mb:.1f} MB")

        sys_ram = float(stats.get("sys_ram_pct", 0.0) or 0.0)
        self._labels["sys_ram"].setText(f"{sys_ram:.0f}%")

        audio_hz = float(stats.get("audio_hz", 0.0) or 0.0)
        self._labels["audio_hz"].setText(f"{audio_hz:.1f} Hz")

        pending_q = int(stats.get("pending_log_queue", 0) or 0)
        msg_count = int(stats.get("message_count", 0) or 0)
        self._labels["queues"].setText(f"{pending_q} queued / {msg_count} rows")
        return stats


__all__ = ["DebugOverlayWidget"]

