"""
R.O.S.E (Project Mirror) — MIRROR K2 Mini Floating Orb Pill.

Always-on-top translucent chamfered HUD capsule with live 3D OrbWidget,
synced state/audio levels, and single-click LIVE/MUTE, PAUSE, and FULL restore.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui.orb_widget import OrbWidget
from ui.theme import METRICS, PALETTE, draw_hud_panel


class MiniOrbWindow(QWidget):
    """Always-on-top compact MIRROR HUD widget hosting a live OrbWidget."""

    def __init__(
        self,
        on_restore: Optional[Callable[[], Any]] = None,
        on_toggle_mute: Optional[Callable[[], Any]] = None,
        on_toggle_call: Optional[Callable[[], Any]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._on_restore = on_restore
        self._on_toggle_mute = on_toggle_mute
        self._on_toggle_call = on_toggle_call
        self._drag_pos: Optional[QPoint] = None
        self._muted = False

        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(256, 108)
        self.setWindowTitle("R.O.S.E Mini Orb")

        self._build_ui()

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._on_anim_tick)

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        self.orb = OrbWidget(self)
        self.orb.setFixedSize(82, 82)
        self.orb.setMinimumSize(82, 82)
        root.addWidget(self.orb)

        right = QVBoxLayout()
        right.setSpacing(5)

        hdr = QHBoxLayout()
        self.title_lbl = QLabel("R.O.S.E", self)
        self.title_lbl.setStyleSheet(
            f"font-family: {METRICS.font_family_display}; font-size: 11px; "
            f"font-weight: 700; letter-spacing: 1.6px; color: {PALETTE.accent_cyan};"
        )
        self.state_lbl = QLabel("ONLINE", self)
        self.state_lbl.setStyleSheet(
            f"font-size: 9.5px; font-weight: 700; color: {PALETTE.text_secondary};"
        )
        hdr.addWidget(self.title_lbl)
        hdr.addStretch(1)
        hdr.addWidget(self.state_lbl)
        right.addLayout(hdr)

        btns = QHBoxLayout()
        btns.setSpacing(5)
        self.mute_btn = QPushButton("MUTE", self)
        self.mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mute_btn.setStyleSheet(self._pill_style())
        self.mute_btn.clicked.connect(self._handle_mute)

        self.call_btn = QPushButton("LIVE", self)
        self.call_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.call_btn.setStyleSheet(self._pill_style(accent=True))
        self.call_btn.clicked.connect(self._handle_call)

        self.restore_btn = QPushButton("FULL", self)
        self.restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restore_btn.setStyleSheet(self._pill_style())
        self.restore_btn.clicked.connect(self._handle_restore)

        btns.addWidget(self.mute_btn)
        btns.addWidget(self.call_btn)
        btns.addWidget(self.restore_btn)
        right.addLayout(btns)
        right.addStretch(1)
        root.addLayout(right, 1)

    @staticmethod
    def _pill_style(accent: bool = False) -> str:
        border = PALETTE.border_medium if accent else PALETTE.border_subtle
        fg = "#46F0FF" if accent else PALETTE.text_primary
        bg = "rgba(12, 64, 110, 0.72)" if accent else "rgba(5, 22, 46, 0.85)"
        return (
            "QPushButton {"
            f"  background-color: {bg};"
            f"  border: 1px solid {border};"
            "  border-radius: 3px;"
            f"  color: {fg};"
            f"  font-family: {METRICS.font_family_display};"
            "  font-size: 9.5px; font-weight: 700; letter-spacing: 0.7px;"
            "  padding: 4px 6px;"
            "}"
            f"QPushButton:hover {{ border-color: {PALETTE.accent_cyan}; }}"
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        draw_hud_panel(painter, QRectF(self.rect()), accent=PALETTE.accent_cyan, fill_alpha=215, chamfer=10.0)

    def set_state(self, state: str) -> None:
        self.orb.set_state(state)
        self.state_lbl.setText(str(state or "ONLINE").upper())
        self._sync_timer_cadence()

    def set_audio_level(self, level: float) -> None:
        self.orb.set_audio_level(level)

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self.mute_btn.setText("UNMUTE" if self._muted else "MUTE")

    def set_call_active(self, active: bool) -> None:
        self.orb.set_call_active(active)
        self.call_btn.setText("END" if active else "LIVE")
        self._sync_timer_cadence()

    def _sync_timer_cadence(self) -> None:
        if not self.isVisible():
            return
        fps = self.orb.target_fps()
        interval = 16 if fps >= 50 else 42
        if self._anim_timer.interval() != interval:
            self._anim_timer.setInterval(interval)

    def _on_anim_tick(self) -> None:
        self.orb.animate_step(self._anim_timer.interval() / 1000.0)
        self._sync_timer_cadence()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._anim_timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._anim_timer.stop()

    def _handle_mute(self) -> None:
        if self._on_toggle_mute:
            self._on_toggle_mute()

    def _handle_call(self) -> None:
        if self._on_toggle_call:
            self._on_toggle_call()

    def _handle_restore(self) -> None:
        if self._on_restore:
            self._on_restore()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._handle_restore()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
