"""WhatsApp Linked Devices QR Pairing Dialog for R.O.S.E PySide6 UI."""

from __future__ import annotations

import base64
import threading
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from actions.whatsapp_bridge import get_whatsapp_status, start_sidecar
from ui.theme import PALETTE, build_shared_qss


class WhatsAppQRDialog(QDialog):
    """Displays the live Baileys QR code so the user can pair WhatsApp from their phone."""

    def __init__(
        self,
        on_log: Optional[Callable[[str], None]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._on_log = on_log
        self._last_qr = ""
        self._connected_notified = False
        self._starting_sidecar = False

        self.setWindowTitle("R.O.S.E — Link WhatsApp")
        self.setModal(False)
        self.setFixedSize(410, 480)
        self.setStyleSheet(build_shared_qss())

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        title = QLabel("WHATSAPP LINKED DEVICES", self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"font-size: 16px; font-weight: 700; letter-spacing: 1.4px; color: {PALETTE.accent_cyan};"
        )
        root.addWidget(title)

        self.status_lbl = QLabel("Checking WhatsApp sidecar...", self)
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {PALETTE.text_primary};")
        root.addWidget(self.status_lbl)

        self.qr_frame = QLabel("Waiting for QR code...", self)
        self.qr_frame.setFixedSize(276, 276)
        self.qr_frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_frame.setStyleSheet(
            "QLabel { background-color: #FFFFFF; color: #0F172A; border: 2px solid #26E6FF;"
            "  border-radius: 10px; font-weight: 600; }"
        )
        root.addWidget(self.qr_frame, 0, Qt.AlignmentFlag.AlignCenter)

        steps = QLabel(
            "1. Open WhatsApp on your phone\n"
            "2. Settings / Menu -> Linked Devices -> Link a Device\n"
            "3. Point your camera at the QR code above",
            self,
        )
        steps.setAlignment(Qt.AlignmentFlag.AlignCenter)
        steps.setStyleSheet(f"font-size: 11px; color: {PALETTE.text_secondary};")
        root.addWidget(steps)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.retry_btn = QPushButton("Retry Sidecar", self)
        self.retry_btn.clicked.connect(self._ensure_started_async)
        row.addWidget(self.retry_btn)

        close_btn = QPushButton("Close", self)
        close_btn.setObjectName("AccentButton")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        root.addLayout(row)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1200)
        self._poll_timer.timeout.connect(self._poll_status)
        self._poll_timer.start()

        self._ensure_started_async()
        QTimer.singleShot(150, self._poll_status)

    def _ensure_started_async(self) -> None:
        if self._starting_sidecar:
            return
        self._starting_sidecar = True
        self.status_lbl.setText("Starting local WhatsApp bridge...")

        def _worker() -> None:
            try:
                start_sidecar()
            finally:
                self._starting_sidecar = False

        threading.Thread(target=_worker, daemon=True, name="rose-wa-start").start()

    def _poll_status(self) -> None:
        if not self.isVisible():
            return
        st = get_whatsapp_status()
        if not st.get("running"):
            if not self._starting_sidecar:
                self.status_lbl.setText(st.get("error") or "Starting WhatsApp sidecar on port 48201...")
            return

        if st.get("connected"):
            user = st.get("user") or {}
            uid = str(user.get("id") or "Linked Account").split(":")[0]
            self.status_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #34D399;")
            self.status_lbl.setText(f"Connected to WhatsApp as +{uid}")
            self.qr_frame.setPixmap(QPixmap())
            self.qr_frame.setStyleSheet(
                "QLabel { background-color: rgba(16, 185, 129, 32); color: #34D399;"
                "  border: 2px solid #34D399; border-radius: 10px; font-size: 14px; font-weight: 700; }"
            )
            self.qr_frame.setText("WHATSAPP LINKED\nReady to read & send messages.")
            if not self._connected_notified:
                self._connected_notified = True
                if self._on_log:
                    self._on_log(f"SYS: WhatsApp connected as +{uid}.")
            self._poll_timer.stop()
            return

        if st.get("paired") and not st.get("connected"):
            self.status_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #F59E0B;")
            self.status_lbl.setText("Paired — reconnecting to WhatsApp servers...")
            return

        qr_data_url = str(st.get("qrDataUrl") or "")
        qr_raw = str(st.get("qr") or "")
        if qr_data_url.startswith("data:image/") and "," in qr_data_url:
            if qr_raw != self._last_qr:
                try:
                    raw_bytes = base64.b64decode(qr_data_url.split(",", 1)[1])
                    pix = QPixmap()
                    if pix.loadFromData(raw_bytes):
                        self.qr_frame.setText("")
                        self.qr_frame.setPixmap(
                            pix.scaled(
                                256,
                                256,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.FastTransformation,
                            )
                        )
                        self._last_qr = qr_raw
                except Exception:
                    pass
            self.status_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {PALETTE.accent_cyan};")
            self.status_lbl.setText("Scan this QR code with WhatsApp on your phone")
        else:
            self.status_lbl.setText("Waiting for WhatsApp QR code from Baileys...")

    def closeEvent(self, event) -> None:
        self._poll_timer.stop()
        super().closeEvent(event)


__all__ = ["WhatsAppQRDialog"]

