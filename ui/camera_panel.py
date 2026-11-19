"""
R.O.S.E (Project Mirror) — MIRROR K2 Camera Vision HUD Panel.

Event-driven (zero idle camera polling!): receives crisp still previews and
vision results after `camera_vision` runs, while preserving 1-click IDENTIFY,
SAVE (Remember), and LIVE controls in the chamfered glowing HUD style.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.theme import HudDot, HudFrame, METRICS, PALETTE


class _CameraPanelBridge(QObject):
    capture_received = Signal(str, object)
    action_finished = Signal(bool, str)


class CameraPreviewPanel(HudFrame):
    """Chamfered HUD CAMERA VISION module with event-driven capture updates."""

    def __init__(
        self,
        on_action: Optional[Callable[[str, str], Any]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(accent=PALETTE.accent_cyan, chamfer=9.0, fill_alpha=178, parent=parent)
        self.setObjectName("CameraVisionCard")
        self._on_action = on_action
        self._last_payload: dict[str, Any] = {}
        self._update_count = 0

        self._bridge = _CameraPanelBridge()
        self._bridge.capture_received.connect(self._apply_capture_ui)
        self._bridge.action_finished.connect(self._on_action_status)

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 9, 12, 10)
        root.setSpacing(6)

        # Header row: ● CAMERA VISION   [IDLE]
        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        dot = HudDot(PALETTE.accent_cyan, diameter=6, parent=self)
        title = QLabel("CAMERA VISION", self)
        title.setStyleSheet(
            f"color: {PALETTE.text_secondary}; font-family: {METRICS.font_family_display}; "
            "font-size: 10px; font-weight: 700; letter-spacing: 1.4px; background: transparent;"
        )
        self.badge_lbl = QLabel("IDLE", self)
        self.badge_lbl.setStyleSheet(
            f"color: {PALETTE.text_secondary}; background-color: rgba(6, 28, 58, 0.85); "
            f"border: 1px solid {PALETTE.border_subtle}; border-radius: 3px; "
            "padding: 1px 6px; font-size: 9px; font-weight: 700; letter-spacing: 0.7px;"
        )
        hdr.addWidget(dot)
        hdr.addWidget(title)
        hdr.addStretch(1)
        hdr.addWidget(self.badge_lbl)
        root.addLayout(hdr)

        # Body row: Thumbnail + Copy
        body = QHBoxLayout()
        body.setSpacing(9)

        self.thumb_lbl = QLabel("NO STILL", self)
        self.thumb_lbl.setFixedSize(84, 52)
        self.thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_lbl.setStyleSheet(
            f"color: {PALETTE.text_muted}; background-color: rgba(3, 12, 26, 0.92); "
            f"border: 1px solid {PALETTE.border_subtle}; border-radius: 4px; "
            "font-size: 9px; font-weight: 700; letter-spacing: 0.7px;"
        )
        body.addWidget(self.thumb_lbl)

        copy_col = QVBoxLayout()
        copy_col.setSpacing(2)
        self.title_lbl = QLabel('Hold item & ask "What is this?"', self)
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setStyleSheet(
            f"color: {PALETTE.text_primary}; font-size: 11px; font-weight: 600; background: transparent;"
        )
        self.detail_lbl = QLabel("Sharp still captures on demand", self)
        self.detail_lbl.setWordWrap(True)
        self.detail_lbl.setStyleSheet(
            f"color: {PALETTE.text_muted}; font-size: 10px; background: transparent;"
        )
        copy_col.addWidget(self.title_lbl)
        copy_col.addWidget(self.detail_lbl)
        copy_col.addStretch(1)
        body.addLayout(copy_col, 1)
        root.addLayout(body)

        # Action strip: IDENTIFY | LIVE (+ compact SAVE)
        btns = QHBoxLayout()
        btns.setSpacing(6)
        self.identify_btn = self._make_pill("IDENTIFY", primary=True)
        self.live_btn = self._make_pill("LIVE", primary=False)
        self.remember_btn = self._make_pill("SAVE", primary=False)
        self.remember_btn.setFixedWidth(48)
        self.remember_btn.setToolTip("Remember this object in camera memory")

        self.identify_btn.clicked.connect(self._on_click_identify)
        self.remember_btn.clicked.connect(self._on_click_remember)
        self.live_btn.clicked.connect(self._on_click_live)

        btns.addWidget(self.identify_btn, 1)
        btns.addWidget(self.live_btn, 1)
        btns.addWidget(self.remember_btn)
        root.addLayout(btns)

    def _make_pill(self, text: str, primary: bool = False) -> QPushButton:
        btn = QPushButton(text, self)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(24)
        if primary:
            btn.setStyleSheet(
                "QPushButton {"
                "  color: #EAF7FF;"
                "  background-color: rgba(14, 78, 128, 0.58);"
                f"  border: 1px solid {PALETTE.border_medium};"
                "  border-radius: 3px;"
                "  padding: 2px 8px;"
                f"  font-family: {METRICS.font_family_display};"
                "  font-size: 10px; font-weight: 700; letter-spacing: 0.9px;"
                "}"
                f"QPushButton:hover {{ border-color: {PALETTE.accent_cyan}; background-color: rgba(24, 118, 178, 0.72); }}"
            )
        else:
            btn.setStyleSheet(
                f"QPushButton {{"
                f"  color: {PALETTE.text_secondary};"
                "  background-color: rgba(5, 22, 46, 0.78);"
                f"  border: 1px solid {PALETTE.border_subtle};"
                "  border-radius: 3px;"
                "  padding: 2px 8px;"
                f"  font-family: {METRICS.font_family_display};"
                "  font-size: 10px; font-weight: 700; letter-spacing: 0.9px;"
                "}"
                f"QPushButton:hover {{ border-color: {PALETTE.accent_cyan}; color: {PALETTE.text_primary}; background-color: rgba(12, 44, 82, 0.85); }}"
            )
        return btn

    def update_from_capture(self, preview_path: str, payload: dict[str, Any]) -> None:
        self._bridge.capture_received.emit(str(preview_path or ""), dict(payload or {}))

    def _apply_capture_ui(self, preview_path: str, payload: dict[str, Any]) -> None:
        self._update_count += 1
        self._last_payload = payload or {}
        if preview_path and Path(preview_path).exists():
            pix = QPixmap(preview_path)
            if not pix.isNull():
                scaled = pix.scaled(
                    self.thumb_lbl.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.thumb_lbl.setPixmap(scaled)
                self.thumb_lbl.setText("")

        status = str(payload.get("status") or "identified")
        conf = int(payload.get("confidence", 0) or 0)
        title = str(payload.get("title") or "Unknown item")
        evidence = [str(e) for e in (payload.get("evidence") or []) if str(e).strip()]
        suggestion = str(payload.get("suggestion") or "").strip()

        self.title_lbl.setText(title)
        self.identify_btn.setText("REANALYZE" if status == "low_conf" else "IDENTIFY")

        if status == "low_conf":
            self.badge_lbl.setText(f"LOW ({conf}%)")
            self.badge_lbl.setStyleSheet(
                f"color: {PALETTE.warning}; background-color: {PALETTE.warning_soft}; "
                f"border: 1px solid {PALETTE.warning}; border-radius: 3px; padding: 1px 6px; font-size: 9px; font-weight: 700;"
            )
            self.detail_lbl.setText(suggestion or "Move closer or adjust lighting.")
        elif status == "saved":
            self.badge_lbl.setText("SAVED")
            self.badge_lbl.setStyleSheet(
                f"color: {PALETTE.accent_cyan}; background-color: {PALETTE.info_soft}; "
                f"border: 1px solid {PALETTE.accent_cyan}; border-radius: 3px; padding: 1px 6px; font-size: 9px; font-weight: 700;"
            )
            self.detail_lbl.setText(suggestion or "Remembered in camera memory.")
        elif status == "error":
            self.badge_lbl.setText("ERR")
            self.badge_lbl.setStyleSheet(
                f"color: {PALETTE.danger}; background-color: {PALETTE.danger_soft}; "
                f"border: 1px solid {PALETTE.danger}; border-radius: 3px; padding: 1px 6px; font-size: 9px; font-weight: 700;"
            )
            self.detail_lbl.setText(suggestion or "Check webcam connection.")
        else:
            self.badge_lbl.setText(f"{conf}% SURE" if conf else "IDENTIFIED")
            self.badge_lbl.setStyleSheet(
                f"color: {PALETTE.success}; background-color: {PALETTE.success_soft}; "
                f"border: 1px solid {PALETTE.success}; border-radius: 3px; padding: 1px 6px; font-size: 9px; font-weight: 700;"
            )
            self.detail_lbl.setText(" · ".join(evidence[:2]) or "Captured cleanly.")

    def _run_bg(self, action: str, query: str = "") -> None:
        self.identify_btn.setEnabled(False)

        def _worker():
            try:
                if self._on_action:
                    self._on_action(action, query)
                else:
                    from actions.camera_vision import camera_vision_result

                    res = camera_vision_result(action, query)
                    self.update_from_capture(res.get("preview_path", ""), res)
                self._bridge.action_finished.emit(True, "")
            except Exception as exc:
                self._bridge.action_finished.emit(False, str(exc))

        threading.Thread(target=_worker, daemon=True, name=f"rose-cam-{action}").start()

    def _on_action_status(self, ok: bool, msg: str) -> None:
        self.identify_btn.setEnabled(True)
        if not ok and msg:
            self.detail_lbl.setText(msg[:72])

    def _on_click_identify(self) -> None:
        act = "reanalyze" if self._last_payload.get("status") == "low_conf" else "identify"
        self._run_bg(act)

    def _on_click_remember(self) -> None:
        label, ok = QInputDialog.getText(self, "Remember Item", "What should R.O.S.E remember this item as?")
        if ok and str(label or "").strip():
            self._run_bg("remember", str(label).strip())

    def _on_click_live(self) -> None:
        self._run_bg("live")
