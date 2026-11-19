"""
R.O.S.E (Project Mirror) — Exact Reference-Skinned MIRROR Command Console (PySide6).

Uses assets/ui/clean_plate.png (derived from 1672x941 reference_bg.png via offline script)
as the visual skin on a frameless 16:9 QWidget, drawing live dynamic values, additive sprite
glows, 64-pt audio waveform, rotating rings, <=38 particles, virtualized 500-msg chat, and
transparent hotspots using a single adaptive QTimer (60/24fps) and dirty-rectangle repaints only.
NEVER imports cv2 and NEVER uses QGraphicsEffect.
"""

from __future__ import annotations

import collections
import ctypes
from ctypes import wintypes
import datetime as dt
import math
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable, Optional

import psutil
from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QKeySequence,
    QPainter,
    QPixmap,
    QRegion,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QDialog,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QPushButton,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app_config import get_backend, has_gemini_api_key, load_app_config
from ui.command_palette import CommandPaletteDialog, PaletteCommand
from ui.conversation_view import ConversationMessage, ConversationModel, MAX_MESSAGES, _BatchBridge
from ui.debug_overlay import DebugOverlayWidget
from ui.info_column import _format_net_rate, trim_memory_safely
from ui.mini_orb import MiniOrbWindow
from ui.setup_dialog import SetupSettingsDialog
from ui.skin_renderer import (
    REF_HEIGHT,
    REF_WIDTH,
    SkinRegionMap,
    SpriteCache,
    make_particle_pool,
    paint_camera_section,
    paint_center_animations,
    paint_conversation_header,
    paint_debug_regions,
    paint_header_section,
    paint_health_section,
    paint_system_status_section,
    paint_time_section,
    paint_weather_section,
    step_particles,
)
from ui.sound import SoundManager
from ui.theme import PALETTE, build_shared_qss, load_rose_fonts
from ui.tk_bridge import shutdown_tk_host

ROOT_DIR = Path(__file__).resolve().parent.parent
CLEAN_PLATE_PATH = ROOT_DIR / "assets" / "ui" / "clean_plate.png"
REF_PLATE_PATH = ROOT_DIR / "assets" / "ui" / "reference_bg.png"

WM_SIZING = 0x0214
WMSZ_LEFT = 1
WMSZ_RIGHT = 2
WMSZ_TOP = 3
WMSZ_TOPLEFT = 4
WMSZ_TOPRIGHT = 5
WMSZ_BOTTOM = 6
WMSZ_BOTTOMLEFT = 7
WMSZ_BOTTOMRIGHT = 8


class _QtBridge(QObject):
    """Thread-safe signal bridge allowing asyncio/audio/worker threads to update Qt cleanly."""

    log_appended = Signal(str)
    state_changed = Signal(str)
    audio_level_updated = Signal(float)
    mic_level_updated = Signal(float)
    call_active_changed = Signal(bool)
    focus_section_requested = Signal(str, int)
    location_refreshed = Signal()
    camera_preview_updated = Signal(str, object)
    interview_mode_changed = Signal(bool)
    sys_mic_muted_changed = Signal(bool)
    sfx_requested = Signal(str)
    window_command = Signal(str)
    health_hologram_requested = Signal(str, str)
    job_center_requested = Signal(str)
    kill_banner_changed = Signal(bool, str)
    after_invoke = Signal(object, tuple)
    fast_vitals_ready = Signal(object)
    slow_vitals_ready = Signal(object)
    weather_ready = Signal(object)
    memory_freed = Signal(str)
    whatsapp_qr_requested = Signal()


class _QtRootCompat:
    """Drop-in compatibility object satisfying main.py's `ui.root` contract."""

    def __init__(self, app: QApplication, window: "RoseMainWindow", bridge: _QtBridge):
        self._app = app
        self._window = window
        self._bridge = bridge

    def mainloop(self) -> int:
        self._window.show()
        self._window.raise_()
        return self._app.exec()

    def after(self, ms: int, func: Callable[..., Any], *args: Any) -> None:
        if not callable(func):
            return
        delay = max(0, int(ms))
        if delay == 0:
            self._bridge.after_invoke.emit(func, args)
            return

        def _sched() -> None:
            QTimer.singleShot(delay, lambda: func(*args))

        if threading.current_thread() is threading.main_thread():
            _sched()
        else:
            self._bridge.after_invoke.emit(_sched, ())

    def update_idletasks(self) -> None:
        self._app.processEvents()

    def winfo_screenwidth(self) -> int:
        scr = self._app.primaryScreen()
        return scr.size().width() if scr else 1920

    def winfo_screenheight(self) -> int:
        scr = self._app.primaryScreen()
        return scr.size().height() if scr else 1080

    def ensure_tk_root(self):
        return None

    def destroy(self) -> None:
        self._window.close()

    def quit(self) -> None:
        self._app.quit()


class _SkinHotspotButton(QAbstractButton):
    """Transparent button placed over skin artwork using cached additive glow sprites only."""

    right_clicked = Signal(QPoint)

    def __init__(
        self,
        label: str,
        sprites: SpriteCache,
        *,
        accent_hex: str = "#26E6FF",
        hover_hex: Optional[str] = None,
        draw_glow: bool = True,
        subtle_hover: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setText(label)
        self._sprites = sprites
        self._accent_hex = accent_hex
        self._hover_hex = hover_hex or accent_hex
        self._draw_glow = draw_glow
        self._subtle_hover = subtle_hover
        self._active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_visual_state(
        self,
        *,
        accent: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> None:
        changed = False
        if accent is not None and accent != self._accent_hex:
            self._accent_hex = accent
            changed = True
        if active is not None and bool(active) != self._active:
            self._active = bool(active)
            changed = True
        if changed:
            self.update()

    def contextMenuEvent(self, event) -> None:
        self.right_clicked.emit(event.globalPos())
        event.accept()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:
        if not self._draw_glow:
            if self.underMouse() or self.isDown():
                p = QPainter(self)
                try:
                    p.setPen(Qt.PenStyle.NoPen)
                    col = QColor(self._hover_hex)
                    col.setAlpha(65 if self.isDown() else 34)
                    p.setBrush(col)
                    p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 5.0, 5.0)
                finally:
                    if p.isActive():
                        p.end()
            return

        hovered = self.underMouse()
        down = self.isDown()
        if not (self._active or hovered or down):
            return

        p = QPainter(self)
        try:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            color = self._accent_hex if (self._active or down) else self._hover_hex
            sprite = self._sprites.button_glow(color, self.width(), self.height())
            if down:
                p.setOpacity(0.95)
            elif self._active and hovered:
                p.setOpacity(0.84)
            elif self._active:
                p.setOpacity(0.32 if self._subtle_hover else 0.68)
            elif self._subtle_hover:
                p.setOpacity(0.36)
            else:
                p.setOpacity(0.52)
            p.drawPixmap(0, 0, sprite)
        finally:
            if p.isActive():
                p.end()


class _SkinTranscriptDelegate(QStyledItemDelegate):
    """Lightweight transcript delegate: SYS yellow, YOU white, R.O.S.E cyan, ERR coral."""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._scale = 1.0
        self._update_fonts()

    def set_scale(self, scale: float) -> None:
        s = max(0.55, min(1.8, float(scale)))
        if abs(s - self._scale) > 0.02:
            self._scale = s
            self._update_fonts()

    def _update_fonts(self) -> None:
        px = max(10, int(round(13.2 * self._scale)))
        self._font_line = QFont("Segoe UI", 9)
        self._font_line.setPixelSize(px)
        self._font_line.setWeight(QFont.Weight.DemiBold)

    def _row_text(self, msg: ConversationMessage) -> str:
        prefix = "SYS" if msg.role == "sys" else ("You" if msg.role == "you" else ("ROSE" if msg.role == "rose" else "ERR"))
        return f"{prefix}: {msg.body}"

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        msg = index.data(ConversationModel.RoleMessage)
        if not isinstance(msg, ConversationMessage):
            return QSize(200, 18)
        vw = option.rect.width() if option.rect.width() > 60 else 280
        usable_w = max(120, vw - 10)
        fm = QFontMetrics(self._font_line)
        br = fm.boundingRect(0, 0, usable_w, 1200, Qt.TextFlag.TextWordWrap, self._row_text(msg))
        return QSize(usable_w, max(16, br.height() + 5))

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        msg = index.data(ConversationModel.RoleMessage)
        if not isinstance(msg, ConversationMessage):
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = option.rect.adjusted(4, 2, -4, -1)
        if msg.role == "sys":
            painter.setFont(self._font_line)
            painter.setPen(QColor("#F6D05C"))
            painter.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, f"SYS: {msg.body}")
        elif msg.role == "you":
            painter.setFont(self._font_line)
            painter.setPen(QColor("#F8FAFC"))
            painter.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, f"You: {msg.body}")
        elif msg.role == "rose":
            painter.setFont(self._font_line)
            painter.setPen(QColor("#3CEEFF"))
            painter.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, f"ROSE: {msg.body}")
        else:
            painter.setFont(self._font_line)
            painter.setPen(QColor("#FB7185"))
            painter.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, f"ERR: {msg.body}")
        painter.restore()


