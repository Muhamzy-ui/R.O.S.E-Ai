"""
R.O.S.E (Project Mirror) — MIRROR K2 Left Telemetry Column.

Stacked chamfered glowing HUD modules matching the reference command console:
- TIME • <CITY> (custom painted analog dial + glowing digital HH:MM :SS + date/day)
- WEATHER • <CITY> (custom vector condition icon + temp + structured metrics via Open-Meteo)
- SYSTEM STATUS (UPTIME, CPU, RAM, DISK, BATTERY bars + live net up/down + inline sparkline)
- >90% RAM proactive guard offering non-silent `Free memory` working-set trim
- HEALTH SUMMARY (live background-fetched lines from `actions.health.get_health_card_lines`)
- CAMERA VISION (`CameraPreviewPanel`)
"""

from __future__ import annotations

import collections
import ctypes
import datetime as dt
import gc
import math
import threading
import time
from typing import Any, Callable, Optional

import psutil
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.camera_panel import CameraPreviewPanel
from ui.theme import HudDot, HudFrame, METRICS, PALETTE


class SparklineWidget(QWidget):
    """Lightweight 24-sample CPU telemetry sparkline rendered inside SYSTEM STATUS."""

    def __init__(self, max_samples: int = 24, color: str = PALETTE.accent_cyan, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._max_samples = max(4, int(max_samples))
        self._samples: collections.deque[float] = collections.deque([0.0] * self._max_samples, maxlen=self._max_samples)
        self._color = QColor(color)
        self.setFixedHeight(14)
        self.setFixedWidth(64)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def push_sample(self, value_pct: float) -> None:
        v = max(0.0, min(100.0, float(value_pct)))
        self._samples.append(v)
        self.update()

    def samples(self) -> list[float]:
        return list(self._samples)

    def paintEvent(self, event) -> None:
        pts = list(self._samples)
        n = len(pts)
        if n < 2:
            return
        w = float(self.width())
        h = float(self.height())
        if w <= 4 or h <= 4:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        line_path = QPainterPath()
        fill_path = QPainterPath()
        step_x = (w - 2.0) / max(1, n - 1)
        for idx, val in enumerate(pts):
            x = 1.0 + idx * step_x
            y = h - 2.0 - (val / 100.0) * (h - 4.0)
            if idx == 0:
                line_path.moveTo(x, y)
                fill_path.moveTo(x, h - 1.0)
                fill_path.lineTo(x, y)
            else:
                line_path.lineTo(x, y)
                fill_path.lineTo(x, y)

        fill_path.lineTo(1.0 + (n - 1) * step_x, h - 1.0)
        fill_path.closeSubpath()

        fill_col = QColor(self._color)
        fill_col.setAlpha(45)
        painter.fillPath(fill_path, fill_col)
        painter.setPen(QPen(self._color, 1.3))
        painter.drawPath(line_path)


class _AnalogClockDial(QWidget):
    """Minimal luminous cyan analog dial beside the digital HUD clock."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(46, 46)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._dt = dt.datetime.now()

    def set_datetime(self, value: dt.datetime) -> None:
        self._dt = value
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        cx, cy = w * 0.5, h * 0.5
        r = min(w, h) * 0.44

        # Subtle glowing face
        p.setBrush(QColor(4, 18, 38, 195))
        p.setPen(QPen(QColor(38, 230, 255, 160), 1.35))
        p.drawEllipse(QPointF(cx, cy), r, r)

        # Inner dashed accent circle
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(38, 230, 255, 58), 0.9))
        p.drawEllipse(QPointF(cx, cy), r * 0.72, r * 0.72)

        # 12 hour markers
        for idx in range(12):
            ang = math.radians(idx * 30.0 - 90.0)
            is_card = (idx % 3 == 0)
            p.setPen(QPen(QColor(135, 245, 255, 220 if is_card else 120), 1.3 if is_card else 0.9))
            r_in = r * (0.76 if is_card else 0.84)
            p.drawLine(
                QPointF(cx + math.cos(ang) * r_in, cy + math.sin(ang) * r_in),
                QPointF(cx + math.cos(ang) * (r * 0.94), cy + math.sin(ang) * (r * 0.94)),
            )

        sec = self._dt.second
        minute = self._dt.minute + sec / 60.0
        hour = (self._dt.hour % 12) + minute / 60.0

        # Hour hand
        ah = math.radians(hour * 30.0 - 90.0)
        p.setPen(QPen(QColor(225, 250, 255, 235), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(ah) * r * 0.48, cy + math.sin(ah) * r * 0.48))

        # Minute hand
        am = math.radians(minute * 6.0 - 90.0)
        p.setPen(QPen(QColor(38, 230, 255, 245), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(am) * r * 0.68, cy + math.sin(am) * r * 0.68))

        # Second hand
        asec = math.radians(sec * 6.0 - 90.0)
        p.setPen(QPen(QColor(130, 252, 255, 195), 0.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(asec) * r * 0.78, cy + math.sin(asec) * r * 0.78))

        # Center hub
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(38, 230, 255, 245))
        p.drawEllipse(QPointF(cx, cy), 2.1, 2.1)


class _WeatherIconWidget(QWidget):
    """Crisp vector holographic weather icon (sun/partly cloudy/cloud/rain/storm/snow)."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(44, 40)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._kind = "partly_cloudy"

    def set_kind(self, kind: str) -> None:
        if kind and kind != self._kind:
            self._kind = kind
            self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        kind = self._kind

        if kind in ("clear", "partly_cloudy"):
            sx, sy, sr = (22.0, 20.0, 9.0) if kind == "clear" else (14.5, 14.5, 7.2)
            # Soft warm solar glow
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 200, 60, 48))
            p.drawEllipse(QPointF(sx, sy), sr * 1.65, sr * 1.65)
            p.setBrush(QColor(253, 212, 72))
            p.drawEllipse(QPointF(sx, sy), sr, sr)
            p.setPen(QPen(QColor(253, 224, 110, 220), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i in range(8):
                a = math.radians(i * 45.0)
                p.drawLine(
                    QPointF(sx + math.cos(a) * (sr + 2.0), sy + math.sin(a) * (sr + 2.0)),
                    QPointF(sx + math.cos(a) * (sr + 5.0), sy + math.sin(a) * (sr + 5.0)),
                )

        if kind in ("partly_cloudy", "cloudy", "rain", "storm", "snow"):
            ox, oy = (10.0, 17.0) if kind == "partly_cloudy" else (7.0, 12.0)
            cloud_col = QColor(140, 195, 230, 235) if kind not in ("storm", "rain") else QColor(110, 165, 215, 235)
            p.setPen(QPen(QColor(210, 245, 255, 170), 0.9))
            p.setBrush(cloud_col)
            path = QPainterPath()
            path.addEllipse(QRectF(ox + 2, oy + 4, 12, 11))
            path.addEllipse(QRectF(ox + 9, oy, 14, 14))
            path.addEllipse(QRectF(ox + 18, oy + 5, 11, 10))
            path.addRoundedRect(QRectF(ox + 5, oy + 8, 21, 7.5), 3.5, 3.5)
            p.drawPath(path.simplified())

        if kind == "rain":
            p.setPen(QPen(QColor(56, 236, 255, 235), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for rx in (13.0, 20.0, 27.0):
                p.drawLine(QPointF(rx, 29.0), QPointF(rx - 2.2, 35.5))
        elif kind == "snow":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(224, 252, 255, 235))
            for sx_pos in (13.0, 20.5, 28.0):
                p.drawEllipse(QPointF(sx_pos, 32.5), 1.7, 1.7)
        elif kind == "storm":
            bolt = QPolygonF([QPointF(21.0, 25.0), QPointF(16.5, 32.0), QPointF(20.5, 32.0), QPointF(18.0, 38.0), QPointF(25.5, 30.0), QPointF(21.5, 30.0)])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 212, 72))
            p.drawPolygon(bolt)


class _HudProgressBar(QWidget):
    """Slim chamfered-track neon telemetry bar matching the SYSTEM STATUS panel."""

    def __init__(self, initial_color: str = PALETTE.accent_cyan, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(7)
        self.setMinimumWidth(68)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._value = 0.0
        self._color = QColor(initial_color)

    def set_value(self, pct: float, color: Optional[str] = None) -> None:
        v = max(0.0, min(100.0, float(pct)))
        changed = abs(v - self._value) >= 0.4
        self._value = v
        if color:
            c = QColor(color)
            if c != self._color:
                self._color = c
                changed = True
        if changed:
            self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(0.0, 1.0, float(self.width()), float(self.height()) - 2.0)
        if r.width() <= 4:
            return
        p.setPen(QPen(QColor(38, 230, 255, 45), 0.8))
        p.setBrush(QColor(5, 20, 42, 220))
        p.drawRoundedRect(r, 2.5, 2.5)

        frac = max(0.0, min(1.0, self._value / 100.0))
        fw = r.width() * frac
        if fw > 1.5:
            fill_r = QRectF(r.left(), r.top(), max(3.0, fw), r.height())
            grad = QLinearGradient(fill_r.left(), 0, fill_r.right(), 0)
            c_dim = QColor(self._color).darker(125)
            c_hi = QColor(self._color).lighter(120)
            grad.setColorAt(0.0, c_dim)
            grad.setColorAt(0.78, self._color)
            grad.setColorAt(1.0, c_hi)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawRoundedRect(fill_r, 2.5, 2.5)


def trim_memory_safely() -> tuple[int, str]:
    """Explicitly trim Python + Win32 working set memory on user request."""
    before_mb = psutil.Process().memory_info().rss / (1024.0 * 1024.0)
    gc.collect()
    try:
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.psapi.EmptyWorkingSet(handle)
    except Exception:
        pass
    after_mb = psutil.Process().memory_info().rss / (1024.0 * 1024.0)
    freed = max(0, int(round(before_mb - after_mb)))
    return freed, f"Trimmed working set (~{freed} MB reclaimed; RSS {after_mb:.0f} MB)"


class _InfoBridge(QObject):
    weather_ready = Signal(object)
    health_ready = Signal(object)
    memory_freed = Signal(str)


def _format_net_rate(bytes_per_sec: float) -> str:
    kbps = max(0.0, float(bytes_per_sec)) / 1024.0
    if kbps >= 1024.0:
        return f"{kbps / 1024.0:.1f} MB/s"
    return f"{kbps:.1f} KB/s"


def _bar_color_for_pct(pct: float, kind: str = "load") -> str:
    if kind == "battery":
        if pct < 20.0:
            return PALETTE.danger
        if pct < 40.0:
            return PALETTE.warning
        return PALETTE.success
    if pct >= 85.0:
        return PALETTE.danger
    if pct >= 65.0:
        return PALETTE.warning
    return PALETTE.accent_cyan


class InfoColumn(QFrame):
    """Left telemetry HUD column matching the MIRROR K2 command console."""

    def __init__(
        self,
        on_camera_action: Optional[Callable[[str, str], Any]] = None,
        on_free_memory_note: Optional[Callable[[str], Any]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._on_free_memory_note = on_free_memory_note
        self._ram_override_pct: Optional[float] = None
        self._last_metrics: dict[str, Any] = {
            "cpu": 0.0,
            "ram": 0.0,
            "disk": 0.0,
            "bat": 100.0,
            "net_up": 0.0,
            "net_down": 0.0,
        }
        self._last_net_counters = None
        self._last_net_ts = 0.0
        self._weather_inflight = False
        self._health_inflight = False

        self.setObjectName("InfoColumn")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("QFrame#InfoColumn { background: transparent; border: none; }")
        self.setFixedWidth(282)

        self._bridge = _InfoBridge()
        self._bridge.weather_ready.connect(self._apply_weather_details)
        self._bridge.health_ready.connect(self._apply_health_lines)
        self._bridge.memory_freed.connect(self._on_memory_freed_done)

        self._section_cards: dict[str, HudFrame] = {}
        self._build_ui(on_camera_action)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._on_second_tick)

        self._stat_timer = QTimer(self)
        self._stat_timer.setInterval(15000)
        self._stat_timer.timeout.connect(self._refresh_slow_vitals)

        self._focus_clear_timer = QTimer(self)
        self._focus_clear_timer.setSingleShot(True)
        self._focus_clear_timer.timeout.connect(self._clear_section_focus)

        try:
            psutil.cpu_percent(interval=None)
            self._last_net_counters = psutil.net_io_counters()
            self._last_net_ts = time.perf_counter()
        except Exception:
            pass

        self._on_second_tick()
        self._refresh_slow_vitals()
        self.refresh_weather_async()
        self.refresh_health_async()
        self.start_timers()

    def _make_card(self, name: str = "SubCard") -> HudFrame:
        card = HudFrame(accent=PALETTE.accent_cyan, chamfer=9.0, fill_alpha=178, parent=self)
        card.setObjectName(name)
        return card

    def _section_header(self, parent_card: QWidget, text: str) -> tuple[QHBoxLayout, QLabel]:
        row = QHBoxLayout()
        row.setSpacing(6)
        dot = HudDot(PALETTE.accent_cyan, diameter=6, parent=parent_card)
        lbl = QLabel(text, parent_card)
        lbl.setStyleSheet(
            f"color: {PALETTE.text_secondary}; font-family: {METRICS.font_family_display}; "
            "font-size: 10px; font-weight: 700; letter-spacing: 1.4px; background: transparent;"
        )
        row.addWidget(dot)
        row.addWidget(lbl)
        row.addStretch(1)
        return row, lbl

    def _build_ui(self, on_camera_action: Optional[Callable[[str, str], Any]]) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scroll container only activates if window height is compressed below natural stack height
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet("background: transparent; border: none;")
        self._scroll.viewport().setStyleSheet("background: transparent;")

        container = QWidget(self._scroll)
        container.setStyleSheet("background: transparent;")
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 2, 0)
        root.setSpacing(6)
        root.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinAndMaxSize)

        # ── 1) TIME • <CITY> ───────────────────────────────────────────────────
        time_card = self._make_card("TimeCard")
        self._section_cards["time"] = time_card
        tl = QVBoxLayout(time_card)
        tl.setContentsMargins(12, 8, 12, 9)
        tl.setSpacing(5)

        t_hdr, self.time_title_lbl = self._section_header(time_card, "TIME")
        tl.addLayout(t_hdr)

        t_body = QHBoxLayout()
        t_body.setSpacing(11)
        self.analog_dial = _AnalogClockDial(time_card)
        t_body.addWidget(self.analog_dial)

        t_right = QVBoxLayout()
        t_right.setSpacing(1)
        clock_line = QHBoxLayout()
        clock_line.setSpacing(6)
        self.clock_lbl = QLabel("--:--", time_card)
        self.clock_lbl.setStyleSheet(
            f"color: #46F0FF; font-family: {METRICS.font_family_display}; "
            "font-size: 22px; font-weight: 700; letter-spacing: 0.8px; background: transparent;"
        )
        self.clock_sec_lbl = QLabel(":--", time_card)
        self.clock_sec_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.clock_sec_lbl.setStyleSheet(
            f"color: {PALETTE.text_secondary}; font-family: {METRICS.font_family_mono}; "
            "font-size: 11px; font-weight: 700; background: transparent;"
        )
        clock_line.addWidget(self.clock_lbl)
        clock_line.addStretch(1)
        clock_line.addWidget(self.clock_sec_lbl)
        t_right.addLayout(clock_line)

        self.date_lbl = QLabel("----", time_card)
        self.date_lbl.setStyleSheet(
            "color: #F6D05C; font-size: 9.5px; font-weight: 700; letter-spacing: 0.7px; background: transparent;"
        )
        self.day_lbl = QLabel("----", time_card)
        self.day_lbl.setStyleSheet(
            f"color: {PALETTE.text_primary}; font-size: 9.5px; font-weight: 600; letter-spacing: 0.8px; background: transparent;"
        )
        t_right.addWidget(self.date_lbl)
        t_right.addWidget(self.day_lbl)
        t_body.addLayout(t_right, 1)
        tl.addLayout(t_body)
        root.addWidget(time_card)

        # ── 2) WEATHER • <CITY> ────────────────────────────────────────────────
        weather_card = self._make_card("WeatherCard")
        self._section_cards["weather"] = weather_card
        wl = QVBoxLayout(weather_card)
        wl.setContentsMargins(12, 8, 12, 8)
        wl.setSpacing(4)

        w_hdr, self.weather_title_lbl = self._section_header(weather_card, "WEATHER")
        wl.addLayout(w_hdr)

        w_body = QHBoxLayout()
        w_body.setSpacing(9)
        self.weather_icon = _WeatherIconWidget(weather_card)
        w_body.addWidget(self.weather_icon, 0, Qt.AlignmentFlag.AlignTop)

        w_right = QVBoxLayout()
        w_right.setSpacing(2)
        w_top = QHBoxLayout()
        self.weather_temp_lbl = QLabel("--°C", weather_card)
        self.weather_temp_lbl.setStyleSheet(
            f"color: #46F0FF; font-family: {METRICS.font_family_display}; "
            "font-size: 19px; font-weight: 700; background: transparent;"
        )
        self.weather_city_lbl = QLabel("--", weather_card)
        self.weather_city_lbl.setStyleSheet(
            f"color: {PALETTE.text_secondary}; font-size: 9.5px; font-weight: 700; letter-spacing: 0.8px; background: transparent;"
        )
        w_top.addWidget(self.weather_temp_lbl)
        w_top.addStretch(1)
        w_top.addWidget(self.weather_city_lbl)
        w_right.addLayout(w_top)

        self.weather_meta_lbl = QLabel("• Loading local weather...", weather_card)
        self.weather_meta_lbl.setWordWrap(True)
        self.weather_meta_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.weather_meta_lbl.setMinimumHeight(42)
        self.weather_meta_lbl.setStyleSheet(
            f"color: {PALETTE.text_secondary}; font-size: 9.5px; line-height: 1.25; background: transparent;"
        )
        w_right.addWidget(self.weather_meta_lbl)
        w_body.addLayout(w_right, 1)
        wl.addLayout(w_body)
        root.addWidget(weather_card)

        # ── 3) SYSTEM STATUS ───────────────────────────────────────────────────
        sys_card = self._make_card("SystemStatusCard")
        self._section_cards["system"] = sys_card
        sl = QVBoxLayout(sys_card)
        sl.setContentsMargins(12, 8, 12, 9)
        sl.setSpacing(4)

        s_hdr, _ = self._section_header(sys_card, "SYSTEM STATUS")
        self.cpu_sparkline = SparklineWidget(max_samples=24, color=PALETTE.accent_cyan, parent=sys_card)
        s_hdr.addWidget(self.cpu_sparkline)
        sl.addLayout(s_hdr)

        # UPTIME line
        up_row = QHBoxLayout()
        up_k = QLabel("UPTIME", sys_card)
        up_k.setStyleSheet(f"color: {PALETTE.text_secondary}; font-size: 9.5px; font-weight: 700; background: transparent;")
        self.uptime_lbl = QLabel("00:00:00", sys_card)
        self.uptime_lbl.setStyleSheet(
            f"color: {PALETTE.text_primary}; font-family: {METRICS.font_family_mono}; "
            "font-size: 10px; font-weight: 700; background: transparent;"
        )
        up_row.addWidget(up_k)
        up_row.addStretch(1)
        up_row.addWidget(self.uptime_lbl)
        sl.addLayout(up_row)

        self.cpu_bar, self.cpu_val_lbl = self._build_meter_row(sl, "CPU", PALETTE.accent_cyan, sys_card)
        self.ram_bar, self.ram_val_lbl = self._build_meter_row(sl, "RAM", PALETTE.accent_cyan, sys_card)
        self.disk_bar, self.disk_val_lbl = self._build_meter_row(sl, "DISK", PALETTE.warning, sys_card)
        self.bat_bar, self.bat_val_lbl = self._build_meter_row(sl, "BATTERY", PALETTE.success, sys_card)

        # Network speeds row (▲ upload / ▼ download)
        net_row = QHBoxLayout()
        net_row.setSpacing(6)
        self.net_up_lbl = QLabel("↑ 0.0 KB/s", sys_card)
        self.net_up_lbl.setStyleSheet(
            f"color: #F6D05C; font-family: {METRICS.font_family_mono}; font-size: 9.5px; font-weight: 600; background: transparent;"
        )
        self.net_down_lbl = QLabel("↓ 0.0 KB/s", sys_card)
        self.net_down_lbl.setStyleSheet(
            f"color: {PALETTE.success}; font-family: {METRICS.font_family_mono}; font-size: 9.5px; font-weight: 600; background: transparent;"
        )
        net_row.addWidget(self.net_up_lbl)
        net_row.addStretch(1)
        net_row.addWidget(self.net_down_lbl)
        sl.addLayout(net_row)

        # >90% RAM proactive guard banner (visible only above 90%)
        self.memory_guard_banner = QFrame(sys_card)
        self.memory_guard_banner.setObjectName("MemoryGuardBanner")
        self.memory_guard_banner.setStyleSheet(
            f"QFrame#MemoryGuardBanner {{ background-color: {PALETTE.danger_soft}; "
            f"border: 1px solid {PALETTE.danger}; border-radius: 4px; }}"
        )
        mg_lay = QHBoxLayout(self.memory_guard_banner)
        mg_lay.setContentsMargins(7, 3, 7, 3)
        mg_lay.setSpacing(6)
        self.memory_guard_lbl = QLabel("Memory >90%", self.memory_guard_banner)
        self.memory_guard_lbl.setStyleSheet(
            f"color: {PALETTE.text_primary}; font-size: 9.5px; font-weight: 700; background: transparent;"
        )
        self.free_memory_btn = QPushButton("Free memory", self.memory_guard_banner)
        self.free_memory_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.free_memory_btn.setStyleSheet(
            f"QPushButton {{ color: #020810; background-color: {PALETTE.warning}; "
            "border: none; border-radius: 3px; padding: 2px 7px; font-size: 9.5px; font-weight: 700; }}"
            "QPushButton:hover { background-color: #FCD34D; }"
        )
        self.free_memory_btn.clicked.connect(self._handle_free_memory_click)
        mg_lay.addWidget(self.memory_guard_lbl, 1)
        mg_lay.addWidget(self.free_memory_btn)
        self.memory_guard_banner.setVisible(False)
        sl.addWidget(self.memory_guard_banner)
        root.addWidget(sys_card)

        # ── 4) HEALTH SUMMARY ──────────────────────────────────────────────────
        health_card = self._make_card("HealthSummaryCard")
        self._section_cards["health"] = health_card
        hl = QVBoxLayout(health_card)
        hl.setContentsMargins(12, 8, 12, 10)
        hl.setSpacing(4)

        h_hdr, _ = self._section_header(health_card, "HEALTH SUMMARY")
        hl.addLayout(h_hdr)

        self.health_body_lbl = QLabel("• Scanning telemetry...", health_card)
        self.health_body_lbl.setWordWrap(True)
        self.health_body_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.health_body_lbl.setMinimumHeight(68)
        self.health_body_lbl.setStyleSheet(
            f"color: {PALETTE.text_secondary}; font-size: 9.5px; line-height: 1.25; background: transparent;"
        )
        hl.addWidget(self.health_body_lbl)
        root.addWidget(health_card)

        # ── 5) CAMERA VISION ───────────────────────────────────────────────────
        self.camera_panel = CameraPreviewPanel(on_action=on_camera_action, parent=self)
        self._section_cards["camera"] = self.camera_panel
        root.addWidget(self.camera_panel)

        root.addStretch(1)
        self._scroll.setWidget(container)
        outer.addWidget(self._scroll)

    def _build_meter_row(
        self,
        parent_layout: QVBoxLayout,
        label: str,
        color: str,
        parent_widget: QWidget,
    ) -> tuple[_HudProgressBar, QLabel]:
        row = QHBoxLayout()
        row.setSpacing(6)
        k_lbl = QLabel(label, parent_widget)
        k_lbl.setFixedWidth(48)
        k_lbl.setStyleSheet(
            f"color: {PALETTE.text_secondary}; font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;"
        )
        bar = _HudProgressBar(color, parent_widget)
        v_lbl = QLabel("0%", parent_widget)
        v_lbl.setFixedWidth(36)
        v_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        v_lbl.setStyleSheet(
            f"color: {PALETTE.text_primary}; font-family: {METRICS.font_family_mono}; "
            "font-size: 9.5px; font-weight: 700; background: transparent;"
        )
        row.addWidget(k_lbl)
        row.addWidget(bar, 1)
        row.addWidget(v_lbl)
        parent_layout.addLayout(row)
        return bar, v_lbl

    def start_timers(self) -> None:
        if not self._tick_timer.isActive():
            self._tick_timer.start()
        if not self._stat_timer.isActive():
            self._stat_timer.start()

    def stop_timers(self) -> None:
        if self._tick_timer.isActive():
            self._tick_timer.stop()
        if self._stat_timer.isActive():
            self._stat_timer.stop()

    def _on_second_tick(self) -> None:
        # 1) Location Clock + Analog Dial
        loc_city = "LOCAL"
        try:
            from actions.weather import get_configured_location, get_location_datetime

            loc = get_configured_location()
            loc_city = str(loc.get("city") or "LOCAL").strip().upper()
            now_dt = get_location_datetime(loc)
        except Exception:
            now_dt = dt.datetime.now()

        self.time_title_lbl.setText(f"TIME  •  {loc_city}")
        self.analog_dial.set_datetime(now_dt)
        self.clock_lbl.setText(now_dt.strftime("%H:%M"))
        self.clock_sec_lbl.setText(now_dt.strftime(":%S"))
        self.date_lbl.setText(now_dt.strftime("%d %B %Y").upper())
        self.day_lbl.setText(now_dt.strftime("%A").upper())

        # 2) Uptime + CPU + RAM + Net speeds
        try:
            uptime_sec = max(0, int(time.time() - psutil.boot_time()))
            hrs, rem = divmod(uptime_sec, 3600)
            mins, secs = divmod(rem, 60)
            self.uptime_lbl.setText(f"{hrs:02d}:{mins:02d}:{secs:02d}")
        except Exception:
            pass

        try:
            cpu = float(psutil.cpu_percent(interval=None))
            ram_pct = float(self._ram_override_pct) if self._ram_override_pct is not None else float(psutil.virtual_memory().percent)
        except Exception:
            cpu, ram_pct = 0.0, 0.0

        self._last_metrics["cpu"] = cpu
        self._last_metrics["ram"] = ram_pct
        self.cpu_sparkline.push_sample(cpu)

        c_cpu = _bar_color_for_pct(cpu, "load")
        self.cpu_bar.set_value(cpu, c_cpu)
        self.cpu_val_lbl.setText(f"{int(round(cpu))}%")
        self.cpu_val_lbl.setStyleSheet(
            f"color: {c_cpu}; font-family: {METRICS.font_family_mono}; font-size: 9.5px; font-weight: 700; background: transparent;"
        )

        c_ram = _bar_color_for_pct(ram_pct, "load")
        self.ram_bar.set_value(ram_pct, c_ram)
        self.ram_val_lbl.setText(f"{int(round(ram_pct))}%")
        self.ram_val_lbl.setStyleSheet(
            f"color: {c_ram}; font-family: {METRICS.font_family_mono}; font-size: 9.5px; font-weight: 700; background: transparent;"
        )

        self.memory_guard_banner.setVisible(ram_pct > 90.0)
        if ram_pct > 90.0:
            self.memory_guard_lbl.setText(f"RAM {ram_pct:.0f}% > 90%")

        # Live Network Up / Down delta
        try:
            now_net = time.perf_counter()
            cur_io = psutil.net_io_counters()
            if self._last_net_counters is not None and (now_net - self._last_net_ts) > 0.2:
                dt_s = now_net - self._last_net_ts
                up_bps = max(0.0, (cur_io.bytes_sent - self._last_net_counters.bytes_sent) / dt_s)
                down_bps = max(0.0, (cur_io.bytes_recv - self._last_net_counters.bytes_recv) / dt_s)
                self._last_metrics["net_up"] = up_bps
                self._last_metrics["net_down"] = down_bps
                self.net_up_lbl.setText(f"↑ {_format_net_rate(up_bps)}")
                self.net_down_lbl.setText(f"↓ {_format_net_rate(down_bps)}")
            self._last_net_counters = cur_io
            self._last_net_ts = now_net
        except Exception:
            pass

    def _refresh_slow_vitals(self) -> None:
        try:
            disk = float(psutil.disk_usage("/").percent)
            c_disk = _bar_color_for_pct(disk, "load")
            self._last_metrics["disk"] = disk
            self.disk_bar.set_value(disk, c_disk)
            self.disk_val_lbl.setText(f"{int(round(disk))}%")
            self.disk_val_lbl.setStyleSheet(
                f"color: {c_disk}; font-family: {METRICS.font_family_mono}; font-size: 9.5px; font-weight: 700; background: transparent;"
            )
        except Exception:
            pass

        try:
            bat = psutil.sensors_battery()
            if bat is not None:
                bp = float(bat.percent)
                c_bat = _bar_color_for_pct(bp, "battery")
                self._last_metrics["bat"] = bp
                self.bat_bar.set_value(bp, c_bat)
                self.bat_val_lbl.setText(f"{int(round(bp))}%")
                self.bat_val_lbl.setStyleSheet(
                    f"color: {c_bat}; font-family: {METRICS.font_family_mono}; font-size: 9.5px; font-weight: 700; background: transparent;"
                )
            else:
                self.bat_bar.set_value(100.0, PALETTE.accent_cyan)
                self.bat_val_lbl.setText("AC")
        except Exception:
            pass

        self.refresh_health_async()

    def refresh_weather_async(self) -> None:
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
        city = str(details.get("city") or "LOCAL").upper()
        self.weather_title_lbl.setText(f"WEATHER  •  {city}")
        self.weather_city_lbl.setText(city)

        icon_kind = str(details.get("icon") or "partly_cloudy")
        self.weather_icon.set_kind(icon_kind)

        temp_c = details.get("temp_c")
        if temp_c is not None:
            self.weather_temp_lbl.setText(f"{temp_c}°C")
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
            self.weather_meta_lbl.setText("\n".join(lines) or "• Live telemetry active")
        else:
            raw = str(details.get("summary") or "Weather unavailable")
            cleaned = raw.split(":", 1)[-1].strip().rstrip(".")
            parts = [p.strip() for p in cleaned.split(",") if p.strip()]
            if parts and "°" in parts[0]:
                self.weather_temp_lbl.setText(parts[0])
                rest = parts[1:4]
            else:
                self.weather_temp_lbl.setText("--°C")
                rest = parts[:3]
            self.weather_meta_lbl.setText("\n".join(f"• {p}" for p in rest) if rest else cleaned[:64])

    def refresh_health_async(self) -> None:
        if self._health_inflight:
            return
        self._health_inflight = True

        def _worker() -> None:
            lines: list[str] = []
            try:
                from actions.health import get_health_card_lines

                lines = list(get_health_card_lines() or [])
            except Exception as exc:
                lines = [f"Telemetry check unavailable ({exc})"]
            finally:
                self._health_inflight = False
            self._bridge.health_ready.emit(lines)

        threading.Thread(target=_worker, daemon=True, name="rose-ui-health").start()

    def _apply_health_lines(self, lines: list[str]) -> None:
        clean = [f"• {str(item).strip()}" for item in (lines or []) if str(item).strip()][:5]
        if clean:
            self.health_body_lbl.setText("\n".join(clean))
            self.health_body_lbl.setMinimumHeight(max(68, len(clean) * 14 + 4))
        else:
            self.health_body_lbl.setText("• Systems nominal")
            self.health_body_lbl.setMinimumHeight(24)

    def _handle_free_memory_click(self) -> None:
        self.free_memory_btn.setEnabled(False)
        self.free_memory_btn.setText("Freeing...")

        def _worker() -> None:
            _freed, note = trim_memory_safely()
            self._bridge.memory_freed.emit(note)

        threading.Thread(target=_worker, daemon=True, name="rose-trim-mem").start()

    def _on_memory_freed_done(self, note: str) -> None:
        self.free_memory_btn.setEnabled(True)
        self.free_memory_btn.setText("Free memory")
        self._on_second_tick()
        if self._on_free_memory_note:
            self._on_free_memory_note(note)

    def trigger_free_memory(self) -> None:
        self._handle_free_memory_click()

    def set_ram_override_for_test(self, ram_pct: Optional[float]) -> None:
        self._ram_override_pct = ram_pct
        self._on_second_tick()

    def get_latest_metrics(self) -> dict[str, Any]:
        return dict(self._last_metrics)

    def update_camera_preview(self, preview_path: str = "", payload: dict | None = None) -> None:
        self.camera_panel.update_from_capture(preview_path, payload or {})

    def focus_section(self, section: str, duration_ms: int = 4200) -> None:
        key = str(section or "").strip().lower()
        target = self._section_cards.get(key)
        self._clear_section_focus()
        if target is None:
            return
        target.set_hud_focus(1.0)
        self._focus_clear_timer.start(max(500, int(duration_ms)))

    def _clear_section_focus(self) -> None:
        for card in self._section_cards.values():
            card.set_hud_focus(0.0)
