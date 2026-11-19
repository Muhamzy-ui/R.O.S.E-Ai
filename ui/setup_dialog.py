"""API, Backend, Voice, and SFX Settings Dialog for R.O.S.E PySide6 UI."""

from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app_config import load_app_config, save_app_config
from ui.theme import PALETTE, VOICES, build_shared_qss


class SetupSettingsDialog(QDialog):
    """Modal glass dialog for runtime backend, API keys, voice, and SFX settings."""

    def __init__(
        self,
        require_key: bool = False,
        current_voice: str = "Aoede",
        effects_enabled: bool = True,
        effects_volume: float = 0.25,
        on_voice_changed: Optional[Callable[[str], Any]] = None,
        on_sfx_toggled: Optional[Callable[[bool], Any]] = None,
        on_sfx_volume_changed: Optional[Callable[[float], Any]] = None,
        on_open_whatsapp_qr: Optional[Callable[[], Any]] = None,
        on_saved: Optional[Callable[[dict], Any]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._require_key = bool(require_key)
        self._on_voice_changed = on_voice_changed
        self._on_sfx_toggled = on_sfx_toggled
        self._on_sfx_volume_changed = on_sfx_volume_changed
        self._on_open_whatsapp_qr = on_open_whatsapp_qr
        self._on_saved = on_saved

        self.setWindowTitle("R.O.S.E System & API Settings")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setStyleSheet(build_shared_qss())

        cfg = load_app_config()
        self._build_ui(cfg, current_voice, effects_enabled, effects_volume)

    def _build_ui(
        self,
        cfg: dict,
        current_voice: str,
        effects_enabled: bool,
        effects_volume: float,
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(14)

        title = QLabel("R.O.S.E Configuration", self)
        title.setStyleSheet(
            f"font-size: 18px; font-weight: 600; color: {PALETTE.text_primary};"
        )
        subtitle = QLabel(
            "Configure Gemini Cloud vs local LM Studio runtime, API keys, voice persona, and HUD effects.",
            self,
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("SecondaryText")
        root.addWidget(title)
        root.addWidget(subtitle)

        # Runtime mode
        mode_row = QHBoxLayout()
        mode_lbl = QLabel("Runtime Mode:", self)
        mode_lbl.setStyleSheet(f"font-weight: 600; color: {PALETTE.accent_cyan};")
        self.rb_gemini = QRadioButton("Gemini Cloud (Live Voice)", self)
        self.rb_lmstudio = QRadioButton("LM Studio (Local Private)", self)
        backend_val = str(cfg.get("backend", "gemini") or "gemini").strip().lower()
        if backend_val == "lmstudio":
            self.rb_lmstudio.setChecked(True)
        else:
            self.rb_gemini.setChecked(True)
        mode_row.addWidget(mode_lbl)
        mode_row.addWidget(self.rb_gemini)
        mode_row.addWidget(self.rb_lmstudio)
        mode_row.addStretch(1)
        root.addLayout(mode_row)

        form = QFormLayout()
        form.setSpacing(10)

        self.gemini_key_edit = QLineEdit(str(cfg.get("gemini_api_key", "") or ""), self)
        self.gemini_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key_edit.setPlaceholderText("AIza... (required for Gemini Cloud)")
        form.addRow("Gemini API Key:", self.gemini_key_edit)

        self.youtube_key_edit = QLineEdit(str(cfg.get("youtube_api_key", "") or ""), self)
        self.youtube_key_edit.setPlaceholderText("Optional YouTube Data API v3 key")
        form.addRow("YouTube API Key:", self.youtube_key_edit)

        self.youtube_handle_edit = QLineEdit(str(cfg.get("youtube_channel_handle", "") or ""), self)
        self.youtube_handle_edit.setPlaceholderText("@channel (optional)")
        form.addRow("YouTube Handle:", self.youtube_handle_edit)

        self.lm_url_edit = QLineEdit(
            str(cfg.get("lmstudio_base_url", "http://127.0.0.1:1234/v1") or ""),
            self,
        )
        form.addRow("LM Studio URL:", self.lm_url_edit)

        self.lm_model_edit = QLineEdit(
            str(cfg.get("lmstudio_model", "local-model") or ""),
            self,
        )
        form.addRow("LM Studio Model:", self.lm_model_edit)

        self.voice_combo = QComboBox(self)
        self.voice_combo.addItems(VOICES)
        saved_voice = str(cfg.get("voice") or current_voice or "Aoede")
        if saved_voice in VOICES:
            self.voice_combo.setCurrentText(saved_voice)
        self.voice_combo.currentTextChanged.connect(self._on_voice_pick)
        form.addRow("Assistant Voice:", self.voice_combo)

        root.addLayout(form)

        # SFX controls
        sfx_row = QHBoxLayout()
        self.sfx_check = QCheckBox("Enable Ambient & UI SFX", self)
        self.sfx_check.setChecked(bool(effects_enabled))
        self.sfx_check.toggled.connect(self._on_sfx_toggle)
        sfx_row.addWidget(self.sfx_check)

        self.vol_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(int(max(0, min(100, round(float(effects_volume) * 100)))))
        self.vol_slider.valueChanged.connect(self._on_vol_move)
        sfx_row.addWidget(self.vol_slider, 1)

        self.vol_lbl = QLabel(f"{self.vol_slider.value()}%", self)
        self.vol_lbl.setMinimumWidth(36)
        sfx_row.addWidget(self.vol_lbl)
        root.addLayout(sfx_row)

        # Action buttons
        btns = QHBoxLayout()
        if self._on_open_whatsapp_qr:
            wa_btn = QPushButton("Link WhatsApp (QR)", self)
            wa_btn.clicked.connect(self._on_open_whatsapp_qr)
            btns.addWidget(wa_btn)
        btns.addStretch(1)
        if not self._require_key:
            cancel_btn = QPushButton("Cancel", self)
            cancel_btn.clicked.connect(self.reject)
            btns.addWidget(cancel_btn)

        save_btn = QPushButton("Save & Apply", self)
        save_btn.setObjectName("AccentButton")
        save_btn.clicked.connect(self._handle_save)
        btns.addWidget(save_btn)
        root.addLayout(btns)

    def _on_voice_pick(self, voice: str) -> None:
        if self._on_voice_changed:
            self._on_voice_changed(voice)

    def _on_sfx_toggle(self, checked: bool) -> None:
        if self._on_sfx_toggled:
            self._on_sfx_toggled(bool(checked))

    def _on_vol_move(self, val: int) -> None:
        self.vol_lbl.setText(f"{val}%")
        if self._on_sfx_volume_changed:
            self._on_sfx_volume_changed(float(val) / 100.0)

    def _handle_save(self) -> None:
        backend_val = "lmstudio" if self.rb_lmstudio.isChecked() else "gemini"
        key = self.gemini_key_edit.text().strip()
        if self._require_key and backend_val == "gemini" and not key:
            QMessageBox.warning(
                self,
                "API Key Required",
                "Enter your Gemini API key or switch Runtime Mode to LM Studio.",
            )
            return

        updates = {
            "backend": backend_val,
            "gemini_api_key": key,
            "youtube_api_key": self.youtube_key_edit.text().strip(),
            "youtube_channel_handle": self.youtube_handle_edit.text().strip(),
            "voice": self.voice_combo.currentText(),
            "sound_effects": bool(self.sfx_check.isChecked()),
            "sound_volume": round(float(self.vol_slider.value()) / 100.0, 2),
            "lmstudio_base_url": self.lm_url_edit.text().strip() or "http://127.0.0.1:1234/v1",
            "lmstudio_model": self.lm_model_edit.text().strip() or "local-model",
        }
        saved = save_app_config(updates)
        if self._on_saved:
            self._on_saved(saved)
        self.accept()


__all__ = ["SetupSettingsDialog"]