class SkinConversationAdapter(QObject):
    """Virtualized 500-message transparent stream + input binding matching ConversationPanel contract."""

    def __init__(self, host: QWidget, sprites: SpriteCache, on_submit: Callable[[str], Any]):
        super().__init__(host)
        self.on_command_submit = on_submit
        self._lock = threading.Lock()
        self._pending_raw: list[str] = []
        self._auto_scroll = True

        self.model = ConversationModel(max_messages=MAX_MESSAGES, parent=self)
        self.delegate = _SkinTranscriptDelegate(self)

        self._bridge = _BatchBridge()
        self._bridge.flush_requested.connect(self._schedule_batch_on_ui)

        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.setInterval(50)
        self._batch_timer.timeout.connect(self._flush_pending_batch)

        self.view = QListView(host)
        self.view.setModel(self.model)
        self.view.setItemDelegate(self.delegate)
        self.view.setUniformItemSizes(False)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.view.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.view.setStyleSheet(
            "QListView { background: transparent; border: none; outline: none; }"
            "QScrollBar:vertical { background: rgba(4, 16, 36, 90); width: 6px; margin: 2px; border-radius: 3px; }"
            "QScrollBar::handle:vertical { background: rgba(38, 230, 255, 140); min-height: 24px; border-radius: 3px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
        )
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._open_context_menu)
        self.view.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)

        self.command_input = QLineEdit(host)
        self.command_input.setPlaceholderText("Type a command...")
        self.command_input.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.command_input.setStyleSheet(
            "QLineEdit {"
            "  background: transparent; border: none; outline: none;"
            "  color: #EAF7FF; selection-background-color: rgba(38, 230, 255, 110);"
            "}"
        )
        self.command_input.returnPressed.connect(self._emit_command)

        self.play_send_btn = _SkinHotspotButton("", sprites, accent_hex="#26E6FF", subtle_hover=True, parent=host)
        self.play_send_btn.setToolTip("Submit command")
        self.play_send_btn.clicked.connect(self._emit_command)

        self.send_btn = _SkinHotspotButton("SEND >", sprites, accent_hex="#26E6FF", subtle_hover=True, parent=host)
        self.send_btn.setToolTip("Submit command")
        self.send_btn.clicked.connect(self._emit_command)

    def layout_in_canvas(self, reg: SkinRegionMap, canvas: QRect) -> None:
        s = canvas.height() / float(REF_HEIGHT)
        self.delegate.set_scale(s)
        msg_r = reg.rect("conversation", "messages", canvas)
        self.view.setGeometry(msg_r)
        self.view.doItemsLayout()

        in_r = reg.rect("conversation", "input_box", canvas)
        self.command_input.setGeometry(in_r)
        f = QFont("Segoe UI", 9)
        f.setPixelSize(max(10, int(round(13.0 * s))))
        self.command_input.setFont(f)

        self.play_send_btn.setGeometry(reg.rect("conversation", "send_icon_btn", canvas))
        self.send_btn.setGeometry(reg.rect("conversation", "send_label_btn", canvas))

    def _emit_command(self) -> None:
        text = self.command_input.text().strip()
        if not text:
            return
        self.command_input.clear()
        if self.on_command_submit:
            self.on_command_submit(text)

    def _on_scroll_changed(self, value: int) -> None:
        sb = self.view.verticalScrollBar()
        self._auto_scroll = value >= max(0, sb.maximum() - 18)

    def _schedule_batch_on_ui(self) -> None:
        if not self._batch_timer.isActive():
            self._batch_timer.start()

    def append_message(self, text: str) -> None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return
        with self._lock:
            self._pending_raw.append(cleaned)
        self._bridge.flush_requested.emit()

    def append_immediate(self, text: str) -> None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return
        self.model.append_batch([cleaned])
        if self._auto_scroll:
            self.view.scrollToBottom()

    def flush_now(self) -> None:
        self._flush_pending_batch()

    def _flush_pending_batch(self) -> None:
        with self._lock:
            if not self._pending_raw:
                return
            batch = self._pending_raw[:]
            self._pending_raw.clear()
        added = self.model.append_batch(batch)
        if added > 0 and self._auto_scroll:
            self.view.scrollToBottom()

    def clear(self) -> None:
        with self._lock:
            self._pending_raw.clear()
        self.model.clear()

    def copy_all(self) -> None:
        lines = [f"{m.prefix}: {m.body}" for m in self.model.messages()]
        cb = QApplication.clipboard()
        if cb and lines:
            cb.setText("\n".join(lines))

    def _open_context_menu(self, pos) -> None:
        menu = QMenu(self.view)
        act_copy = menu.addAction("Copy All")
        act_clear = menu.addAction("Clear Conversation")
        chosen = menu.exec(self.view.viewport().mapToGlobal(pos))
        if chosen == act_copy:
            self.copy_all()
        elif chosen == act_clear:
            self.clear()

    def set_state_badge(self, _state: str, _level: float = 0.15, _dt_sec: float = 0.03) -> None:
        pass

    def message_count(self) -> int:
        return self.model.rowCount()

    def pending_queue_size(self) -> int:
        with self._lock:
            return len(self._pending_raw)


