"""R.O.S.E MIRROR Skin Renderer — Pre-rendered additive sprites, region scaler, and dirty-rect painters.

Renders live dynamic values, additive glow overlays, 64-pt audio waveform, rotating holographic
rings, floating particles (<= 40), and Ctrl+Shift+R debug boxes strictly atop clean_plate.png.
NEVER imports cv2 and NEVER uses QGraphicsEffect.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import random
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)

from ui.theme import METRICS, PALETTE

REF_WIDTH = 1672
REF_HEIGHT = 941
REGIONS_FILE = Path(__file__).resolve().parent.parent / "ui_regions.json"
MAX_PARTICLES = 38


class SkinRegionMap:
    """Loads and scales ui_regions.json rectangles cleanly from 1672x941 to current canvas."""

    def __init__(self, path: Path = REGIONS_FILE):
        self._path = path
        self.data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> dict[str, Any]:
        with self._path.open("r", encoding="utf-8") as f:
            self.data = json.load(f)
        return self.data

    def rectf(self, section: str, key: str, canvas: QRect, *, sub_key: str | None = None) -> QRectF:
        sec = self.data.get(section, {})
        raw = sec.get(key)
        if sub_key is not None and isinstance(raw, dict):
            raw = raw.get(sub_key)
        if not raw or len(raw) != 4:
            return QRectF()
        return self.scale_rectf(raw, canvas)

    @staticmethod
    def scale_rectf(raw: list[float], canvas: QRect) -> QRectF:
        sx = canvas.width() / float(REF_WIDTH)
        sy = canvas.height() / float(REF_HEIGHT)
        return QRectF(
            canvas.x() + raw[0] * sx,
            canvas.y() + raw[1] * sy,
            raw[2] * sx,
            raw[3] * sy,
        )

    def rect(self, section: str, key: str, canvas: QRect, *, sub_key: str | None = None, pad: int = 0) -> QRect:
        r = self.rectf(section, key, canvas, sub_key=sub_key).toAlignedRect()
        if pad:
            r = r.adjusted(-pad, -pad, pad, pad)
        return r

    def point(self, section: str, key: str, canvas: QRect) -> QPointF:
        raw = self.data.get(section, {}).get(key, [0, 0])
        sx = canvas.width() / float(REF_WIDTH)
        sy = canvas.height() / float(REF_HEIGHT)
        return QPointF(canvas.x() + raw[0] * sx, canvas.y() + raw[1] * sy)

    def scale_scalar(self, val: float, canvas: QRect) -> float:
        return val * (canvas.height() / float(REF_HEIGHT))

    def debug_entries(self, canvas: QRect) -> list[tuple[str, QColor, QRectF]]:
        section_colors = {
            "window": QColor("#FACC15"),
            "header": QColor("#22D3EE"),
            "time_panel": QColor("#FB923C"),
            "weather_panel": QColor("#4ADE80"),
            "system_status": QColor("#F472B6"),
            "health_summary": QColor("#60A5FA"),
            "camera_vision": QColor("#F87171"),
            "center": QColor("#C084FC"),
            "dock": QColor("#34D399"),
            "conversation": QColor("#FDE047"),
        }
        entries: list[tuple[str, QColor, QRectF]] = []
        for sec, col in section_colors.items():
            val = self.data.get(sec, {})
            if not isinstance(val, dict):
                continue
            for k, v in val.items():
                if isinstance(v, list) and len(v) == 4 and all(isinstance(n, (int, float)) for n in v):
                    entries.append((f"{sec}.{k}", col, self.scale_rectf(v, canvas)))
                elif isinstance(v, dict):
                    for bk, bv in v.items():
                        if isinstance(bv, list) and len(bv) == 4:
                            entries.append((f"{sec}.{bk}", col, self.scale_rectf(bv, canvas)))
                elif k == "lines" and isinstance(v, list):
                    for idx, lv in enumerate(v):
                        if isinstance(lv, list) and len(lv) == 4:
                            entries.append((f"health.l{idx+1}", col, self.scale_rectf(lv, canvas)))
        return entries


STATE_STYLE_MAP: dict[str, dict[str, Any]] = {
    "IDLE": {"color": "#26E6FF", "dot": "#26E6FF", "label": "Idle", "ring_speed": 10.0, "glow": 0.42},
    "LISTENING": {"color": "#2AF2FF", "dot": "#2AF2FF", "label": "Listening...", "ring_speed": 22.0, "glow": 0.72},
    "THINKING": {"color": "#A78BFA", "dot": "#C084FC", "label": "Thinking...", "ring_speed": 68.0, "glow": 0.85},
    "SPEAKING": {"color": "#44F8FF", "dot": "#44F8FF", "label": "Speaking...", "ring_speed": 34.0, "glow": 0.92},
    "MUTED": {"color": "#7BA3BD", "dot": "#F59E0B", "label": "Mic Muted", "ring_speed": 4.0, "glow": 0.18},
    "PAUSED": {"color": "#F59E0B", "dot": "#F59E0B", "label": "Paused", "ring_speed": 4.0, "glow": 0.24},
    "ERROR": {"color": "#F59E0B", "dot": "#FB7185", "label": "Warning", "ring_speed": 18.0, "glow": 0.65},
    "CALL": {"color": "#34D399", "dot": "#34D399", "label": "Connected", "ring_speed": 28.0, "glow": 0.88},
}


class SpriteCache:
    """Pre-rendered offscreen additive sprites (zero per-frame gradient allocation, zero QGraphicsEffect)."""

    def __init__(self) -> None:
        self._ring_size = 0
        self._dashed_ring: Optional[QPixmap] = None
        self._thinking_arcs: Optional[QPixmap] = None
        self._pedestal_size = (0, 0)
        self._pedestal_glow: Optional[QPixmap] = None
        self._glow_size = 0
        self._globe_glows: dict[str, QPixmap] = {}
        self._btn_glows: dict[tuple[str, int, int], QPixmap] = {}

    def globe_glow(self, state: str, diameter: int) -> QPixmap:
        d = max(64, int(diameter))
        if abs(d - self._glow_size) > 4:
            self._glow_size = d
            self._globe_glows.clear()
        key = state if state in STATE_STYLE_MAP else "IDLE"
        cached = self._globe_glows.get(key)
        if cached is not None:
            return cached

        pix = QPixmap(d, d)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        c = QColor(STATE_STYLE_MAP[key]["color"])
        center = QPointF(d * 0.5, d * 0.5)

        rg = QRadialGradient(center, d * 0.5)
        c_core = QColor(c)
        c_core.setAlpha(155)
        c_mid = QColor(c)
        c_mid.setAlpha(68)
        rg.setColorAt(0.0, QColor(235, 252, 255, 110))
        rg.setColorAt(0.32, c_core)
        rg.setColorAt(0.68, c_mid)
        rg.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, d, d, rg)
        p.end()
        self._globe_glows[key] = pix
        return pix

    def dashed_ring(self, diameter: int) -> QPixmap:
        d = max(96, int(diameter))
        if abs(d - self._ring_size) > 4 or self._dashed_ring is None:
            self._ring_size = d
            self._dashed_ring = self._build_dashed_ring(d)
            self._thinking_arcs = self._build_thinking_arcs(d)
        return self._dashed_ring

    def thinking_arcs(self, diameter: int) -> QPixmap:
        self.dashed_ring(diameter)
        assert self._thinking_arcs is not None
        return self._thinking_arcs

    @staticmethod
    def _build_dashed_ring(d: int) -> QPixmap:
        pix = QPixmap(d, d)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx, cy = d * 0.5, d * 0.5
        r_out = d * 0.445
        r_in = d * 0.395

        pen_outer = QPen(QColor(80, 240, 255, 165), max(1.6, d * 0.0055))
        pen_outer.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen_outer.setDashPattern([8.0, 5.0, 3.0, 5.0])
        p.setPen(pen_outer)
        p.drawEllipse(QPointF(cx, cy), r_out, r_out)

        pen_inner = QPen(QColor(110, 245, 255, 120), max(1.2, d * 0.0038))
        pen_inner.setDashPattern([14.0, 10.0])
        p.setPen(pen_inner)
        p.drawEllipse(QPointF(cx, cy), r_in, r_in)

        # Cardinal bright micro ticks
        p.setPen(QPen(QColor(180, 250, 255, 200), max(1.8, d * 0.005)))
        for idx in range(12):
            ang = math.radians(idx * 30.0)
            p.drawLine(
                QPointF(cx + math.cos(ang) * (r_out - d * 0.016), cy + math.sin(ang) * (r_out - d * 0.016)),
                QPointF(cx + math.cos(ang) * (r_out + d * 0.012), cy + math.sin(ang) * (r_out + d * 0.012)),
            )
        p.end()
        return pix

    @staticmethod
    def _build_thinking_arcs(d: int) -> QPixmap:
        pix = QPixmap(d, d)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx, cy = d * 0.5, d * 0.5
        r1 = d * 0.415
        r2 = d * 0.355
        p.setBrush(Qt.BrushStyle.NoBrush)

        pen_v1 = QPen(QColor(178, 118, 255, 215), max(2.2, d * 0.007), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen_v1)
        box1 = QRectF(cx - r1, cy - r1, r1 * 2, r1 * 2)
        p.drawArc(box1, 15 * 16, 85 * 16)
        p.drawArc(box1, 195 * 16, 85 * 16)

        pen_v2 = QPen(QColor(88, 232, 255, 195), max(1.8, d * 0.005), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen_v2)
        box2 = QRectF(cx - r2, cy - r2, r2 * 2, r2 * 2)
        p.drawArc(box2, 110 * 16, 70 * 16)
        p.drawArc(box2, 290 * 16, 70 * 16)
        p.end()
        return pix

    def pedestal_glow(self, w: int, h: int) -> QPixmap:
        tw, th = max(32, int(w)), max(16, int(h))
        if abs(tw - self._pedestal_size[0]) > 4 or abs(th - self._pedestal_size[1]) > 2 or self._pedestal_glow is None:
            self._pedestal_size = (tw, th)
            pix = QPixmap(tw, th)
            pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rg = QRadialGradient(QPointF(tw * 0.5, th * 0.5), tw * 0.5, QPointF(tw * 0.5, th * 0.5))
            rg.setColorAt(0.0, QColor(150, 245, 255, 95))
            rg.setColorAt(0.55, QColor(38, 224, 255, 130))
            rg.setColorAt(0.82, QColor(20, 145, 255, 55))
            rg.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.save()
            p.translate(tw * 0.5, th * 0.5)
            p.scale(1.0, float(th) / float(tw))
            p.translate(-tw * 0.5, -tw * 0.5)
            p.fillRect(QRectF(0, 0, tw, tw), rg)
            p.restore()
            # Bright elliptical inner edge arc
            p.setPen(QPen(QColor(135, 246, 255, 190), max(1.6, th * 0.032)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(tw * 0.08, th * 0.14, tw * 0.84, th * 0.72))
            p.end()
            self._pedestal_glow = pix
        return self._pedestal_glow

    def button_glow(self, color_hex: str, w: int, h: int) -> QPixmap:
        tw, th = max(24, int(w)), max(16, int(h))
        key = (color_hex, tw, th)
        cached = self._btn_glows.get(key)
        if cached is not None:
            return cached
        if len(self._btn_glows) > 48:
            self._btn_glows.clear()
        pix = QPixmap(tw, th)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        col = QColor(color_hex)
        # Soft radial core bloom inside rounded chamfer rect
        fill_col = QColor(col)
        fill_col.setAlpha(46)
        p.setBrush(fill_col)
        border_col = QColor(col)
        border_col.setAlpha(210)
        p.setPen(QPen(border_col, max(1.6, th * 0.045)))
        p.drawRoundedRect(QRectF(2.0, 2.0, tw - 4.0, th - 4.0), 7.0, 7.0)
        outer_col = QColor(col)
        outer_col.setAlpha(95)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(outer_col, max(3.0, th * 0.085)))
        p.drawRoundedRect(QRectF(1.5, 1.5, tw - 3.0, th - 3.0), 8.0, 8.0)
        p.end()
        self._btn_glows[key] = pix
        return pix


def make_particle_pool(count: int = MAX_PARTICLES) -> list[list[float]]:
    """Allocate <= 40 lightweight particle slots: [nx, ny, vx, vy, r, alpha, phase]."""
    rng = random.Random(4207)
    n = min(40, max(12, int(count)))
    pool: list[list[float]] = []
    for _ in range(n):
        ang = rng.uniform(0.0, math.tau)
        dist = rng.uniform(0.14, 0.92)
        nx = 0.5 + math.cos(ang) * dist * 0.44
        ny = 0.5 + math.sin(ang) * dist * 0.44
        pool.append([
            nx,
            ny,
            rng.uniform(-0.025, 0.025),
            rng.uniform(-0.045, -0.012),
            rng.uniform(0.9, 2.1),
            rng.uniform(0.35, 0.95),
            rng.uniform(0.0, math.tau),
        ])
    return pool


def step_particles(pool: list[list[float]], dt_sec: float, speed_mult: float) -> None:
    """Advance particle positions in normalized [0..1] globe_box coordinates."""
    dt_c = min(0.08, max(0.001, dt_sec)) * speed_mult
    for pt in pool:
        pt[0] += pt[2] * dt_c
        pt[1] += pt[3] * dt_c
        pt[6] += dt_c * 2.2
        dx = pt[0] - 0.5
        dy = pt[1] - 0.5
        if dx * dx + dy * dy > 0.22 or pt[1] < 0.06:
            # Respawn near pedestal bottom/core cleanly inside globe_box
            pt[0] = 0.5 + math.sin(pt[6] * 3.7) * 0.28
            pt[1] = 0.72 + 0.14 * math.cos(pt[6] * 1.9)


def _scaled_font(family: str, px: float, bold: bool = False, letter_spacing: float = 0.0) -> QFont:
    f = QFont(family)
    f.setPixelSize(max(7, int(round(px))))
    f.setBold(bold)
    if letter_spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
    return f


def paint_header_section(
    p: QPainter,
    reg: SkinRegionMap,
    canvas: QRect,
    *,
    voice_label: str,
    state: str,
    call_active: bool,
    sys_mic_muted: bool,
) -> None:
    s = canvas.height() / float(REF_HEIGHT)
    dirty = p.clipBoundingRect() if p.hasClipping() else QRectF(canvas)

    vr = reg.rectf("header", "settings_voice_text", canvas)
    if dirty.intersects(vr):
        p.setFont(_scaled_font("Segoe UI", 11.0 * s, bold=False, letter_spacing=0.2 * s))
        p.setPen(QColor("#A9CEE8"))
        p.drawText(vr, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"VOICE CORE : {voice_label}")

    ind_r = reg.rectf("header", "online_indicator", canvas)
    if dirty.intersects(ind_r):
        if sys_mic_muted:
            dot_c, txt_c, txt = QColor("#F43F5E"), QColor("#FB7185"), "MIC OFF"
        elif call_active:
            dot_c, txt_c, txt = QColor("#34D399"), QColor("#34D399"), "IN CALL"
        elif state == "PAUSED":
            dot_c, txt_c, txt = QColor("#F59E0B"), QColor("#FBBF24"), "PAUSED"
        elif state == "MUTED":
            dot_c, txt_c, txt = QColor("#F59E0B"), QColor("#FBBF24"), "MUTED"
        elif state == "ERROR":
            dot_c, txt_c, txt = QColor("#EF4444"), QColor("#F87171"), "ERROR"
        else:
            dot_c, txt_c, txt = QColor("#22E55A"), QColor("#42EEFF"), "ONLINE"

        dr = reg.rectf("header", "online_dot", canvas)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        halo = QColor(dot_c)
        halo.setAlpha(72)
        p.setBrush(halo)
        p.drawEllipse(dr.adjusted(-1.5 * s, -1.5 * s, 1.5 * s, 1.5 * s))
        p.setBrush(dot_c)
        p.drawEllipse(dr.adjusted(1.5 * s, 1.5 * s, -1.5 * s, -1.5 * s))
        p.restore()

        tr = reg.rectf("header", "online_text", canvas)
        p.setFont(_scaled_font("Bahnschrift", 16.0 * s, bold=True, letter_spacing=0.9 * s))
        p.setPen(txt_c)
        p.drawText(tr.adjusted(0, 1.0 * s, 0, 1.0 * s), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)


def paint_time_section(
    p: QPainter,
    reg: SkinRegionMap,
    canvas: QRect,
    *,
    now_dt: dt.datetime,
    city_label: str,
) -> None:
    s = canvas.height() / float(REF_HEIGHT)
    dirty = p.clipBoundingRect() if p.hasClipping() else QRectF(canvas)
    card_r = reg.rectf("time_panel", "card", canvas)
    if not dirty.intersects(card_r):
        return

    hr = reg.rectf("time_panel", "header_city", canvas)
    if dirty.intersects(hr):
        p.setFont(_scaled_font("Bahnschrift", 13.8 * s, bold=True, letter_spacing=0.8 * s))
        p.setPen(QColor("#62ECFF"))
        p.drawText(hr.adjusted(0, 1.2 * s, 0, 1.2 * s), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"· {city_label.upper()}")

    dial_r = reg.rectf("time_panel", "clock_dial", canvas)
    if dirty.intersects(dial_r):
        cx, cy = dial_r.center().x(), dial_r.center().y() + 1.0 * s
        rad = min(dial_r.width(), dial_r.height()) * 0.27
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        sec = now_dt.second
        minute = now_dt.minute + sec / 60.0
        hour = (now_dt.hour % 12) + minute / 60.0
        ah = math.radians(hour * 30.0 - 90.0)
        am = math.radians(minute * 6.0 - 90.0)
        asec = math.radians(sec * 6.0 - 90.0)
        p.setPen(QPen(QColor("#E6FAFF"), max(1.6, 2.2 * s), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(ah) * rad * 0.62, cy + math.sin(ah) * rad * 0.62))
        p.setPen(QPen(QColor("#3AF0FF"), max(1.1, 1.6 * s), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(am) * rad * 0.88, cy + math.sin(am) * rad * 0.88))
        p.setPen(QPen(QColor("#9EF9FF"), max(0.7, 0.9 * s), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(asec) * rad * 0.94, cy + math.sin(asec) * rad * 0.94))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#46F0FF"))
        p.drawEllipse(QPointF(cx, cy), max(1.4, 2.0 * s), max(1.4, 2.0 * s))
        p.restore()

    dig_r = reg.rectf("time_panel", "clock_digits", canvas)
    if dirty.intersects(dig_r):
        p.setFont(_scaled_font("Bahnschrift", 38.0 * s, bold=True, letter_spacing=1.2 * s))
        p.setPen(QColor(38, 230, 255, 85))
        p.drawText(dig_r.adjusted(0, 0.0, 0, -1.0 * s), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, now_dt.strftime("%H:%M"))
        p.setPen(QColor("#3AF0FF"))
        p.drawText(dig_r.adjusted(0, -1.0 * s, 0, -2.0 * s), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, now_dt.strftime("%H:%M"))

    sec_r = reg.rectf("time_panel", "seconds_digits", canvas)
    if dirty.intersects(sec_r):
        p.setFont(_scaled_font("Bahnschrift", 19.5 * s, bold=True, letter_spacing=0.6 * s))
        p.setPen(QColor("#42D8F5"))
        p.drawText(sec_r, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, now_dt.strftime(":%S"))

    dt_r = reg.rectf("time_panel", "date_text", canvas)
    if dirty.intersects(dt_r):
        p.setFont(_scaled_font("Bahnschrift", 12.5 * s, bold=True, letter_spacing=0.5 * s))
        p.setPen(QColor("#F5C542"))
        p.drawText(dt_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, now_dt.strftime("%d %B %Y").upper())

    wd_r = reg.rectf("time_panel", "weekday_text", canvas)
    if dirty.intersects(wd_r):
        p.setFont(_scaled_font("Bahnschrift", 11.5 * s, bold=False, letter_spacing=0.6 * s))
        p.setPen(QColor("#E8F6FF"))
        p.drawText(wd_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, now_dt.strftime("%A").upper())


def _paint_weather_glyph(p: QPainter, r: QRectF, kind: str) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    cx, cy = r.center().x(), r.center().y()
    w, h = r.width(), r.height()
    k = str(kind or "partly_cloudy").lower()

    if k in ("clear", "partly_cloudy"):
        sx = cx if k == "clear" else cx - w * 0.16
        sy = cy if k == "clear" else cy - h * 0.12
        sr = min(w, h) * (0.24 if k == "clear" else 0.18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 204, 64, 52))
        p.drawEllipse(QPointF(sx, sy), sr * 1.6, sr * 1.6)
        p.setBrush(QColor("#FCCD3E"))
        p.drawEllipse(QPointF(sx, sy), sr, sr)
        p.setPen(QPen(QColor("#FDE047"), max(1.1, w * 0.025), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for idx in range(8):
            ang = math.radians(idx * 45.0)
            p.drawLine(
                QPointF(sx + math.cos(ang) * sr * 1.32, sy + math.sin(ang) * sr * 1.32),
                QPointF(sx + math.cos(ang) * sr * 1.72, sy + math.sin(ang) * sr * 1.72),
            )

    if k != "clear":
        # Stylized vector cloud matching reference icon
        c_col = QColor("#E2EEF8") if k in ("partly_cloudy", "cloudy") else QColor("#9BB8D4")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c_col)
        ox = cx + w * 0.05
        oy = cy + h * 0.16
        p.drawEllipse(QPointF(ox - w * 0.14, oy - h * 0.04), w * 0.15, h * 0.16)
        p.drawEllipse(QPointF(ox + w * 0.04, oy - h * 0.10), w * 0.19, h * 0.21)
        p.drawEllipse(QPointF(ox + w * 0.21, oy - h * 0.02), w * 0.13, h * 0.14)
        p.drawRoundedRect(QRectF(ox - w * 0.26, oy - h * 0.04, w * 0.56, h * 0.22), 6.0, 6.0)

        if k in ("rain", "storm"):
            p.setPen(QPen(QColor("#38BDF8"), max(1.3, w * 0.028), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for dx in (-0.14, 0.02, 0.18):
                x0 = ox + w * dx
                p.drawLine(QPointF(x0, oy + h * 0.22), QPointF(x0 - w * 0.05, oy + h * 0.36))
        if k == "storm":
            bolt = QPolygonF([
                QPointF(ox + w * 0.02, oy + h * 0.18),
                QPointF(ox - w * 0.06, oy + h * 0.30),
                QPointF(ox + w * 0.03, oy + h * 0.30),
                QPointF(ox - w * 0.04, oy + h * 0.42),
            ])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#FACC15"))
            p.drawPolygon(bolt)
        if k == "snow":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#EFF6FF"))
            for dx in (-0.14, 0.02, 0.18):
                p.drawEllipse(QPointF(ox + w * dx, oy + h * 0.29), 2.1, 2.1)
    p.restore()


def paint_weather_section(
    p: QPainter,
    reg: SkinRegionMap,
    canvas: QRect,
    *,
    city: str,
    temp_text: str,
    lines_text: str,
    icon_kind: str,
) -> None:
    s = canvas.height() / float(REF_HEIGHT)
    dirty = p.clipBoundingRect() if p.hasClipping() else QRectF(canvas)
    if not dirty.intersects(reg.rectf("weather_panel", "card", canvas)):
        return

    hc_r = reg.rectf("weather_panel", "header_city", canvas)
    if dirty.intersects(hc_r):
        p.setFont(_scaled_font("Bahnschrift", 13.4 * s, bold=True, letter_spacing=0.8 * s))
        p.setPen(QColor("#62ECFF"))
        p.drawText(hc_r.adjusted(0, 1.2 * s, 0, 1.2 * s), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"· {city.upper()}")

    ic_r = reg.rectf("weather_panel", "icon", canvas)
    if dirty.intersects(ic_r):
        _paint_weather_glyph(p, ic_r, icon_kind)

    tp_r = reg.rectf("weather_panel", "temp", canvas)
    if dirty.intersects(tp_r):
        p.setFont(_scaled_font("Bahnschrift", 34.0 * s, bold=True, letter_spacing=0.5 * s))
        p.setPen(QColor(38, 230, 255, 80))
        p.drawText(tp_r.adjusted(0, 1.0 * s, 0, 1.0 * s), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, temp_text)
        p.setPen(QColor("#3AF0FF"))
        p.drawText(tp_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, temp_text)

    cr_r = reg.rectf("weather_panel", "city_right", canvas)
    if dirty.intersects(cr_r):
        p.setFont(_scaled_font("Bahnschrift", 11.5 * s, bold=True, letter_spacing=0.6 * s))
        p.setPen(QColor("#52E0FF"))
        p.drawText(cr_r, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, city.upper())

    ln_r = reg.rectf("weather_panel", "lines", canvas)
    if dirty.intersects(ln_r):
        p.setFont(_scaled_font("Segoe UI", 11.2 * s, bold=False))
        p.setPen(QColor("#DCEDFF"))
        p.drawText(ln_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, lines_text)


def _sys_bar_color(pct: float, kind: str) -> QColor:
    if kind == "battery":
        if pct < 20.0:
            return QColor("#EF4444")
        if pct < 40.0:
            return QColor("#F59E0B")
        return QColor("#22E55A")
    if pct >= 85.0:
        return QColor("#EF4444")
    if pct >= 65.0:
        return QColor("#F59E0B")
    return QColor("#22E8FF")


def paint_system_status_section(
    p: QPainter,
    reg: SkinRegionMap,
    canvas: QRect,
    *,
    uptime_text: str,
    cpu_pct: float,
    ram_pct: float,
    disk_pct: float,
    bat_pct: float,
    bat_label: str,
    net_up_text: str,
    net_down_text: str,
) -> None:
    s = canvas.height() / float(REF_HEIGHT)
    dirty = p.clipBoundingRect() if p.hasClipping() else QRectF(canvas)
    if not dirty.intersects(reg.rectf("system_status", "card", canvas)):
        return

    up_r = reg.rectf("system_status", "uptime_value", canvas)
    if dirty.intersects(up_r):
        p.setFont(_scaled_font("Bahnschrift", 12.0 * s, bold=False, letter_spacing=0.4 * s))
        p.setPen(QColor("#7CE8FF"))
        p.drawText(up_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, uptime_text)

    bars = (
        ("cpu_bar", "cpu_pct", cpu_pct, f"{int(round(cpu_pct))}%", "load"),
        ("ram_bar", "ram_pct", ram_pct, f"{int(round(ram_pct))}%", "load"),
        ("disk_bar", "disk_pct", disk_pct, f"{int(round(disk_pct))}%", "load"),
        ("battery_bar", "battery_pct", bat_pct, bat_label, "battery"),
    )
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for bar_k, pct_k, pct_val, pct_str, kind in bars:
        br = reg.rectf("system_status", bar_k, canvas)
        pr = reg.rectf("system_status", pct_k, canvas)
        col = _sys_bar_color(pct_val, kind)
        if dirty.intersects(br):
            inner = br.adjusted(1.0 * s, 1.5 * s, -1.0 * s, -1.5 * s)
            frac = max(0.0, min(1.0, pct_val / 100.0))
            fw = inner.width() * frac
            if fw > 1.0:
                fill_r = QRectF(inner.left(), inner.top(), max(3.0 * s, fw), inner.height())
                p.setPen(Qt.PenStyle.NoPen)
                # Soft glow halo + bright core capsule
                glow_c = QColor(col)
                glow_c.setAlpha(75)
                p.setBrush(glow_c)
                p.drawRoundedRect(fill_r.adjusted(-1.0 * s, -1.0 * s, 1.5 * s, 1.0 * s), 3.0 * s, 3.0 * s)
                p.setBrush(col)
                p.drawRoundedRect(fill_r, 2.5 * s, 2.5 * s)
        if dirty.intersects(pr):
            p.setFont(_scaled_font("Bahnschrift", 12.0 * s, bold=True))
            p.setPen(col.lighter(115))
            p.drawText(pr, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, pct_str)
    p.restore()

    nu_r = reg.rectf("system_status", "net_up", canvas)
    if dirty.intersects(nu_r):
        p.setFont(_scaled_font("Bahnschrift", 12.0 * s, bold=True))
        p.setPen(QColor("#F5C542"))
        p.drawText(nu_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, net_up_text)

    nd_r = reg.rectf("system_status", "net_down", canvas)
    if dirty.intersects(nd_r):
        p.setFont(_scaled_font("Bahnschrift", 12.0 * s, bold=True))
        p.setPen(QColor("#34D399"))
        p.drawText(nd_r, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, net_down_text)


def paint_health_section(
    p: QPainter,
    reg: SkinRegionMap,
    canvas: QRect,
    *,
    lines: list[str],
) -> None:
    s = canvas.height() / float(REF_HEIGHT)
    dirty = p.clipBoundingRect() if p.hasClipping() else QRectF(canvas)
    all_r = reg.rectf("health_summary", "all_lines", canvas)
    if not dirty.intersects(all_r):
        return
    row_rects = reg.data.get("health_summary", {}).get("lines", [])
    p.setFont(_scaled_font("Segoe UI", 11.4 * s, bold=False))
    p.setPen(QColor("#D2ECFF"))
    for idx, raw_rect in enumerate(row_rects):
        if idx >= len(lines):
            break
        rr = reg.scale_rectf(raw_rect, canvas)
        if dirty.intersects(rr):
            txt = str(lines[idx]).strip()
            if not txt.startswith("-"):
                txt = f"- {txt}"
            p.drawText(rr, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)


def paint_camera_section(
    p: QPainter,
    reg: SkinRegionMap,
    canvas: QRect,
    *,
    status_text: str,
    headline: str,
    subtext: str,
    thumb_pixmap: Optional[QPixmap],
) -> None:
    s = canvas.height() / float(REF_HEIGHT)
    dirty = p.clipBoundingRect() if p.hasClipping() else QRectF(canvas)
    if not dirty.intersects(reg.rectf("camera_vision", "card", canvas)):
        return

    sr = reg.rectf("camera_vision", "status", canvas)
    if dirty.intersects(sr):
        p.setFont(_scaled_font("Bahnschrift", 12.0 * s, bold=True, letter_spacing=0.6 * s))
        st_u = str(status_text or "IDLE").upper()
        col = QColor("#34D399") if st_u == "LIVE" else (QColor("#FACC15") if st_u in ("SCAN", "CAPTURE") else QColor("#74E8FF"))
        p.setPen(col)
        p.drawText(sr, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, st_u)

    tr = reg.rectf("camera_vision", "thumbnail", canvas)
    if thumb_pixmap is not None and not thumb_pixmap.isNull() and dirty.intersects(tr):
        inner = tr.adjusted(3.0 * s, 3.0 * s, -3.0 * s, -3.0 * s)
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(inner, 4.0 * s, 4.0 * s)
        p.setClipPath(clip)
        p.drawPixmap(inner.toRect(), thumb_pixmap)
        p.restore()

    txr = reg.rectf("camera_vision", "text", canvas)
    if dirty.intersects(txr):
        top_r = QRectF(txr.x(), txr.y(), txr.width(), txr.height() * 0.50)
        bot_r = QRectF(txr.x(), txr.y() + txr.height() * 0.46, txr.width(), txr.height() * 0.54)
        p.setFont(_scaled_font("Segoe UI", 11.2 * s, bold=True))
        p.setPen(QColor("#5EEBFF"))
        p.drawText(top_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, headline)
        if subtext:
            p.setFont(_scaled_font("Segoe UI", 10.2 * s, bold=False))
            p.setPen(QColor("#C6E2F7"))
            p.drawText(bot_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, subtext)


def paint_center_animations(
    p: QPainter,
    reg: SkinRegionMap,
    sprites: SpriteCache,
    canvas: QRect,
    *,
    state: str,
    call_active: bool,
    call_elapsed_sec: int,
    phase: float,
    ring_angle: float,
    think_angle: float,
    audio_level: float,
    mic_level: float,
    error_flash: float,
    particles: list[list[float]],
    wave_points: list[float],
) -> None:
    s = canvas.height() / float(REF_HEIGHT)
    dirty = p.clipBoundingRect() if p.hasClipping() else QRectF(canvas)

    eff_state = "CALL" if call_active and state in ("IDLE", "LISTENING") else state
    if eff_state not in STATE_STYLE_MAP:
        eff_state = "IDLE"
    prof = STATE_STYLE_MAP[eff_state]

    g_box = reg.rectf("center", "globe_box", canvas)
    w_band = reg.rectf("center", "waveform_band", canvas)
    ped_ring = reg.rectf("center", "pedestal_ring", canvas)
    ped_status = reg.rectf("center", "pedestal_status", canvas)

    gc = reg.point("center", "globe_center", canvas)
    r_inner = reg.scale_scalar(float(reg.data["center"].get("globe_inner_radius", 160)), canvas)
    r_ring = reg.scale_scalar(float(reg.data["center"].get("globe_ring_radius", 224)), canvas)

    # 1. Pedestal light-ring gentle pulse (additive)
    if dirty.intersects(ped_ring):
        ped_pix = sprites.pedestal_glow(int(ped_ring.width()), int(ped_ring.height()))
        pulse = 0.45 + 0.35 * (0.5 + 0.5 * math.sin(phase * 1.9)) + 0.25 * audio_level
        p.save()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.setOpacity(max(0.18, min(0.95, pulse)))
        p.drawPixmap(ped_ring.toRect(), ped_pix)
        p.restore()

    # 2. Globe glow, rotating dashed ring, thinking arcs, call ripples & <=40 particles (additive, clipped)
    if dirty.intersects(g_box):
        p.save()
        p.setClipRect(g_box)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

        # Soft breathing or amplitude-reactive globe glow sprite
        base_alpha = prof["glow"]
        if eff_state == "SPEAKING":
            glow_alpha = min(0.95, 0.48 + 0.50 * audio_level)
            scale_g = 1.0 + 0.14 * audio_level
        elif eff_state == "LISTENING":
            glow_alpha = min(0.88, 0.48 + 0.22 * math.sin(phase * 2.6) + 0.28 * mic_level)
            scale_g = 1.0 + 0.06 * math.sin(phase * 2.6) + 0.06 * mic_level
        elif eff_state == "IDLE":
            glow_alpha = 0.24 + 0.16 * (0.5 + 0.5 * math.sin(phase * 1.4))
            scale_g = 0.98 + 0.04 * math.sin(phase * 1.4)
        else:
            glow_alpha = base_alpha + 0.12 * math.sin(phase * 2.8)
            scale_g = 1.0 + 0.05 * audio_level

        # Keep cached sprite size constant across breathing/speech pulses; scale only the draw rect
        base_glow_diam = int(r_inner * 2.55)
        glow_sprite = sprites.globe_glow("ERROR" if error_flash > 0.05 else eff_state, base_glow_diam)
        draw_glow_d = base_glow_diam * scale_g
        p.setOpacity(max(0.10, min(0.95, glow_alpha + error_flash * 0.4)))
        p.drawPixmap(
            QRectF(gc.x() - draw_glow_d * 0.5, gc.y() - draw_glow_d * 0.5, draw_glow_d, draw_glow_d),
            glow_sprite,
            QRectF(0.0, 0.0, float(glow_sprite.width()), float(glow_sprite.height())),
        )

        # Slow rotating dashed ring sprite overlay
        ring_diam = int(r_ring * 2.15)
        ring_pix = sprites.dashed_ring(ring_diam)
        p.setOpacity(0.58 if eff_state not in ("MUTED", "PAUSED") else 0.22)
        p.save()
        p.translate(gc)
        p.rotate(ring_angle)
        p.drawPixmap(int(-ring_pix.width() * 0.5), int(-ring_pix.height() * 0.5), ring_pix)
        p.restore()

        # Thinking violet-tinted spinning arcs
        if eff_state == "THINKING":
            arc_pix = sprites.thinking_arcs(ring_diam)
            p.setOpacity(0.86)
            p.save()
            p.translate(gc)
            p.rotate(-think_angle)
            p.drawPixmap(int(-arc_pix.width() * 0.5), int(-arc_pix.height() * 0.5), arc_pix)
            p.restore()

        # Call mode expanding ripple rings
        if call_active:
            p.setBrush(Qt.BrushStyle.NoBrush)
            for idx in range(3):
                t_ring = ((phase * 0.38) + idx / 3.0) % 1.0
                rr = r_inner * (0.85 + t_ring * 0.85)
                alpha_r = int(200 * (1.0 - t_ring) * (1.0 - t_ring))
                if alpha_r > 4:
                    p.setOpacity(alpha_r / 255.0)
                    p.setPen(QPen(QColor("#34D399"), max(1.4, 2.2 * s)))
                    p.drawEllipse(gc, rr, rr)

        # Tiny floating atmospheric sparks (max ~38)
        p.setPen(Qt.PenStyle.NoPen)
        spark_col = QColor(prof["color"])
        for pt in particles:
            px = g_box.x() + pt[0] * g_box.width()
            py = g_box.y() + pt[1] * g_box.height()
            twinkle = 0.55 + 0.45 * math.sin(pt[6])
            a = int(170 * pt[5] * twinkle * (0.45 if eff_state == "IDLE" else 0.85))
            if a > 8:
                p.setOpacity(a / 255.0)
                p.setBrush(spark_col)
                pr = pt[4] * s
                p.drawEllipse(QPointF(px, py), pr, pr)

        p.restore()

    # 3. Live 64-pt Additive Waveform across waveform_band (dominates over baked one)
    if dirty.intersects(w_band) and len(wave_points) >= 16:
        p.save()
        p.setClipRect(w_band)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        n_pts = len(wave_points)
        mid_y = w_band.center().y()
        half_h = w_band.height() * 0.46
        x0 = w_band.left()
        step_x = w_band.width() / max(1, n_pts - 1)

        if error_flash > 0.05:
            beam_col = QColor("#F59E0B")
        elif eff_state == "THINKING":
            beam_col = QColor("#B794F4")
        elif eff_state == "CALL":
            beam_col = QColor("#34D399")
        else:
            beam_col = QColor("#4AF6FF")

        is_dim = eff_state in ("MUTED", "PAUSED")
        gain = 0.18 if is_dim else (0.38 if eff_state == "IDLE" else 1.0)

        # Vertical mirror equalizer needles + smooth double beam envelope
        top_poly = QPolygonF()
        bot_poly = QPolygonF()
        pen_needle = QPen(beam_col, max(1.4, 2.2 * s), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen_needle)
        for idx, amp in enumerate(wave_points):
            x = x0 + idx * step_x
            # Bell taper toward left/right edges matching reference equator flare
            t_norm = abs((idx / max(1, n_pts - 1)) * 2.0 - 1.0)
            bell = math.exp(-3.2 * t_norm * t_norm)
            h_px = max(1.0 * s, amp * gain * half_h * (0.28 + 0.72 * bell))
            if h_px > 2.0 * s:
                p.setOpacity(min(0.95, 0.35 + 0.65 * (h_px / max(1.0, half_h))))
                p.drawLine(QPointF(x, mid_y - h_px), QPointF(x, mid_y + h_px))
            top_poly.append(QPointF(x, mid_y - h_px * 0.62))
            bot_poly.append(QPointF(x, mid_y + h_px * 0.62))

        p.setOpacity(0.55 if not is_dim else 0.15)
        edge_grad = QLinearGradient(w_band.left(), mid_y, w_band.right(), mid_y)
        edge_grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        edge_grad.setColorAt(0.18, QColor(140, 245, 255, 190))
        edge_grad.setColorAt(0.50, QColor(215, 252, 255, 245))
        edge_grad.setColorAt(0.82, QColor(140, 245, 255, 190))
        edge_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(QPen(edge_grad, max(1.1, 1.5 * s)))
        p.drawPolyline(top_poly)
        p.drawPolyline(bot_poly)
        p.setOpacity(0.75 if not is_dim else 0.25)
        p.drawLine(QPointF(w_band.left(), mid_y), QPointF(w_band.right(), mid_y))
        p.restore()

    # 4. Pedestal Status label ("• Listening..." / "• Call 00:12" / state caption)
    if dirty.intersects(ped_status):
        if call_active:
            mm, ss = divmod(max(0, int(call_elapsed_sec)), 60)
            label = f"Call {mm:02d}:{ss:02d}"
            txt_col = QColor("#34D399")
        else:
            label = str(prof["label"])
            txt_col = QColor(prof["color"])

        f = _scaled_font("Bahnschrift", 15.0 * s, bold=True, letter_spacing=0.6 * s)
        p.setFont(f)
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance(label)
        dot_r = 4.2 * s
        gap = 7.0 * s
        total = dot_r * 2 + gap + tw
        sx = ped_status.center().x() - total * 0.5
        cy = ped_status.center().y()

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dot_c = QColor(prof["dot"] if not call_active else "#34D399")
        halo = QColor(dot_c)
        halo.setAlpha(95)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(sx + dot_r, cy), dot_r * 1.5, dot_r * 1.5)
        p.setBrush(dot_c)
        p.drawEllipse(QPointF(sx + dot_r, cy), dot_r, dot_r)
        p.setPen(txt_col)
        p.drawText(QRectF(sx + dot_r * 2 + gap, ped_status.top(), tw + 12.0 * s, ped_status.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        p.restore()


def paint_conversation_header(
    p: QPainter,
    reg: SkinRegionMap,
    canvas: QRect,
    *,
    state: str,
    call_active: bool,
    phase: float,
    level: float,
) -> None:
    s = canvas.height() / float(REF_HEIGHT)
    dirty = p.clipBoundingRect() if p.hasClipping() else QRectF(canvas)
    st_r = reg.rectf("conversation", "status", canvas)
    if dirty.intersects(st_r):
        eff = "LIVE CALL" if call_active else ("ONLINE" if state == "IDLE" else state.upper())
        col = QColor("#34D399") if call_active else (
            QColor("#F59E0B") if eff in ("PAUSED", "MUTED") else (
                QColor("#C084FC") if eff == "THINKING" else QColor("#46F0FF")
            )
        )
        p.setFont(_scaled_font("Bahnschrift", 14.2 * s, bold=True, letter_spacing=0.9 * s))
        p.setPen(col)
        p.drawText(st_r.adjusted(0, 1.5 * s, 0, 1.5 * s), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, eff)

    mw_r = reg.rectf("conversation", "mini_wave", canvas)
    if dirty.intersects(mw_r):
        # Soft additive pulse over the baked mini-wave icon during active states
        active = call_active or state in ("LISTENING", "SPEAKING", "THINKING")
        if active:
            p.save()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            cx = mw_r.left() + mw_r.width() * 0.16
            step = (mw_r.width() * 0.68) / 7.0
            mid = mw_r.center().y()
            weights = (0.35, 0.65, 0.95, 0.70, 1.0, 0.72, 0.42)
            p.setPen(QPen(QColor(80, 244, 255, 195), max(1.4, 2.0 * s), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i, w_val in enumerate(weights):
                x = cx + i * step
                amp = 0.30 + 0.70 * (0.5 + 0.5 * math.sin(phase * 5.2 + i * 0.85)) * max(0.25, level)
                bh = max(2.0 * s, mw_r.height() * 0.34 * w_val * amp)
                p.drawLine(QPointF(x, mid - bh), QPointF(x, mid + bh))
            p.restore()


def paint_debug_regions(p: QPainter, reg: SkinRegionMap, canvas: QRect) -> None:
    """Ctrl+Shift+R debug outline overlay drawing every ui_regions.json rectangle."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    font = _scaled_font("Cascadia Mono", 10.0, bold=True)
    p.setFont(font)
    for name, col, rf in reg.debug_entries(canvas):
        p.setPen(QPen(col, 1.25))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rf)
        tag = QRectF(rf.x() + 1, rf.y() + 1, min(145.0, max(54.0, len(name) * 6.4 + 6)), 13.0)
        p.fillRect(tag, QColor(4, 10, 22, 195))
        p.setPen(col)
        p.drawText(tag.adjusted(2, 0, -2, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
    p.restore()