class RoseMainWindow(QWidget):
    """Frameless 16:9 MIRROR window skin driven by clean_plate.png & ui_regions.json."""

    def __init__(self, facade: "JarvisUI", bridge: _QtBridge):
        super().__init__(None)
        self._facade = facade
        self._bridge = bridge
        self._shutting_down = False
        self._call_active = False
        self._call_started_at = 0.0
        self._interview_active = False
        self._paused = False
        self._muted = False
        self._sys_mic_muted = False
        self._current_state = "LISTENING"
        self._voice_label = "Windows"
        self._health_dialogs: list[QDialog] = []
        self._job_center_win = None
        self._wa_qr_dialog = None
        self._sound = SoundManager()

        # Skin assets & caches
        self._regions = SkinRegionMap()
        self._sprites = SpriteCache()
        plate_src = CLEAN_PLATE_PATH if CLEAN_PLATE_PATH.exists() else REF_PLATE_PATH
        self._plate_raw = QPixmap(str(plate_src))
        self._scaled_plate: Optional[QPixmap] = None
        self._scaled_canvas_size = QSize()
        self._canvas_rect = QRect(0, 0, REF_WIDTH, REF_HEIGHT)
        self._in_aspect_adjust = False
        self._drag_offset: Optional[QPoint] = None
        self._show_debug_regions = False

        # Live telemetry state (written on UI thread only from worker signals)
        self._city_name = "ABUJA"
        self._now_dt = dt.datetime.now()
        self._weather_city = "ABUJA"
        self._weather_temp = "--°C"
        self._weather_lines = "• syncing weather"
        self._weather_icon = "partly_cloudy"
        self._uptime_str = "00:00:00"
        self._cpu_pct = 0.0
        self._ram_pct = 0.0
        self._ram_override_pct: Optional[float] = None
        self._disk_pct = 0.0
        self._bat_pct = 100.0
        self._bat_label = "AC"
        self._net_up_str = "0.0 KB/s"
        self._net_down_str = "0.0 KB/s"
        self._last_net_counters = None
        self._last_net_ts = 0.0
        self._health_lines = ["- Initializing health scan..."]
        self._cam_status = "IDLE"
        self._cam_headline = 'Hold item & ask "What is this?"'
        self._cam_subtext = "Sharp still captures on demand"
        self._cam_pixmap: Optional[QPixmap] = None
        self._cam_update_count = 0

        # Dirty comparison state so unchanged static labels NEVER repaint
        self._last_clock_hhmm = ""
        self._last_clock_sec = -1
        self._last_date_str = ""
        self._last_sys_sig = None

        # Animation & waveform state
        self._phase = 0.0
        self._ring_angle = 0.0
        self._think_angle = 0.0
        self._audio_level = 0.0
        self._mic_level = 0.0
        self._error_flash = 0.0
        self._particles = make_particle_pool(38)
        self._wave_points = [0.08] * 64

        # Telemetry & frame budget tracking
        self._paint_ms_hist: collections.deque[float] = collections.deque([2.5] * 24, maxlen=120)
        self._frame_dt_hist: collections.deque[float] = collections.deque(maxlen=60)
        self._last_anim_ts = time.perf_counter()
        self._last_1hz_mono = 0.0
        self._last_slow_mono = 0.0
        self._last_weather_mono = 0.0
        self._fast_inflight = False
        self._slow_inflight = False
        self._weather_inflight = False
        self._global_hotkey_prev: dict[str, bool] = {}

        # Window framing
        self.setWindowTitle("R.O.S.E")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)
        self.setMinimumSize(960, 540)

        init_w, init_h = self._pick_initial_16x9_size()
        self.resize(init_w, init_h)

        self._build_overlay_controls()
        self._wire_signals()
        self._setup_hotkeys()
        self._sync_sound_from_config()
        self._rebuild_plate_cache()
        self._layout_overlay_controls()

        # Single master UI clock (60fps active / 24fps idle / stopped when minimized or hidden)
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(16)
        self._ui_timer.timeout.connect(self._on_ui_tick)
        self._anim_timer = self._ui_timer

        try:
            self._psutil_proc: Optional[psutil.Process] = psutil.Process()
            self._psutil_proc.cpu_percent(interval=None)
        except Exception:
            self._psutil_proc = None
        self._cached_ui_cpu_pct = 0.0
        self._last_ui_cpu_ts = time.perf_counter()

        try:
            from core.permissions import security_manager

            security_manager.register_kill_callback(self._on_kill_switch_activated)
            if security_manager.is_killed:
                self._apply_kill_banner_ui(True, "Kill Switch Active")
        except Exception:
            pass

        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            QTimer.singleShot(280, self._start_initial_audio_bed)

        # Prime location & background workers without blocking UI creation
        self._refresh_city_cache()
        self._spawn_fast_vitals_worker()
        self._spawn_slow_vitals_worker()
        self._spawn_weather_worker()

    def _pick_initial_16x9_size(self) -> tuple[int, int]:
        scr = QApplication.primaryScreen()
        if not scr:
            return (1408, 792)
        ag = scr.availableGeometry()
        if ag.width() >= 1700 and ag.height() >= 965:
            return (REF_WIDTH, REF_HEIGHT)
        max_w = int(ag.width() * 0.90)
        max_h = int(ag.height() * 0.90)
        w = min(max_w, int(round(max_h * (REF_WIDTH / float(REF_HEIGHT)))))
        w = max(960, w)
        h = max(540, int(round(w * (REF_HEIGHT / float(REF_WIDTH)))))
        return (w, h)

    def _build_overlay_controls(self) -> None:
        # Window chrome buttons (transparent over baked -, [], X icons)
        self.min_btn = _SkinHotspotButton("", self._sprites, draw_glow=False, parent=self)
        self.min_btn.setToolTip("Minimize")
        self.min_btn.clicked.connect(self.showMinimized)

        self.max_btn = _SkinHotspotButton("", self._sprites, draw_glow=False, parent=self)
        self.max_btn.setToolTip("Maximize / Restore (F11)")
        self.max_btn.clicked.connect(self.toggle_fullscreen)

        self.close_btn = _SkinHotspotButton("", self._sprites, hover_hex="#F43F5E", draw_glow=False, parent=self)
        self.close_btn.setToolTip("Shutdown R.O.S.E")
        self.close_btn.clicked.connect(self._facade.shutdown)

        # Settings card hotspot
        self.settings_btn = _SkinHotspotButton("", self._sprites, accent_hex="#26E6FF", subtle_hover=True, parent=self)
        self.settings_btn.setToolTip("Open System Settings, Voice Core & Sound FX (Right-click for quick tools)")
        self.settings_btn.clicked.connect(self.open_settings_dialog)
        self.settings_btn.right_clicked.connect(self._open_settings_context_menu)

        # Emergency OS mic unmute badge & Kill switch banner & RAM>90 offer
        self.unmute_mic_btn = QPushButton("UNMUTE MIC", self)
        self.unmute_mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unmute_mic_btn.setStyleSheet(
            "QPushButton { background-color: rgba(244, 63, 94, 210); color: #FFFFFF;"
            "  border: 1px solid #FDA4AF; border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 8px; }"
            "QPushButton:hover { background-color: rgba(251, 113, 133, 240); }"
        )
        self.unmute_mic_btn.clicked.connect(self._handle_unmute_sys_mic)
        self.unmute_mic_btn.setVisible(False)

        self.free_memory_btn = QPushButton("Free memory", self)
        self.free_memory_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.free_memory_btn.setStyleSheet(
            "QPushButton { background-color: rgba(15, 23, 42, 205); color: #FCA5A5;"
            "  border: 1px solid rgba(248, 113, 113, 200); border-radius: 4px; font-size: 9px; font-weight: 700; padding: 1px 6px; }"
            "QPushButton:hover { background-color: rgba(220, 38, 38, 210); color: #FFFFFF; border-color: #FECACA; }"
        )
        self.free_memory_btn.clicked.connect(self.trigger_free_memory)
        self.free_memory_btn.setVisible(False)

        self.kill_banner_btn = QPushButton("KILL SWITCH ENGAGED — Click to reset", self)
        self.kill_banner_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.kill_banner_btn.setStyleSheet(
            "QPushButton { background-color: rgba(180, 20, 48, 235); color: #FFF1F2;"
            "  border: 1px solid #FB7185; border-radius: 4px; font-size: 10px; font-weight: 700; padding: 3px 10px; }"
        )
        self.kill_banner_btn.clicked.connect(self._unlock_kill_switch_local)
        self.kill_banner_btn.setVisible(False)

        # Camera Vision clickable hotspots (IDENTIFY & LIVE)
        self.cam_identify_btn = _SkinHotspotButton("IDENTIFY", self._sprites, accent_hex="#26E6FF", parent=self)
        self.cam_identify_btn.setToolTip("Capture high-res still and identify item")
        self.cam_identify_btn.clicked.connect(lambda: self._launch_camera_action("identify"))

        self.cam_live_btn = _SkinHotspotButton("LIVE", self._sprites, accent_hex="#34D399", parent=self)
        self.cam_live_btn.setToolTip("Open live camera window")
        self.cam_live_btn.clicked.connect(lambda: self._launch_camera_action("live"))

        # Bottom 6 dock transparent hotspots (preserving exact .text() contract)
        self.live_btn = _SkinHotspotButton("LIVE", self._sprites, accent_hex="#26E6FF", subtle_hover=True, parent=self)
        self.live_btn.set_visual_state(active=True)
        self.live_btn.setToolTip("Toggle Live Microphone (F4 / Ctrl+M)")
        self.live_btn.clicked.connect(self.toggle_mute)
        self.mic_btn = self.live_btn

        self.pause_btn = _SkinHotspotButton("PAUSE", self._sprites, accent_hex="#F59E0B", hover_hex="#26E6FF", parent=self)
        self.pause_btn.setToolTip("Pause / Resume Voice Assistant (F5)")
        self.pause_btn.clicked.connect(self.toggle_pause)

        self.mini_orb_btn = _SkinHotspotButton("MINI ORB", self._sprites, accent_hex="#26E6FF", parent=self)
        self.mini_orb_btn.setToolTip("Compact Floating Mini Orb (F9 / Alt+R)")
        self.mini_orb_btn.clicked.connect(self.switch_to_mini_orb)

        self.interview_btn = _SkinHotspotButton("INTERVIEW", self._sprites, accent_hex="#34D399", hover_hex="#26E6FF", parent=self)
        self.interview_btn.setToolTip("Toggle Silent Interview Co-Pilot (F8 / Ctrl+I)")
        self.interview_btn.clicked.connect(self.toggle_interview_mode)

        self.jobs_btn = _SkinHotspotButton("JOBS", self._sprites, accent_hex="#22E55A", parent=self)
        self.jobs_btn.setToolTip("Open Job Command Center (F10 / Ctrl+J)")
        self.jobs_btn.clicked.connect(lambda: self.open_job_center("radar"))

        self.shutdown_btn = _SkinHotspotButton("SHUTDOWN", self._sprites, accent_hex="#EF4444", parent=self)
        self.shutdown_btn.setToolTip("Shutdown R.O.S.E")
        self.shutdown_btn.clicked.connect(self._facade.shutdown)

        self._bottom_pills = [
            self.live_btn,
            self.pause_btn,
            self.mini_orb_btn,
            self.interview_btn,
            self.jobs_btn,
            self.shutdown_btn,
        ]

        # Right conversation transparent view + command input
        self.conversation_panel = SkinConversationAdapter(self, self._sprites, self._submit_text_command_text)
        self.command_input = self.conversation_panel.command_input
        self.send_btn = self.conversation_panel.send_btn

        # Overlays / Secondary windows
        self.debug_overlay = DebugOverlayWidget(stats_provider=self.collect_perf_stats, parent=self)
        self.mini_orb = MiniOrbWindow(
            on_restore=self.restore_from_mini_orb,
            on_toggle_mute=self.toggle_mute,
            on_toggle_call=self.toggle_call,
        )
        self.command_palette = CommandPaletteDialog(self._build_palette_commands(), parent=self)

    def _wire_signals(self) -> None:
        b = self._bridge
        b.log_appended.connect(self.conversation_panel.append_message)
        b.state_changed.connect(self._apply_state_ui)
        b.audio_level_updated.connect(self._apply_audio_level_ui)
        b.mic_level_updated.connect(self._apply_mic_level_ui)
        b.call_active_changed.connect(self._apply_call_ui)
        b.focus_section_requested.connect(self._apply_focus_section)
        b.location_refreshed.connect(self._apply_location_refresh)
        b.camera_preview_updated.connect(self.update_camera_preview)
        b.interview_mode_changed.connect(self._apply_interview_state_ui)
        b.sys_mic_muted_changed.connect(self._apply_sys_mic_muted_ui)
        b.sfx_requested.connect(self._play_sfx_ui)
        b.window_command.connect(self._apply_window_command)
        b.health_hologram_requested.connect(self._show_health_dialog_ui)
        b.job_center_requested.connect(self.open_job_center)
        b.kill_banner_changed.connect(self._apply_kill_banner_ui)
        b.after_invoke.connect(self._invoke_on_main)
        b.fast_vitals_ready.connect(self._apply_fast_vitals)
        b.slow_vitals_ready.connect(self._apply_slow_vitals)
        b.weather_ready.connect(self._apply_weather_details)
        b.memory_freed.connect(self._on_memory_freed_done)
        b.whatsapp_qr_requested.connect(self.open_whatsapp_qr_dialog)

    @staticmethod
    def _invoke_on_main(fn: object, args: tuple) -> None:
        try:
            if callable(fn):
                fn(*args)
        except Exception:
            pass

    def _setup_hotkeys(self) -> None:
        self._shortcuts: list[QShortcut] = []

        def _bind(seq: str, fn: Callable[[], Any]) -> None:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(fn)
            self._shortcuts.append(sc)

        _bind("F4", self.toggle_mute)
        _bind("Ctrl+M", self.toggle_mute)
        _bind("F5", self.toggle_pause)
        _bind("F8", self.toggle_interview_mode)
        _bind("Ctrl+I", self.toggle_interview_mode)
        _bind("F9", self.toggle_mini_orb)
        _bind("Alt+R", self.toggle_mini_orb)
        _bind("F10", lambda: self.open_job_center("radar"))
        _bind("Ctrl+J", lambda: self.open_job_center("radar"))
        _bind("F11", self.toggle_fullscreen)
        _bind("Ctrl+K", self.open_command_palette)
        _bind("Ctrl+Shift+D", self.toggle_debug_overlay)
        _bind("Ctrl+Shift+R", self.toggle_debug_regions)
        _bind("Ctrl+Alt+Shift+K", self.trigger_kill_switch_hotkey)
        _bind("Escape", self._handle_escape_key)

    def _poll_win32_hotkeys(self) -> None:
        if os.name != "nt" or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        try:
            u32 = ctypes.windll.user32
            ctrl_down = bool(u32.GetAsyncKeyState(0x11) & 0x8000)
            alt_down = bool(u32.GetAsyncKeyState(0x12) & 0x8000)
            shift_down = bool(u32.GetAsyncKeyState(0x10) & 0x8000)
            combos = {
                "kill": ctrl_down and alt_down and shift_down and bool(u32.GetAsyncKeyState(0x4B) & 0x8000),
                "f8": bool(u32.GetAsyncKeyState(0x77) & 0x8000) and not (ctrl_down or alt_down),
                "f9": bool(u32.GetAsyncKeyState(0x78) & 0x8000) and not (ctrl_down or alt_down),
                "alt_r": alt_down and (not ctrl_down) and bool(u32.GetAsyncKeyState(0x52) & 0x8000),
                "f10": bool(u32.GetAsyncKeyState(0x79) & 0x8000) and not (ctrl_down or alt_down),
            }
            for name, pressed in combos.items():
                was = self._global_hotkey_prev.get(name, False)
                self._global_hotkey_prev[name] = pressed
                if pressed and not was:
                    if name == "kill":
                        self.trigger_kill_switch_hotkey()
                    elif name == "f8" and not self.isActiveWindow():
                        self.toggle_interview_mode()
                    elif name in ("f9", "alt_r") and not self.isActiveWindow():
                        self.toggle_mini_orb()
                    elif name == "f10" and not self.isActiveWindow():
                        self.open_job_center("radar")
        except Exception:
            pass

    def _build_palette_commands(self) -> list[PaletteCommand]:
        return [
            PaletteCommand("mute", "Toggle Microphone Mute", "Voice", "F4 / Ctrl+M", "mic live", self.toggle_mute),
            PaletteCommand("pause", "Pause / Resume Assistant", "Voice", "F5", "pause", self.toggle_pause),
            PaletteCommand("call", "Start / End Live Call Mode", "Voice", "Call", "call", self.toggle_call),
            PaletteCommand("interview", "Toggle Silent Interview Co-Pilot", "Mode", "F8 / Ctrl+I", "interview", self.toggle_interview_mode),
            PaletteCommand("mini", "Switch to Floating Mini Orb", "Window", "F9 / Alt+R", "mini", self.switch_to_mini_orb),
            PaletteCommand("jobs", "Open Job Command Center", "Jobs", "F10 / Ctrl+J", "radar", lambda: self.open_job_center("radar")),
            PaletteCommand("cam_id", "Camera Vision: Identify Object", "Camera", "", "camera", lambda: self._launch_camera_action("identify")),
            PaletteCommand("cam_live", "Camera Vision: Open Live View", "Camera", "", "webcam", lambda: self._launch_camera_action("live")),
            PaletteCommand("free_mem", "Trim Memory Working Set", "System", "", "ram", self.trigger_free_memory),
            PaletteCommand("whatsapp_qr", "Link / Check WhatsApp (QR Code)", "Comms", "", "whatsapp qr", self.open_whatsapp_qr_dialog),
            PaletteCommand("settings", "System Settings & Voice Core", "Config", "", "voice api lm", self.open_settings_dialog),
            PaletteCommand("debug", "Toggle Perf Debug Overlay", "Debug", "Ctrl+Shift+D", "fps", self.toggle_debug_overlay),
            PaletteCommand("regions", "Toggle UI Regions Debug Outlines", "Debug", "Ctrl+Shift+R", "regions", self.toggle_debug_regions),
            PaletteCommand("full", "Toggle Fullscreen", "Window", "F11", "max", self.toggle_fullscreen),
            PaletteCommand("kill", "Autonomous Safety Kill Switch", "Safety", "Ctrl+Alt+Shift+K", "stop", self.trigger_kill_switch_hotkey),
            PaletteCommand("clear", "Clear Conversation History", "Chat", "", "clean", self.conversation_panel.clear),
            PaletteCommand("quit", "Shutdown R.O.S.E", "System", "", "exit", self._facade.shutdown),
        ]

    # ── Aspect Ratio, Plate Caching & Dirty-Rect Layout ───────────────────────

    def _compute_canvas_rect(self) -> QRect:
        w, h = self.width(), self.height()
        target_w = int(round(h * (REF_WIDTH / float(REF_HEIGHT))))
        if target_w <= w:
            x = (w - target_w) // 2
            return QRect(x, 0, max(1, target_w), max(1, h))
        target_h = int(round(w * (REF_HEIGHT / float(REF_WIDTH))))
        y = (h - target_h) // 2
        return QRect(0, y, max(1, w), max(1, target_h))

    def _rebuild_plate_cache(self) -> None:
        self._canvas_rect = self._compute_canvas_rect()
        sz = self._canvas_rect.size()
        if sz.width() <= 0 or sz.height() <= 0:
            return
        if self._scaled_plate is not None and self._scaled_canvas_size == sz:
            return
        if not self._plate_raw.isNull():
            self._scaled_plate = self._plate_raw.scaled(
                sz,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._scaled_canvas_size = sz

    def _layout_overlay_controls(self) -> None:
        c = self._canvas_rect
        r = self._regions
        s = c.height() / float(REF_HEIGHT)

        self.min_btn.setGeometry(r.rect("window", "minimize_btn", c))
        self.max_btn.setGeometry(r.rect("window", "maximize_btn", c))
        self.close_btn.setGeometry(r.rect("window", "close_btn", c))
        self.settings_btn.setGeometry(r.rect("header", "settings_box", c))

        self.cam_identify_btn.setGeometry(r.rect("camera_vision", "identify_btn", c))
        self.cam_live_btn.setGeometry(r.rect("camera_vision", "live_btn", c))

        for name, btn in zip(
            ("LIVE", "PAUSE", "MINI ORB", "INTERVIEW", "JOBS", "SHUTDOWN"),
            self._bottom_pills,
        ):
            btn.setGeometry(r.rect("dock", "buttons", c, sub_key=name))

        self.conversation_panel.layout_in_canvas(r, c)

        # Optional contextual pills positioned cleanly inside header/status areas
        ind_r = r.rect("header", "online_indicator", c)
        self.unmute_mic_btn.setGeometry(
            int(ind_r.x() - 96 * s),
            int(ind_r.y() + 2 * s),
            int(86 * s),
            int(24 * s),
        )

        sys_r = r.rect("system_status", "card", c)
        self.free_memory_btn.setGeometry(
            int(sys_r.right() - 88 * s),
            int(sys_r.top() + 5 * s),
            int(78 * s),
            int(18 * s),
        )

        self.kill_banner_btn.setGeometry(
            int(c.x() + c.width() * 0.25),
            int(c.y() + 42 * s),
            int(c.width() * 0.50),
            int(26 * s),
        )

        if self.debug_overlay.isVisible():
            self.debug_overlay.move(max(12, c.right() - 310), max(54, int(c.y() + 92 * s)))

    def nativeEvent(self, eventType, message):
        if os.name == "nt" and eventType in (b"windows_generic_MSG", "windows_generic_MSG"):
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_SIZING and not (self.isMaximized() or self.isFullScreen()):
                    rc = ctypes.cast(msg.lParam, ctypes.POINTER(wintypes.RECT)).contents
                    w = max(960, int(rc.right - rc.left))
                    h = max(540, int(rc.bottom - rc.top))
                    ratio = REF_WIDTH / float(REF_HEIGHT)
                    if msg.wParam in (WMSZ_TOP, WMSZ_BOTTOM):
                        new_w = max(960, int(round(h * ratio)))
                        rc.right = rc.left + new_w
                    else:
                        new_h = max(540, int(round(w / ratio)))
                        if msg.wParam in (WMSZ_TOPLEFT, WMSZ_TOPRIGHT):
                            rc.top = rc.bottom - new_h
                        else:
                            rc.bottom = rc.top + new_h
                    return True, 1
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def resize(self, *args) -> None:
        if len(args) == 1 and hasattr(args[0], "width"):
            w, h = int(args[0].width()), int(args[0].height())
        elif len(args) == 2:
            w, h = int(args[0]), int(args[1])
        else:
            super().resize(*args)
            return
        if not (self.isMaximized() or self.isFullScreen()):
            w = max(960, w)
            h = max(540, int(round(w * (REF_HEIGHT / float(REF_WIDTH)))))
        super().resize(w, h)
        if hasattr(self, "_regions") and hasattr(self, "min_btn"):
            self._rebuild_plate_cache()
            self._layout_overlay_controls()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        if not (self.isMaximized() or self.isFullScreen()) and not self._in_aspect_adjust:
            expected_h = max(540, int(round(w * (REF_HEIGHT / float(REF_WIDTH)))))
            if abs(h - expected_h) > 2:
                self._in_aspect_adjust = True
                super().resize(w, expected_h)
                self._in_aspect_adjust = False
                return
        self._rebuild_plate_cache()
        self._layout_overlay_controls()
        self.update()

    # ── Frameless Drag & Resize Mouse Handling ────────────────────────────────

    def _drag_edges_at(self, pos: QPoint) -> Qt.Edge:
        if self.isMaximized() or self.isFullScreen():
            return Qt.Edge(0)
        m = 7
        edges = Qt.Edge(0)
        if pos.x() <= m:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - m:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= m:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - m:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            edges = self._drag_edges_at(pos)
            if edges and self.windowHandle():
                self.windowHandle().startSystemResize(edges)
                event.accept()
                return
            drag_rect = self._regions.rect("window", "title_bar_drag", self._canvas_rect)
            if drag_rect.contains(pos):
                if self.isMaximized():
                    self.showNormal()
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        edges = self._drag_edges_at(pos)
        if edges in (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif edges in (Qt.Edge.RightEdge | Qt.Edge.TopEdge, Qt.Edge.LeftEdge | Qt.Edge.BottomEdge):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._regions.rect("window", "title_bar_drag", self._canvas_rect).contains(event.position().toPoint()):
                self.toggle_fullscreen()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    # ── Dirty-Rect Painting Only ──────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        t0 = time.perf_counter()
        p = QPainter(self)
        try:
            reg_dirty: QRegion = event.region()
            dirty_box = event.rect()
            c = self._canvas_rect

            # 1. Paint clean plate strictly inside dirty region rects (never rescale per frame)
            if c != self.rect():
                for r in reg_dirty:
                    p.fillRect(r, QColor("#02060E"))

            if self._scaled_plate is not None and not self._scaled_plate.isNull():
                for r in reg_dirty:
                    inter = r.intersected(c)
                    if not inter.isEmpty():
                        src_r = inter.translated(-c.x(), -c.y())
                        p.drawPixmap(inter, self._scaled_plate, src_r)
            else:
                p.fillRect(dirty_box, QColor("#040B1A"))

            # Helper closure checks true multi-rect QRegion intersection so left panels never repaint on anim frames
            def _hits(section: str, key: str) -> bool:
                return reg_dirty.intersects(self._regions.rect(section, key, c, pad=2))

            if _hits("header", "settings_voice_text") or _hits("header", "online_indicator"):
                paint_header_section(
                    p,
                    self._regions,
                    c,
                    voice_label=self._voice_label,
                    state=self._current_state,
                    call_active=self._call_active,
                    sys_mic_muted=self._sys_mic_muted,
                )

            if _hits("time_panel", "card"):
                paint_time_section(p, self._regions, c, now_dt=self._now_dt, city_label=self._city_name)

            if _hits("weather_panel", "card"):
                paint_weather_section(
                    p,
                    self._regions,
                    c,
                    city=self._weather_city,
                    temp_text=self._weather_temp,
                    lines_text=self._weather_lines,
                    icon_kind=self._weather_icon,
                )

            if _hits("system_status", "card"):
                ram_show = self._ram_override_pct if self._ram_override_pct is not None else self._ram_pct
                paint_system_status_section(
                    p,
                    self._regions,
                    c,
                    uptime_text=self._uptime_str,
                    cpu_pct=self._cpu_pct,
                    ram_pct=ram_show,
                    disk_pct=self._disk_pct,
                    bat_pct=self._bat_pct,
                    bat_label=self._bat_label,
                    net_up_text=self._net_up_str,
                    net_down_text=self._net_down_str,
                )

            if _hits("health_summary", "all_lines"):
                paint_health_section(p, self._regions, c, lines=self._health_lines)

            if _hits("camera_vision", "card"):
                paint_camera_section(
                    p,
                    self._regions,
                    c,
                    status_text=self._cam_status,
                    headline=self._cam_headline,
                    subtext=self._cam_subtext,
                    thumb_pixmap=self._cam_pixmap,
                )

            if (
                _hits("center", "globe_box")
                or _hits("center", "waveform_band")
                or _hits("center", "pedestal_ring")
                or _hits("center", "pedestal_status")
            ):
                call_secs = int(max(0.0, t0 - self._call_started_at)) if (self._call_active and self._call_started_at > 0) else 0
                paint_center_animations(
                    p,
                    self._regions,
                    self._sprites,
                    c,
                    state=self._current_state,
                    call_active=self._call_active,
                    call_elapsed_sec=call_secs,
                    phase=self._phase,
                    ring_angle=self._ring_angle,
                    think_angle=self._think_angle,
                    audio_level=self._audio_level,
                    mic_level=self._mic_level,
                    error_flash=self._error_flash,
                    particles=self._particles,
                    wave_points=self._wave_points,
                )

            if _hits("conversation", "status") or _hits("conversation", "mini_wave"):
                paint_conversation_header(
                    p,
                    self._regions,
                    c,
                    state=self._current_state,
                    call_active=self._call_active,
                    phase=self._phase,
                    level=max(self._audio_level, self._mic_level),
                )

            if self._show_debug_regions:
                paint_debug_regions(p, self._regions, c)
        finally:
            if p.isActive():
                p.end()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self._paint_ms_hist.append(elapsed_ms)

    # ── Single Timer Clock (60fps active / 24fps idle / stopped when min) ─────

    def _target_fps(self) -> int:
        if self._call_active or self._current_state in ("LISTENING", "SPEAKING", "THINKING", "CALL"):
            return 60
        return 24

    def _sync_timer_rate(self) -> None:
        if self._shutting_down or self.isMinimized() or not self.isVisible():
            if self._ui_timer.isActive():
                self._ui_timer.stop()
            return
        desired = 16 if self._target_fps() >= 50 else 42
        if self._ui_timer.interval() != desired:
            self._ui_timer.setInterval(desired)
        if not self._ui_timer.isActive():
            self._last_anim_ts = time.perf_counter()
            self._ui_timer.start()

    def _step_waveform_and_motion(self, dt_sec: float) -> None:
        dt_c = min(0.08, max(0.001, dt_sec))
        eff = "CALL" if (self._call_active and self._current_state in ("IDLE", "LISTENING")) else self._current_state
        prof_speed = 22.0 if eff == "LISTENING" else (68.0 if eff == "THINKING" else (34.0 if eff == "SPEAKING" else (28.0 if eff == "CALL" else 10.0)))
        if eff in ("MUTED", "PAUSED"):
            prof_speed = 4.0

        self._phase += dt_c * (1.8 if eff == "IDLE" else 3.2)
        self._ring_angle = (self._ring_angle + dt_c * prof_speed) % 360.0
        self._think_angle = (self._think_angle + dt_c * 96.0) % 360.0
        if self._error_flash > 0.0:
            self._error_flash = max(0.0, self._error_flash - dt_c * 1.4)

        step_particles(self._particles, dt_c, 0.55 if eff == "IDLE" else 1.15)

        # Smooth 64-point real-audio reactive waveform
        lvl = max(self._audio_level, self._mic_level)
        if eff == "IDLE":
            energy = 0.14 + 0.05 * math.sin(self._phase * 1.6)
        elif eff in ("MUTED", "PAUSED"):
            energy = 0.06
        elif eff == "THINKING":
            energy = 0.34 + 0.16 * math.sin(self._phase * 3.8)
        elif eff == "SPEAKING":
            energy = max(0.28, min(1.0, 0.24 + lvl * 0.95))
        else:
            energy = max(0.18, min(0.95, 0.16 + lvl * 0.88))

        alpha = 0.42 if eff == "SPEAKING" else 0.28
        n = len(self._wave_points)
        for idx in range(n):
            u = idx / float(n - 1)
            carrier = (
                0.55
                + 0.30 * math.sin(self._phase * 4.4 + u * 24.0)
                + 0.15 * math.cos(self._phase * 7.1 - u * 42.0)
            )
            target = max(0.04, min(1.0, energy * carrier))
            self._wave_points[idx] += (target - self._wave_points[idx]) * alpha

    def _on_ui_tick(self) -> None:
        if self._shutting_down or self.isMinimized() or not self.isVisible():
            self._ui_timer.stop()
            return

        now = time.perf_counter()
        dt_sec = min(0.1, max(0.001, now - self._last_anim_ts))
        self._last_anim_ts = now
        self._frame_dt_hist.append(dt_sec)

        self._step_waveform_and_motion(dt_sec)
        self._sync_timer_rate()

        # Invalidate ONLY animated dirty rectangles (never self.update() whole window!)
        c = self._canvas_rect
        self.update(self._regions.rect("center", "globe_box", c, pad=1))
        self.update(self._regions.rect("center", "waveform_band", c, pad=1))
        self.update(self._regions.rect("center", "pedestal_ring", c, pad=1))
        if self._call_active or self._current_state in ("LISTENING", "SPEAKING", "THINKING"):
            self.update(self._regions.rect("conversation", "mini_wave", c, pad=1))

        # Background polled hotkeys & scheduled telemetry ticks
        now_m = time.monotonic()
        self._poll_win32_hotkeys()

        if (now_m - self._last_1hz_mono) >= 1.0:
            self._last_1hz_mono = now_m
            self._tick_clock_ui()
            self._spawn_fast_vitals_worker()
            if self._call_active and self._call_started_at > 0:
                self.update(self._regions.rect("center", "pedestal_status", c, pad=2))

        if (now_m - self._last_slow_mono) >= 15.0:
            self._last_slow_mono = now_m
            self._spawn_slow_vitals_worker()

        if (now_m - self._last_weather_mono) >= 600.0:
            self._last_weather_mono = now_m
            self._spawn_weather_worker()

    # ── Background Telemetry Workers (Zero OS/Net Work on UI Thread) ──────────

    def _refresh_city_cache(self) -> None:
        def _w() -> None:
            try:
                from actions.weather import get_configured_location

                loc = get_configured_location()
                city = str(loc.get("city") or "LOCAL").upper()
                self._city_name = city
            except Exception:
                pass

        threading.Thread(target=_w, daemon=True, name="rose-loc-init").start()

    def _tick_clock_ui(self) -> None:
        try:
            from actions.weather import get_location_datetime

            now_dt, _ = get_location_datetime(self._city_name)
        except Exception:
            now_dt = dt.datetime.now()
        self._now_dt = now_dt
        c = self._canvas_rect

        if now_dt.second != self._last_clock_sec:
            self._last_clock_sec = now_dt.second
            self.update(self._regions.rect("time_panel", "seconds_digits", c, pad=2))
            self.update(self._regions.rect("time_panel", "clock_dial", c, pad=2))

        hhmm = now_dt.strftime("%H:%M")
        if hhmm != self._last_clock_hhmm:
            self._last_clock_hhmm = hhmm
            self.update(self._regions.rect("time_panel", "clock_digits", c, pad=2))

        d_str = now_dt.strftime("%d %B %Y").upper()
        if d_str != self._last_date_str:
            self._last_date_str = d_str
            self.update(self._regions.rect("time_panel", "date_text", c, pad=2))
            self.update(self._regions.rect("time_panel", "weekday_text", c, pad=2))
            self.update(self._regions.rect("time_panel", "header_city", c, pad=2))

    def _spawn_fast_vitals_worker(self) -> None:
        if self._fast_inflight:
            return
        self._fast_inflight = True

        def _worker() -> None:
            try:
                cpu = float(psutil.cpu_percent(interval=None))
                ram = float(psutil.virtual_memory().percent)
                up_s = max(0, int(time.time() - psutil.boot_time()))
                hh, rem = divmod(up_s, 3600)
                mm, ss = divmod(rem, 60)
                uptime_str = f"{hh:02d}:{mm:02d}:{ss:02d}"

                up_str = self._net_up_str
                dn_str = self._net_down_str
                now_net = time.perf_counter()
                cur_io = psutil.net_io_counters()
                if self._last_net_counters is not None and (now_net - self._last_net_ts) > 0.2:
                    dt_s = now_net - self._last_net_ts
                    up_bps = max(0.0, (cur_io.bytes_sent - self._last_net_counters.bytes_sent) / dt_s)
                    dn_bps = max(0.0, (cur_io.bytes_recv - self._last_net_counters.bytes_recv) / dt_s)
                    up_str = _format_net_rate(up_bps)
                    dn_str = _format_net_rate(dn_bps)
                self._last_net_counters = cur_io
                self._last_net_ts = now_net

                self._bridge.fast_vitals_ready.emit({
                    "cpu": cpu,
                    "ram": ram,
                    "uptime": uptime_str,
                    "net_up": up_str,
                    "net_down": dn_str,
                })
            except Exception:
                pass
            finally:
                self._fast_inflight = False

        threading.Thread(target=_worker, daemon=True, name="rose-fast-vitals").start()

    def _apply_fast_vitals(self, data: dict[str, Any]) -> None:
        self._cpu_pct = float(data.get("cpu", 0.0))
        self._ram_pct = float(data.get("ram", 0.0))
        self._uptime_str = str(data.get("uptime", self._uptime_str))
        self._net_up_str = str(data.get("net_up", self._net_up_str))
        self._net_down_str = str(data.get("net_down", self._net_down_str))

        ram_eff = self._ram_override_pct if self._ram_override_pct is not None else self._ram_pct
        self.free_memory_btn.setVisible(ram_eff > 90.0)

        sig = (
            int(round(self._cpu_pct)),
            int(round(ram_eff)),
            self._uptime_str,
            self._net_up_str,
            self._net_down_str,
        )
        if sig != self._last_sys_sig:
            self._last_sys_sig = sig
            self.update(self._regions.rect("system_status", "card", self._canvas_rect, pad=2))

    def _spawn_slow_vitals_worker(self) -> None:
        if self._slow_inflight:
            return
        self._slow_inflight = True

        def _worker() -> None:
            payload: dict[str, Any] = {}
            try:
                payload["disk"] = float(psutil.disk_usage("/").percent)
            except Exception:
                pass
            try:
                bat = psutil.sensors_battery()
                if bat is not None:
                    bp = float(bat.percent)
                    payload["bat"] = bp
                    payload["bat_label"] = f"{int(round(bp))}%"
                else:
                    payload["bat"] = 100.0
                    payload["bat_label"] = "AC"
            except Exception:
                pass
            try:
                from actions.health import get_health_card_lines

                lines = [str(item).strip() for item in (get_health_card_lines() or []) if str(item).strip()][:5]
                payload["health"] = lines or ["- Systems nominal"]
            except Exception as exc:
                payload["health"] = [f"- Check unavailable ({exc})"]
            self._slow_inflight = False
            self._bridge.slow_vitals_ready.emit(payload)

        threading.Thread(target=_worker, daemon=True, name="rose-slow-vitals").start()

    def _apply_slow_vitals(self, data: dict[str, Any]) -> None:
        c = self._canvas_rect
        if "disk" in data or "bat" in data:
            self._disk_pct = float(data.get("disk", self._disk_pct))
            self._bat_pct = float(data.get("bat", self._bat_pct))
            self._bat_label = str(data.get("bat_label", self._bat_label))
            self.update(self._regions.rect("system_status", "card", c, pad=2))
        if "health" in data:
            self._health_lines = list(data["health"])
            self.update(self._regions.rect("health_summary", "all_lines", c, pad=2))

    def _spawn_weather_worker(self) -> None:
        if self._weather_inflight:
            return
        self._weather_inflight = True

        def _worker() -> None:
            city_name = "LOCAL"
            details: dict[str, Any] = {}
            try:
                from actions.weather import get_configured_location, get_weather_details

                loc = get_configured_location()
                city_name = str(loc.get("city") or "LOCAL")
                details = get_weather_details(city_name)
            except Exception as exc:
                details = {"city": city_name, "summary": f"Weather unavailable ({exc})"}
            finally:
                self._weather_inflight = False
            self._bridge.weather_ready.emit(details)

        threading.Thread(target=_worker, daemon=True, name="rose-ui-weather").start()

    def _apply_weather_details(self, details: dict[str, Any]) -> None:
        city = str(details.get("city") or self._city_name or "LOCAL").upper()
        self._city_name = city
        self._weather_city = city
        self._weather_icon = str(details.get("icon") or "partly_cloudy")

        temp_c = details.get("temp_c")
        if temp_c is not None:
            self._weather_temp = f"{temp_c}°C"
            lines = []
            cond = str(details.get("condition") or "").strip()
            feels = details.get("feels_like_c")
            hum = details.get("humidity")
            if cond:
                lines.append(f"• {cond}")
            if feels is not None:
                lines.append(f"• feels like {feels}°C")
            if hum is not None:
                lines.append(f"• humidity {hum}%")
            self._weather_lines = "\n".join(lines) or "• Live telemetry active"
        else:
            raw = str(details.get("summary") or "Weather unavailable")
            cleaned = raw.split(":", 1)[-1].strip().rstrip(".")
            parts = [pt.strip() for pt in cleaned.split(",") if pt.strip()]
            if parts and "°" in parts[0]:
                self._weather_temp = parts[0]
                rest = parts[1:4]
            else:
                self._weather_temp = "--°C"
                rest = parts[:3]
            self._weather_lines = "\n".join(f"• {pt}" for pt in rest) if rest else cleaned[:64]

        c = self._canvas_rect
        self.update(self._regions.rect("weather_panel", "card", c, pad=2))
        self.update(self._regions.rect("time_panel", "header_city", c, pad=2))

    def set_ram_override_for_test(self, ram_pct: Optional[float]) -> None:
        self._ram_override_pct = ram_pct
        self._apply_fast_vitals({"cpu": self._cpu_pct, "ram": self._ram_pct, "uptime": self._uptime_str, "net_up": self._net_up_str, "net_down": self._net_down_str})

    def trigger_free_memory(self) -> None:
        self.free_memory_btn.setEnabled(False)
        self.free_memory_btn.setText("Freeing...")

        def _worker() -> None:
            _freed, note = trim_memory_safely()
            self._bridge.memory_freed.emit(note)

        threading.Thread(target=_worker, daemon=True, name="rose-trim-mem").start()

    def _on_memory_freed_done(self, note: str) -> None:
        self.free_memory_btn.setEnabled(True)
        self.free_memory_btn.setText("Free memory")
        self._spawn_fast_vitals_worker()
        self._facade.write_log(f"SYS: {note}")

    # ── Camera Preview & Actions ──────────────────────────────────────────────

    def _launch_camera_action(self, action: str) -> None:
        threading.Thread(target=self._handle_camera_action, args=(action, ""), daemon=True, name="rose-cam-act").start()

    def _handle_camera_action(self, action: str, query: str = "") -> None:
        try:
            from actions.camera_vision import camera_vision_result

            res = camera_vision_result(action, query)
            self._facade.update_camera_preview(res.get("preview_path", ""), res)
            summary = res.get("spoken_summary") or res.get("summary")
            if summary:
                self._facade.write_log(f"ROSE: {summary}")
        except Exception as exc:
            self._facade.write_log(f"ERR: Camera action failed ({exc})")

    def update_camera_preview(self, preview_path: str = "", payload: dict | None = None) -> None:
        data = dict(payload or {})
        self._cam_update_count += 1
        st = str(data.get("status") or ("LIVE" if data.get("live_active") else ("CAPTURE" if preview_path else "IDLE"))).upper()
        self._cam_status = st[:10]
        if data.get("title"):
            conf = data.get("confidence")
            self._cam_headline = f"{data['title']}" + (f" ({conf}%)" if conf is not None else "")
        elif data.get("summary"):
            self._cam_headline = str(data["summary"])[:48]
        ev = data.get("evidence") or data.get("detail")
        if isinstance(ev, list) and ev:
            self._cam_subtext = ", ".join(str(x) for x in ev[:2])[:56]
        elif ev:
            self._cam_subtext = str(ev)[:56]

        if preview_path and Path(preview_path).exists():
            pix = QPixmap(str(preview_path))
            if not pix.isNull():
                self._cam_pixmap = pix
        elif not preview_path and data.get("clear_still"):
            self._cam_pixmap = None

        self.update(self._regions.rect("camera_vision", "card", self._canvas_rect, pad=2))

    # ── User Commands, Mode Toggles, Dialogs ──────────────────────────────────

    def _submit_text_command_text(self, text: str) -> None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return
        if self._facade.on_text_command:
            threading.Thread(
                target=self._facade.on_text_command,
                args=(cleaned,),
                daemon=True,
                name="rose-text-cmd",
            ).start()
        else:
            self._facade.write_log(f"YOU: {cleaned}")

    def _handle_unmute_sys_mic(self) -> None:
        try:
            from core.mic_monitor import set_default_mic_muted

            if set_default_mic_muted(False):
                self._facade._suppress_unmute_log_until = time.monotonic() + 3.5
                self._apply_sys_mic_muted_ui(False)
                self._facade.write_log("SYS: 🎙 Windows microphone unmuted — listening.")
            else:
                self._facade.write_log("ERR: Could not toggle Windows microphone automatically. Check Sound settings or laptop mic key.")
        except Exception as exc:
            self._facade.write_log(f"ERR: Mic unmute failed ({exc})")

    def toggle_mute(self) -> None:
        self._muted = not self._muted
        self._facade.muted = self._muted
        if self._muted:
            self.live_btn._subtle_hover = False
            self.live_btn.set_visual_state(accent="#F59E0B", active=True)
        else:
            self.live_btn._subtle_hover = not self._call_active
            self.live_btn.set_visual_state(accent="#34D399" if self._call_active else "#26E6FF", active=True)
        self.mini_orb.set_muted(self._muted)
        self._apply_state_ui("MUTED" if self._muted else ("PAUSED" if self._paused else "LISTENING"))
        self._facade.write_log("SYS: Microphone muted." if self._muted else "SYS: Microphone active.")

    def toggle_pause(self) -> None:
        self._paused = not self._paused
        self._facade._paused = self._paused
        self.pause_btn.set_visual_state(accent="#F59E0B", active=self._paused)
        self._apply_state_ui("PAUSED" if self._paused else "LISTENING")
        if self._facade.on_pause_toggle:
            try:
                self._facade.on_pause_toggle(self._paused)
            except Exception:
                pass
        self._facade.write_log("SYS: ROSE paused." if self._paused else "SYS: ROSE resumed...")

    def toggle_call(self) -> None:
        self.set_call_active(not self._call_active)

    def toggle_interview_mode(self) -> None:
        if self._facade.on_interview_toggle:
            threading.Thread(target=self._facade.on_interview_toggle, daemon=True, name="rose-interview-toggle").start()
        else:
            self._apply_interview_state_ui(not self._interview_active)

    def set_call_active(self, active: bool) -> None:
        self._bridge.call_active_changed.emit(bool(active))

    def switch_to_mini_orb(self) -> None:
        self.hide()
        self._sync_timer_rate()
        self.mini_orb.set_state("CALL" if self._call_active else self._current_state)
        self.mini_orb.set_muted(self._muted)
        self.mini_orb.set_call_active(self._call_active)
        if self.mini_orb.pos().isNull():
            scr = QApplication.primaryScreen()
            if scr:
                ag = scr.availableGeometry()
                self.mini_orb.move(ag.right() - self.mini_orb.width() - 24, ag.bottom() - self.mini_orb.height() - 28)
        self.mini_orb.show()
        self.mini_orb.raise_()

    def restore_from_mini_orb(self) -> None:
        self.mini_orb.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._sync_timer_rate()

    def toggle_mini_orb(self) -> None:
        if self.mini_orb.isVisible() and not self.isVisible():
            self.restore_from_mini_orb()
        else:
            self.switch_to_mini_orb()

    def toggle_fullscreen(self) -> None:
        if self.isMaximized() or self.isFullScreen():
            self.showNormal()
        else:
            self.showMaximized()

    def open_command_palette(self) -> None:
        self.command_palette.open_centered(self)

    def toggle_debug_overlay(self) -> None:
        c = self._canvas_rect
        s = c.height() / float(REF_HEIGHT)
        self.debug_overlay.move(max(12, c.right() - 310), max(54, int(c.y() + 92 * s)))
        self.debug_overlay.toggle()

    def toggle_debug_regions(self) -> None:
        self._show_debug_regions = not self._show_debug_regions
        if self._show_debug_regions:
            try:
                self._regions.reload()
                self._layout_overlay_controls()
            except Exception as exc:
                self._facade.write_log(f"ERR: ui_regions.json reload failed ({exc})")
        self.update()

    def open_whatsapp_qr_dialog(self) -> None:
        from ui.whatsapp_dialog import WhatsAppQRDialog

        if self._wa_qr_dialog is None or not self._wa_qr_dialog.isVisible():
            self._wa_qr_dialog = WhatsAppQRDialog(on_log=self._facade.write_log, parent=self)
        self._wa_qr_dialog.show()
        self._wa_qr_dialog.raise_()
        self._wa_qr_dialog.activateWindow()

    def open_settings_dialog(self) -> None:
        cfg = load_app_config()
        dlg = SetupSettingsDialog(
            current_voice=str(cfg.get("voice") or "Aoede"),
            effects_enabled=self._facade.effects_enabled(),
            effects_volume=self._facade.get_effects_volume(),
            on_voice_changed=self._on_voice_preview,
            on_sfx_toggled=lambda enabled: self._apply_sound_settings(enabled, self._facade.get_effects_volume()),
            on_sfx_volume_changed=lambda vol: self._apply_sound_settings(self._facade.effects_enabled(), vol),
            on_open_whatsapp_qr=self.open_whatsapp_qr_dialog,
            on_saved=self._on_settings_saved,
            parent=self,
        )
        dlg.exec()

    def _on_voice_preview(self, voice: str) -> None:
        self.set_voice_core_label(get_backend() or "gemini", str(voice or "Windows"))

    def set_voice_core_label(self, backend: str, voice: str) -> None:
        label = str(voice or "Windows").strip() or "Windows"
        if str(backend or "").strip().lower() == "lmstudio" and label.lower() == "aoede":
            label = "Windows"
        if label != self._voice_label:
            self._voice_label = label
            self.update(self._regions.rect("header", "settings_voice_text", self._canvas_rect, pad=2))

    def _open_settings_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        act_settings = menu.addAction("System Settings & Voice Core")
        act_whatsapp = menu.addAction("Link WhatsApp (QR Code)")
        act_palette = menu.addAction("Command Palette (Ctrl+K)")
        act_debug = menu.addAction("Performance Debug Overlay (Ctrl+Shift+D)")
        act_regions = menu.addAction("Toggle UI Regions Debug (Ctrl+Shift+R)")
        chosen = menu.exec(global_pos)
        if chosen == act_settings:
            self.open_settings_dialog()
        elif chosen == act_whatsapp:
            self.open_whatsapp_qr_dialog()
        elif chosen == act_palette:
            self.open_command_palette()
        elif chosen == act_debug:
            self.toggle_debug_overlay()
        elif chosen == act_regions:
            self.toggle_debug_regions()

    def _apply_sound_settings(self, enabled: bool, volume: float) -> None:
        self._facade._effects_enabled = bool(enabled)
        self._facade._effects_volume = max(0.0, min(1.0, float(volume)))
        self._sound.set_volume(self._facade._effects_volume)
        self._sound.set_enabled(self._facade._effects_enabled)

    def _on_settings_saved(self, updates: dict[str, Any]) -> None:
        cfg = load_app_config()
        self.set_voice_core_label(
            str(updates.get("backend") or get_backend() or "gemini"),
            str(updates.get("voice") or cfg.get("voice") or "Aoede"),
        )
        self._facade.write_log("SYS: Settings saved cleanly.")

    def _sync_sound_from_config(self) -> None:
        try:
            cfg = load_app_config()
            enabled = bool(cfg.get("sound_effects", True))
            vol = float(cfg.get("sound_volume", 0.20) or 0.20)
            self._sound.set_volume(vol)
            self._sound._enabled = enabled
            backend = str(cfg.get("backend") or get_backend() or "gemini")
            voice = str(cfg.get("voice") or "Windows")
            self.set_voice_core_label(backend, voice)
        except Exception:
            pass

    def _start_initial_audio_bed(self) -> None:
        if self._shutting_down:
            return
        try:
            cfg = load_app_config()
            if cfg.get("sound_effects", True):
                self._sound.play_startup()
        except Exception:
            pass

    def open_job_center(self, tab_key: str = "radar") -> None:
        if self._job_center_win is None:
            try:
                from actions.job_command_center import get_job_command_center
                from ui.job_center import JobCenterWindow

                self._job_center_win = JobCenterWindow(backend=get_job_command_center())
            except Exception:
                if self._facade.on_jobs_toggle:
                    self._facade.on_jobs_toggle()
                    return
                from ui.job_center import JobCenterWindow

                self._job_center_win = JobCenterWindow()
        self._job_center_win.select_tab(tab_key or "radar")
        self._job_center_win.show()
        self._job_center_win.raise_()
        self._job_center_win.activateWindow()

    def _on_kill_switch_activated(self, *_args: Any) -> None:
        if not self._shutting_down:
            self._facade._safe_emit(self._bridge.kill_banner_changed, True, "Safety Kill Switch Active")

    def _unlock_kill_switch_local(self) -> None:
        try:
            from core.permissions import security_manager

            security_manager.unlock_kill_switch()
            self._apply_kill_banner_ui(False, "")
            self._facade.write_log("SYS: Safety kill-switch unlocked locally — normal operation restored.")
        except Exception:
            pass

    def trigger_kill_switch_hotkey(self) -> None:
        try:
            from core.permissions import security_manager

            if security_manager.is_killed:
                self._unlock_kill_switch_local()
            else:
                security_manager.trigger_kill_switch("Global hotkey Ctrl+Alt+Shift+K")
                self._apply_kill_banner_ui(True, "Global hotkey Ctrl+Alt+Shift+K")
                self._facade.write_log("ERR: KILL SWITCH ENGAGED — autonomous loops halted.")
        except Exception:
            pass

    def _handle_escape_key(self) -> None:
        if self.command_palette.isVisible():
            self.command_palette.reject()
        elif self.debug_overlay.isVisible():
            self.debug_overlay.hide()
        elif self._show_debug_regions:
            self._show_debug_regions = False
            self.update()
        elif self.isFullScreen():
            self.showNormal()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)

        if key == Qt.Key.Key_F4 and not (ctrl or alt):
            self.toggle_mute()
        elif key == Qt.Key.Key_F5 and not (ctrl or alt):
            self.toggle_pause()
        elif key == Qt.Key.Key_F8 and not (ctrl or alt):
            self.toggle_interview_mode()
        elif key == Qt.Key.Key_F9 and not (ctrl or alt):
            self.toggle_mini_orb()
        elif key == Qt.Key.Key_F10 and not (ctrl or alt):
            self.open_job_center("radar")
        elif key == Qt.Key.Key_F11 and not (ctrl or alt):
            self.toggle_fullscreen()
        elif ctrl and shift and key == Qt.Key.Key_D and not alt:
            self.toggle_debug_overlay()
        elif ctrl and shift and key == Qt.Key.Key_R and not alt:
            self.toggle_debug_regions()
        elif ctrl and key == Qt.Key.Key_K and not (shift or alt):
            self.open_command_palette()
        elif key == Qt.Key.Key_Escape:
            self._handle_escape_key()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    # ── Qt State & Audio Signal Handlers ──────────────────────────────────────

    def _apply_state_ui(self, state: str) -> None:
        norm = str(state or "IDLE").strip().upper()
        if norm == "ERROR":
            self._error_flash = 1.0
        self._current_state = norm
        if self._call_active and norm in ("IDLE", "LISTENING"):
            norm = "CALL"

        self._sync_timer_rate()
        if self.mini_orb.isVisible():
            self.mini_orb.set_state(norm)

        c = self._canvas_rect
        self.update(self._regions.rect("center", "pedestal_status", c, pad=2))
        self.update(self._regions.rect("center", "globe_box", c, pad=2))
        self.update(self._regions.rect("conversation", "status", c, pad=2))
        self.update(self._regions.rect("header", "online_indicator", c, pad=2))
        self._sync_sound_for_state()

    def _apply_audio_level_ui(self, level: float) -> None:
        self._audio_level = max(0.0, min(1.0, float(level)))
        if self.mini_orb.isVisible():
            self.mini_orb.set_audio_level(self._audio_level)

    def _apply_mic_level_ui(self, level: float) -> None:
        self._mic_level = max(0.0, min(1.0, float(level)))

    def _apply_call_ui(self, active: bool) -> None:
        self._call_active = bool(active)
        self._call_started_at = time.perf_counter() if self._call_active else 0.0
        if not self._muted:
            self.live_btn._subtle_hover = not self._call_active
            self.live_btn.set_visual_state(accent="#34D399" if self._call_active else "#26E6FF", active=True)
        self._sync_timer_rate()
        if self.mini_orb.isVisible():
            self.mini_orb.set_call_active(self._call_active)
        c = self._canvas_rect
        self.update(self._regions.rect("center", "pedestal_status", c, pad=2))
        self.update(self._regions.rect("center", "globe_box", c, pad=2))
        self.update(self._regions.rect("conversation", "status", c, pad=2))
        self.update(self._regions.rect("header", "online_indicator", c, pad=2))

    def _apply_focus_section(self, section: str, _duration_ms: int) -> None:
        sec_map = {
            "time": ("time_panel", "card"),
            "weather": ("weather_panel", "card"),
            "system": ("system_status", "card"),
            "health": ("health_summary", "card"),
            "camera": ("camera_vision", "card"),
        }
        target = sec_map.get(str(section or "").strip().lower())
        if target:
            self.update(self._regions.rect(target[0], target[1], self._canvas_rect, pad=2))

    def _apply_location_refresh(self) -> None:
        self._refresh_city_cache()
        self._spawn_weather_worker()
        self._tick_clock_ui()

    def _apply_interview_state_ui(self, active: bool) -> None:
        self._interview_active = bool(active)
        self.interview_btn.set_visual_state(accent="#34D399", active=self._interview_active)

    def _apply_sys_mic_muted_ui(self, muted: bool) -> None:
        self._sys_mic_muted = bool(muted)
        self.unmute_mic_btn.setVisible(self._sys_mic_muted)
        self.update(self._regions.rect("header", "online_indicator", self._canvas_rect, pad=2))

    def _play_sfx_ui(self, kind: str) -> None:
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        if not self._facade.effects_enabled():
            return
        if kind == "success":
            self._sound.play_success()
        elif kind == "error":
            self._sound.play_error()

    def _apply_window_command(self, cmd: str) -> None:
        if cmd == "popup":
            if self.mini_orb.isVisible() and not self.isVisible():
                self.restore_from_mini_orb()
            self.showNormal()
            self.raise_()
            self.activateWindow()
        elif cmd == "minimize":
            self.showMinimized()
        elif cmd == "mini":
            self.switch_to_mini_orb()
        elif cmd == "restore_mini":
            self.restore_from_mini_orb()
        elif cmd == "shutdown":
            self._shutting_down = True
            self._quit_app_process()

    def _show_health_dialog_ui(self, query: str, data_str: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("R.O.S.E System Health Hologram")
        dlg.setFixedSize(460, 320)
        dlg.setStyleSheet(f"QDialog {{ background-color: {PALETTE.bg_navy}; border: 1px solid {PALETTE.border_medium}; }}")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        hdr = QLabel(f"SYSTEM HEALTH REPORT — {(query or 'ALL').upper()}", dlg)
        hdr.setStyleSheet(f"font-size: 13px; font-weight: 700; letter-spacing: 1.2px; color: {PALETTE.accent_cyan};")
        lay.addWidget(hdr)

        box = QTextEdit(dlg)
        box.setReadOnly(True)
        box.setPlainText(str(data_str or "No health data returned."))
        lay.addWidget(box, 1)

        close_btn = QPushButton("CLOSE", dlg)
        close_btn.setFixedWidth(100)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(dlg.accept)
        lay.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

        self._health_dialogs.append(dlg)
        dlg.finished.connect(lambda: self._health_dialogs.remove(dlg) if dlg in self._health_dialogs else None)
        dlg.show()

    def _apply_kill_banner_ui(self, visible: bool, reason: str) -> None:
        self.kill_banner_btn.setVisible(bool(visible))
        if visible and reason:
            self.kill_banner_btn.setText(f"KILL SWITCH ENGAGED ({reason}) — Click to reset")

    def _sync_sound_for_state(self) -> None:
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        if not self._facade.effects_enabled():
            return
        if self._current_state == "THINKING":
            self._sound.start_thinking()
        else:
            self._sound.stop_thinking()

    # ── Lifecycle & Telemetry ─────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_timer_rate()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._ui_timer.stop()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_timer_rate()

    def closeEvent(self, event) -> None:
        if not self._shutting_down:
            self._shutting_down = True
            self._quit_app_process()
        event.accept()

    def _quit_app_process(self) -> None:
        self._facade._closing = True
        self._ui_timer.stop()
        self.mini_orb.hide()
        if self._wa_qr_dialog is not None:
            try:
                self._wa_qr_dialog.close()
            except Exception:
                pass
            self._wa_qr_dialog = None
        self._sound.stop_all()
        shutdown_tk_host()
        on_sd = getattr(self._facade, "on_shutdown", None)
        if callable(on_sd):
            try:
                on_sd()
            except Exception:
                pass
        self.close()
        if getattr(self._facade, "_allow_process_exit", True):
            app = QApplication.instance()
            if app:
                app.quit()

    def collect_perf_stats(self) -> dict[str, Any]:
        times = sorted(self._paint_ms_hist) or [0.0]
        avg_ms = round(sum(times) / len(times), 3)
        p95_idx = min(len(times) - 1, int(round(0.95 * (len(times) - 1))))
        p95_ms = round(times[p95_idx], 3)

        dts = list(self._frame_dt_hist)
        actual_fps = round(len(dts) / max(0.01, sum(dts)), 1) if dts else float(self._target_fps())

        try:
            proc = self._psutil_proc or psutil.Process()
            if self._psutil_proc is None:
                self._psutil_proc = proc
                proc.cpu_percent(interval=None)
            ram_mb = round(proc.memory_info().rss / (1024.0 * 1024.0), 1)
            now_mono = time.perf_counter()
            if now_mono - self._last_ui_cpu_ts >= 0.35:
                raw_cpu = proc.cpu_percent(interval=None)
                n_cores = max(1, psutil.cpu_count() or 1)
                self._cached_ui_cpu_pct = round(raw_cpu / n_cores, 1)
                self._last_ui_cpu_ts = now_mono
            cpu_pct = self._cached_ui_cpu_pct
            sys_ram = round(psutil.virtual_memory().percent, 1)
        except Exception:
            ram_mb, cpu_pct, sys_ram = 0.0, 0.0, 0.0

        return {
            "backend": get_backend() or "gemini",
            "state": self._current_state,
            "actual_fps": actual_fps,
            "target_fps": self._target_fps(),
            "avg_frame_ms": avg_ms,
            "p95_frame_ms": p95_ms,
            "ui_cpu_pct": cpu_pct,
            "proc_ram_mb": ram_mb,
            "sys_ram_pct": sys_ram,
            "audio_hz": 30.0 if self._facade._audio_emit_count > 0 else 0.0,
            "message_count": self.conversation_panel.message_count(),
            "pending_log_queue": self.conversation_panel.pending_queue_size(),
        }


class JarvisUI:
    """Thread-safe public UI facade preserved 100% compatible with main.py & lmstudio_runtime.py."""

    def __init__(
        self,
        backend_text: str | None = None,
        muted: bool = False,
        paused: bool = False,
        allow_process_exit: bool = True,
        show_on_init: bool = True,
        **_unused: Any,
    ):
        self._app = QApplication.instance()
        if self._app is None:
            self._app = QApplication(sys.argv or ["rose"])
        load_rose_fonts()
        self._app.setFont(QFont("Segoe UI", 9))
        self._app.setStyleSheet(build_shared_qss())

        self.muted = bool(muted)
        self._paused = bool(paused)
        self._allow_process_exit = bool(allow_process_exit)
        self._closing = False
        self._suppress_unmute_log_until = 0.0
        self._effects_enabled = True
        self._effects_volume = 0.20

        self.on_text_command: Optional[Callable[[str], Any]] = None
        self.on_pause_toggle: Optional[Callable[[bool], Any]] = None
        self.on_effects_state_change: Optional[Callable[[bool], Any]] = None
        self.on_interview_toggle: Optional[Callable[[], Any]] = None
        self.on_jobs_toggle: Optional[Callable[[], Any]] = None
        self.on_shutdown: Optional[Callable[[], Any]] = None

        self._audio_lock = threading.Lock()
        self._audio_smoothed = 0.0
        self._last_audio_emit_ts = 0.0
        self._last_mic_emit_ts = 0.0
        self._audio_emit_count = 0

        self._bridge = _QtBridge(self._app)
        try:
            self._app.aboutToQuit.connect(self._mark_closing)
        except Exception:
            pass
        self.main_window = RoseMainWindow(self, self._bridge)
        self.root = _QtRootCompat(self._app, self.main_window, self._bridge)

        if backend_text:
            cfg = load_app_config()
            self.main_window.set_voice_core_label(str(backend_text), str(cfg.get("voice") or "Windows"))

        try:
            cfg = load_app_config()
            self._effects_enabled = bool(cfg.get("sound_effects", True))
            self._effects_volume = max(0.0, min(1.0, float(cfg.get("sound_volume", 0.20) or 0.20)))
            self.main_window._sound.set_volume(self._effects_volume)
            self.main_window._sound._enabled = self._effects_enabled
        except Exception:
            pass

        if show_on_init:
            self.main_window.show()
            self._app.processEvents()

    @property
    def app(self) -> QApplication:
        return self._app

    def wait_for_api_key(self) -> None:
        backend = (get_backend() or "gemini").strip().lower()
        if backend == "lmstudio":
            return
        if has_gemini_api_key():
            return
        done = threading.Event()

        def _prompt() -> None:
            self.main_window.open_settings_dialog()
            done.set()

        if threading.current_thread() is threading.main_thread():
            _prompt()
        else:
            self._bridge.after_invoke.emit(_prompt, ())
            done.wait()

    def _mark_closing(self) -> None:
        self._closing = True

    def _safe_emit(self, signal_obj: Any, *args: Any) -> None:
        if self._closing:
            return
        try:
            signal_obj.emit(*args)
        except RuntimeError:
            self._closing = True

    def write_log(self, text: str) -> None:
        self._safe_emit(self._bridge.log_appended, str(text or ""))

    def write_debug(self, text: str, level: str = "INFO") -> None:
        lvl = str(level or "INFO").upper()
        if lvl in ("ERR", "ERROR", "WARN", "WARNING"):
            self._safe_emit(self._bridge.log_appended, f"ERR: {text}")

    def set_state(self, state: str) -> None:
        self._safe_emit(self._bridge.state_changed, str(state or "IDLE"))

    def start_call(self) -> None:
        self._safe_emit(self._bridge.call_active_changed, True)

    def end_call(self) -> None:
        self._safe_emit(self._bridge.call_active_changed, False)

    def set_call_active(self, active: bool) -> None:
        self._safe_emit(self._bridge.call_active_changed, bool(active))

    def _emit_smoothed_audio(self, raw_level: float) -> None:
        if self._closing:
            return
        now = time.perf_counter()
        lvl = max(0.0, min(1.0, float(raw_level)))
        with self._audio_lock:
            self._audio_smoothed += (lvl - self._audio_smoothed) * 0.42
            if (now - self._last_audio_emit_ts) < (1.0 / 30.0):
                return
            self._last_audio_emit_ts = now
            self._audio_emit_count += 1
            emit_val = self._audio_smoothed
        self._safe_emit(self._bridge.audio_level_updated, emit_val)

    def set_mic_level(self, level: float) -> None:
        if self._closing:
            return
        lvl = max(0.0, min(1.0, float(level)))
        now = time.perf_counter()
        with self._audio_lock:
            if (now - self._last_mic_emit_ts) >= (1.0 / 30.0):
                self._last_mic_emit_ts = now
                self._safe_emit(self._bridge.mic_level_updated, lvl)
        self._emit_smoothed_audio(lvl)

    def set_audio_level(self, level: float) -> None:
        self._emit_smoothed_audio(level)

    def mark_user_activity(self, active: bool = True) -> None:
        if self._closing:
            return
        if active and not self.muted and not self._paused:
            try:
                if self.main_window._current_state in ("IDLE", "INITIALISING"):
                    self.set_state("LISTENING")
            except RuntimeError:
                self._closing = True

    def set_user_speaking(self, value: bool) -> None:
        self.mark_user_activity(bool(value))

    def set_sys_mic_muted(self, muted: bool) -> None:
        self._safe_emit(self._bridge.sys_mic_muted_changed, bool(muted))

    def pop_up_screen(self) -> None:
        self._safe_emit(self._bridge.window_command, "popup")

    def minimize_screen(self) -> None:
        self._safe_emit(self._bridge.window_command, "minimize")

    def switch_to_mini_orb(self) -> None:
        self._safe_emit(self._bridge.window_command, "mini")

    def restore_from_mini_orb(self) -> None:
        self._safe_emit(self._bridge.window_command, "restore_mini")

    def focus_panel(self, section: str, duration_ms: int = 4200) -> None:
        self._safe_emit(self._bridge.focus_section_requested, str(section or ""), int(duration_ms))

    def refresh_location(self) -> None:
        self._safe_emit(self._bridge.location_refreshed)

    def update_camera_preview(self, preview_path: str = "", payload: dict | None = None) -> None:
        self._safe_emit(self._bridge.camera_preview_updated, str(preview_path or ""), dict(payload or {}))

    def set_interview_mode_active(self, active: bool) -> None:
        self._safe_emit(self._bridge.interview_mode_changed, bool(active))

    def trigger_kill_switch_hotkey(self) -> None:
        if not self._closing:
            self._safe_emit(self._bridge.after_invoke, self.main_window.trigger_kill_switch_hotkey, ())

    def get_effects_volume(self) -> float:
        return self._effects_volume

    def effects_enabled(self) -> bool:
        return self._effects_enabled

    def play_success_sfx(self) -> None:
        self._safe_emit(self._bridge.sfx_requested, "success")

    def play_error_sfx(self) -> None:
        self._safe_emit(self._bridge.sfx_requested, "error")

    def show_health_hologram(self, query: str, data_str: str) -> None:
        self._safe_emit(self._bridge.health_hologram_requested, str(query or ""), str(data_str or ""))

    def open_job_center(self, tab_key: str = "radar") -> None:
        self._safe_emit(self._bridge.job_center_requested, str(tab_key or "radar"))

    def show_whatsapp_qr(self) -> None:
        self._safe_emit(self._bridge.whatsapp_qr_requested)

    def get_perf_stats(self) -> dict[str, Any]:
        return self.main_window.collect_perf_stats()

    def shutdown(self) -> None:
        self._safe_emit(self._bridge.window_command, "shutdown")
