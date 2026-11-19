"""
R.O.S.E (Project Mirror) — MIRROR K2 Central Holographic Globe & Radar Spectrum Orb.

Pure Qt QPainter + cached QPixmap core/halo sprites (zero web views, zero Qt blur effects):
- Real-time rotating 3D projected dotted holographic Earth point cloud (precomputed geometry)
- Counter-rotating segmented cybernetic radar/scanning arcs and tick rings
- Signature horizontal symmetric electric-cyan audio waveform beam slicing the equator
- 250ms cubic state transitions + reactive ripples + adaptive FPS (24Hz idle / 60Hz active)
- Strict A4 perf budgets (< 4ms typical paint, p95 < 16ms)
"""

from __future__ import annotations

import collections
import math
import time
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui.theme import ORB_STATE_PROFILES, PALETTE

# Coarse 72x26 equirectangular continent mask (lon -180..+180 step 5°, lat +65..-60 step 5°)
# Gives an unmistakable holographic Earth point cloud with zero external image dependencies.
_WORLD_MASK = (
    "........................................................................",
    "...........####..####..........#########............########............",
    ".......##################.....##########......####################......",
    "......###################.....#########....##########################...",
    "......#################........######....##############################.",
    "........###############.........#####...################################",
    ".........############...........#######.##############################..",
    "..........#########............######################################...",
    "...........######.............#####################################.....",
    "............#####............####################################.......",
    ".............###.............#####..############################........",
    ".............##..#..........#####....#############.###########..........",
    "..............#.............#####.....##########....#########...........",
    "...............###..........#########...######........#####.............",
    "................######......###########..###...........###..............",
    "................########....###########...................#.............",
    "................#########....#########..................................",
    ".................########....########.......................#####.......",
    ".................#######......#######......................#######......",
    "..................######......######.......................#######......",
    "..................#####........####.........................#####.......",
    "...................###..........##......................................",
    "...................##...................................................",
    "........................................................................",
)


def _build_globe_points() -> tuple[tuple[float, float, float, int], ...]:
    """Precompute unit-sphere 3D coordinates (x0, y0, z0, kind) for landmass & grid dots."""
    pts: list[tuple[float, float, float, int]] = []
    rows = len(_WORLD_MASK)
    cols = len(_WORLD_MASK[0])

    for r, line in enumerate(reversed(_WORLD_MASK)):
        # Map row r from -62° to +68°
        lat_deg = -62.0 + (r / max(1, rows - 1)) * 130.0
        lat = math.radians(lat_deg)
        c_lat = math.cos(lat)
        s_lat = math.sin(lat)
        for c, ch in enumerate(line):
            lon_deg = -180.0 + (c / cols) * 360.0
            lon = math.radians(lon_deg)
            is_land = ch == "#"
            if is_land:
                # Subtle organic scatter via deterministic hash offset
                jx = ((r * 37 + c * 17) % 11 - 5) * 0.008
                jy = ((r * 23 + c * 31) % 11 - 5) * 0.008
                x0 = c_lat * math.cos(lon) + jx
                y0 = s_lat + jy
                z0 = c_lat * math.sin(lon)
                norm = math.sqrt(x0 * x0 + y0 * y0 + z0 * z0) or 1.0
                pts.append((x0 / norm, y0 / norm, z0 / norm, 1))
            elif (r % 4 == 0 and c % 3 == 0):
                # Faint meridian/parallel holographic lattice dot
                x0 = c_lat * math.cos(lon)
                y0 = s_lat
                z0 = c_lat * math.sin(lon)
                pts.append((x0, y0, z0, 0))
    return tuple(pts)


_GLOBE_POINTS = _build_globe_points()
_RING_STEPS = 64
_UNIT_CIRCLE = tuple(
    (math.cos(2.0 * math.pi * i / _RING_STEPS), math.sin(2.0 * math.pi * i / _RING_STEPS))
    for i in range(_RING_STEPS)
)
_SPEC_BARS = 54


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_color(c1: QColor, c2: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(round(_lerp(c1.red(), c2.red(), t))),
        int(round(_lerp(c1.green(), c2.green(), t))),
        int(round(_lerp(c1.blue(), c2.blue(), t))),
        int(round(_lerp(c1.alpha(), c2.alpha(), t))),
    )


def _ease_out_cubic(t: float) -> float:
    u = 1.0 - max(0.0, min(1.0, t))
    return 1.0 - u * u * u


class OrbWidget(QWidget):
    """Hardware-lightweight 3D Holographic MIRROR Globe + Radar Rings + Equator Waveform."""

    _SPRITE_BASE_SIZE = 280
    _TRANSITION_SEC = 0.25

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(280, 260)

        self._state = "IDLE"
        self._call_active = False
        self._custom_caption = ""

        idle_p = ORB_STATE_PROFILES["IDLE"]
        self._cur_primary = QColor(idle_p["primary"])
        self._cur_secondary = QColor(idle_p["secondary"])
        self._cur_scale = float(idle_p["scale"])
        self._cur_spin_speed = float(idle_p["spin_speed"])
        self._cur_deform_gain = float(idle_p["deform_gain"])
        self._cur_breath_amp = float(idle_p["breath_amp"])

        self._from_primary = QColor(self._cur_primary)
        self._from_secondary = QColor(self._cur_secondary)
        self._from_scale = self._cur_scale
        self._from_spin_speed = self._cur_spin_speed
        self._from_deform_gain = self._cur_deform_gain
        self._from_breath_amp = self._cur_breath_amp

        self._to_primary = QColor(self._cur_primary)
        self._to_secondary = QColor(self._cur_secondary)
        self._to_scale = self._cur_scale
        self._to_spin_speed = self._cur_spin_speed
        self._to_deform_gain = self._cur_deform_gain
        self._to_breath_amp = self._cur_breath_amp

        self._transition_elapsed = self._TRANSITION_SEC

        self._phase = 0.0
        self._spin_angle = 0.0
        self._ring_angle_a = 0.0
        self._ring_angle_b = 0.0
        self._ripple_phase = 0.0

        self._mic_level = 0.0
        self._audio_level = 0.0
        self._amp_smooth = 0.0

        self._caption_text = idle_p["caption"]
        self._caption_color = QColor(PALETTE.text_primary)

        # Frame telemetry
        self._frame_times_ms: collections.deque[float] = collections.deque(maxlen=180)
        self._paint_count = 0

        # Pre-rendered core & halo pixmap sprites per state
        self._core_sprites: dict[str, QPixmap] = {}
        self._halo_sprites: dict[str, QPixmap] = {}
        self._build_sprite_cache()

    @property
    def state(self) -> str:
        return self._state

    @property
    def call_active(self) -> bool:
        return self._call_active

    def set_call_active(self, active: bool, caption: str = "") -> None:
        self._call_active = bool(active)
        self._custom_caption = str(caption or "")
        if self._call_active:
            self.set_state("CALL")
        elif self._state == "CALL":
            self.set_state("IDLE")
        self.update()

    def set_state(self, state: str) -> None:
        norm = str(state or "IDLE").strip().upper()
        if norm not in ORB_STATE_PROFILES:
            norm = "IDLE"
        if norm == self._state and not self._custom_caption:
            return
        self._state = norm
        profile = ORB_STATE_PROFILES[norm]

        self._from_primary = QColor(self._cur_primary)
        self._from_secondary = QColor(self._cur_secondary)
        self._from_scale = self._cur_scale
        self._from_spin_speed = self._cur_spin_speed
        self._from_deform_gain = self._cur_deform_gain
        self._from_breath_amp = self._cur_breath_amp

        self._to_primary = QColor(profile["primary"])
        self._to_secondary = QColor(profile["secondary"])
        self._to_scale = float(profile["scale"])
        self._to_spin_speed = float(profile["spin_speed"])
        self._to_deform_gain = float(profile["deform_gain"])
        self._to_breath_amp = float(profile["breath_amp"])

        self._transition_elapsed = 0.0
        self._caption_text = self._custom_caption or profile["caption"]
        self.update()

    def set_audio_level(self, level: float) -> None:
        self._audio_level = max(0.0, min(1.0, float(level)))

    def set_mic_level(self, level: float) -> None:
        self._mic_level = max(0.0, min(1.0, float(level)))

    def is_transitioning(self) -> bool:
        return self._transition_elapsed < self._TRANSITION_SEC

    def target_fps(self) -> int:
        if self.is_transitioning():
            return 60
        profile = ORB_STATE_PROFILES.get(self._state, ORB_STATE_PROFILES["IDLE"])
        return int(profile["fps"])

    def reset_perf_stats(self) -> None:
        self._frame_times_ms.clear()
        self._paint_count = 0

    def get_perf_stats(self) -> dict[str, float]:
        samples = sorted(self._frame_times_ms)
        if not samples:
            return {"avg_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "paints": float(self._paint_count)}
        avg = sum(samples) / len(samples)
        idx95 = min(len(samples) - 1, int(0.95 * (len(samples) - 1)))
        return {
            "avg_ms": round(avg, 2),
            "p95_ms": round(samples[idx95], 2),
            "max_ms": round(samples[-1], 2),
            "paints": float(self._paint_count),
        }

    def _build_sprite_cache(self) -> None:
        size = self._SPRITE_BASE_SIZE
        c = size * 0.5
        for state_name, prof in ORB_STATE_PROFILES.items():
            p_col = QColor(prof["primary"])
            s_col = QColor(prof["secondary"])

            # Soft atmospheric halo bloom pixmap
            halo = QPixmap(size, size)
            halo.fill(Qt.GlobalColor.transparent)
            hp = QPainter(halo)
            hp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            h_grad = QRadialGradient(QPointF(c, c), c * 0.96)
            outer_a = QColor(p_col)
            outer_a.setAlpha(72)
            mid_a = QColor(s_col)
            mid_a.setAlpha(38)
            h_grad.setColorAt(0.0, QColor(p_col.red(), p_col.green(), p_col.blue(), 110))
            h_grad.setColorAt(0.45, outer_a)
            h_grad.setColorAt(0.75, mid_a)
            h_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            hp.setPen(Qt.PenStyle.NoPen)
            hp.setBrush(h_grad)
            hp.drawEllipse(QPointF(c, c), c * 0.96, c * 0.96)
            hp.end()
            self._halo_sprites[state_name] = halo

            # Deep holographic globe atmosphere core pixmap (symmetrical limb glow)
            core = QPixmap(size, size)
            core.fill(Qt.GlobalColor.transparent)
            cp = QPainter(core)
            cp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            sphere_r = c * 0.68
            g = QRadialGradient(QPointF(c, c), sphere_r)
            g.setColorAt(0.0, QColor(28, 175, 240, 68))
            g.setColorAt(0.45, QColor(12, 85, 165, 118))
            g.setColorAt(0.78, QColor(p_col.red(), p_col.green(), p_col.blue(), 165))
            g.setColorAt(0.95, QColor(185, 250, 255, 225))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            cp.setPen(Qt.PenStyle.NoPen)
            cp.setBrush(g)
            cp.drawEllipse(QPointF(c, c), sphere_r, sphere_r)
            cp.end()
            self._core_sprites[state_name] = core

    def animate_step(self, dt_sec: float = 0.016) -> None:
        dt = max(0.001, min(0.1, float(dt_sec)))

        if self._transition_elapsed < self._TRANSITION_SEC:
            self._transition_elapsed = min(self._TRANSITION_SEC, self._transition_elapsed + dt)
            k = _ease_out_cubic(self._transition_elapsed / self._TRANSITION_SEC)
            self._cur_primary = _lerp_color(self._from_primary, self._to_primary, k)
            self._cur_secondary = _lerp_color(self._from_secondary, self._to_secondary, k)
            self._cur_scale = _lerp(self._from_scale, self._to_scale, k)
            self._cur_spin_speed = _lerp(self._from_spin_speed, self._to_spin_speed, k)
            self._cur_deform_gain = _lerp(self._from_deform_gain, self._to_deform_gain, k)
            self._cur_breath_amp = _lerp(self._from_breath_amp, self._to_breath_amp, k)

        raw_amp = max(self._audio_level, self._mic_level * 0.92)
        if self._state == "SPEAKING":
            raw_amp = max(raw_amp, 0.24 + 0.14 * abs(math.sin(self._phase * 2.2)))
        elif self._state == "LISTENING":
            raw_amp = max(raw_amp, 0.12 + 0.05 * math.sin(self._phase * 1.5))
        elif self._state == "THINKING":
            raw_amp = max(raw_amp, 0.16 + 0.07 * math.sin(self._phase * 2.6))

        alpha = 0.28 if raw_amp > self._amp_smooth else 0.14
        self._amp_smooth += (raw_amp - self._amp_smooth) * alpha

        self._phase += dt * 2.8
        self._spin_angle += dt * (0.38 * self._cur_spin_speed)
        self._ring_angle_a = (self._ring_angle_a + dt * 24.0 * max(0.35, self._cur_spin_speed)) % 360.0
        self._ring_angle_b = (self._ring_angle_b - dt * 16.0 * max(0.35, self._cur_spin_speed)) % 360.0

        prof = ORB_STATE_PROFILES.get(self._state, ORB_STATE_PROFILES["IDLE"])
        if prof["ripple"]:
            self._ripple_phase = (self._ripple_phase + dt * 0.85) % 1.0
        else:
            self._ripple_phase = 0.0

        self.update()

    def paintEvent(self, event) -> None:
        t0 = time.perf_counter()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        w = float(self.width())
        h = float(self.height())
        if w < 24 or h < 24:
            return

        cx = w * 0.5
        cy = h * 0.49
        min_dim = min(w, h)
        breath = 1.0 + math.sin(self._phase * 0.85) * self._cur_breath_amp + self._amp_smooth * 0.032
        base_r = min_dim * 0.255 * self._cur_scale * breath

        primary = self._cur_primary
        secondary = self._cur_secondary

        # 1) Downward holographic projection light beam toward pedestal
        beam_h = min(h - cy, base_r * 1.92)
        beam_poly = QPainterPath()
        beam_poly.moveTo(cx - base_r * 0.58, cy + base_r * 0.58)
        beam_poly.lineTo(cx + base_r * 0.58, cy + base_r * 0.58)
        beam_poly.lineTo(cx + base_r * 1.05, cy + beam_h)
        beam_poly.lineTo(cx - base_r * 1.05, cy + beam_h)
        beam_poly.closeSubpath()
        beam_grad = QLinearGradient(cx, cy + base_r * 0.55, cx, cy + beam_h)
        b_top = QColor(primary)
        b_top.setAlpha(int(68 + 45 * self._amp_smooth))
        beam_grad.setColorAt(0.0, b_top)
        beam_grad.setColorAt(0.65, QColor(secondary.red(), secondary.green(), secondary.blue(), 22))
        beam_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillPath(beam_poly, beam_grad)

        # 2) Cached halo + core sphere pixmaps (zero runtime blur)
        halo_pix = self._halo_sprites.get(self._state) or self._halo_sprites["IDLE"]
        halo_diam = base_r * 3.15
        painter.drawPixmap(
            QRectF(cx - halo_diam * 0.5, cy - halo_diam * 0.5, halo_diam, halo_diam),
            halo_pix,
            QRectF(halo_pix.rect()),
        )

        core_pix = self._core_sprites.get(self._state) or self._core_sprites["IDLE"]
        core_diam = base_r * 2.94  # matches sphere_r = 0.68 * c -> sphere diameter ~ base_r * 2.0
        painter.drawPixmap(
            QRectF(cx - core_diam * 0.5, cy - core_diam * 0.5, core_diam, core_diam),
            core_pix,
            QRectF(core_pix.rect()),
        )

        # 3) Outer & Inner Counter-Rotating Segmented MIRROR Radar Rings
        self._paint_radar_rings(painter, cx, cy, base_r, primary, secondary)

        # 4) Reactive ripple pulse when speaking / thinking / call
        prof = ORB_STATE_PROFILES.get(self._state, ORB_STATE_PROFILES["IDLE"])
        if prof["ripple"]:
            for offset in (0.0, 0.5):
                rp = (self._ripple_phase + offset) % 1.0
                rr = base_r * (1.06 + rp * 0.70)
                rc = QColor(primary)
                rc.setAlpha(int((1.0 - rp) * (90 + 75 * self._amp_smooth)))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(rc, 1.5))
                painter.drawEllipse(QPointF(cx, cy), rr, rr)

        # 5) Rotating 3D Projected Dotted Holographic Earth inside globe sphere
        self._paint_3d_globe(painter, cx, cy, base_r * 0.94, primary, secondary)

        # Crisp globe atmospheric rim
        rim_col = QColor(primary).lighter(128)
        rim_col.setAlpha(215)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(rim_col, 1.6))
        painter.drawEllipse(QPointF(cx, cy), base_r * 0.98, base_r * 0.98)

        # 6) Signature Horizontal Audio Spectrum Beam slicing across the equator
        self._paint_equator_waveform(painter, cx, cy, w, base_r, primary)

        t1 = time.perf_counter()
        self._paint_count += 1
        self._frame_times_ms.append((t1 - t0) * 1000.0)

    def _paint_radar_rings(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        r: float,
        primary: QColor,
        secondary: QColor,
    ) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Ring 1: Outer fine telemetry ring with tick notches
        r_outer = r * 1.44
        c_dim = QColor(primary)
        c_dim.setAlpha(85)
        painter.setPen(QPen(c_dim, 1.0))
        painter.drawEllipse(QPointF(cx, cy), r_outer, r_outer)

        # 36 calibrated tick marks around r_outer (precomputed trig LUT offset by spin)
        tick_pen = QPen(QColor(primary.red(), primary.green(), primary.blue(), 145), 1.15)
        painter.setPen(tick_pen)
        a0 = math.radians(self._ring_angle_a * 0.35)
        ca, sa = math.cos(a0), math.sin(a0)
        for idx in range(0, _RING_STEPS, 2):
            ux0, uy0 = _UNIT_CIRCLE[idx]
            ux = ux0 * ca - uy0 * sa
            uy = ux0 * sa + uy0 * ca
            tick_len = 7.0 if idx % 8 == 0 else 3.5
            painter.drawLine(
                QPointF(cx + ux * (r_outer - tick_len), cy + uy * (r_outer - tick_len)),
                QPointF(cx + ux * r_outer, cy + uy * r_outer),
            )

        # Ring 2: Rotating thick broken cyber arcs (clockwise)
        r_arc1 = r * 1.31
        rect1 = QRectF(cx - r_arc1, cy - r_arc1, r_arc1 * 2.0, r_arc1 * 2.0)
        c_arc1 = QColor(primary)
        c_arc1.setAlpha(195)
        painter.setPen(QPen(c_arc1, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        for start_deg, span_deg in ((18, 62), (112, 46), (198, 66), (294, 42)):
            painter.drawArc(rect1, int((start_deg - self._ring_angle_a) * 16), int(span_deg * 16))

        # Ring 3: Counter-rotating inner scanner segments (CCW)
        r_arc2 = r * 1.16
        rect2 = QRectF(cx - r_arc2, cy - r_arc2, r_arc2 * 2.0, r_arc2 * 2.0)
        c_arc2 = QColor(secondary).lighter(125)
        c_arc2.setAlpha(205)
        painter.setPen(QPen(c_arc2, 2.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        for start_deg, span_deg in ((5, 78), (130, 52), (225, 64), (312, 34)):
            painter.drawArc(rect2, int((start_deg - self._ring_angle_b) * 16), int(span_deg * 16))

        # Ring 4: Hairline orbital guide + cardinal crosshair flares
        r_inner = r * 1.05
        c_hair = QColor(200, 250, 255, 130)
        painter.setPen(QPen(c_hair, 1.0))
        painter.drawEllipse(QPointF(cx, cy), r_inner, r_inner)

        for deg_idx in (0, 16, 32, 48):
            ux, uy = _UNIT_CIRCLE[deg_idx]
            painter.drawLine(
                QPointF(cx + ux * (r_inner - 4), cy + uy * (r_inner - 4)),
                QPointF(cx + ux * (r_outer + 6), cy + uy * (r_outer + 6)),
            )

    def _paint_3d_globe(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        radius: float,
        primary: QColor,
        secondary: QColor,
    ) -> None:
        cyaw = math.cos(self._spin_angle)
        syaw = math.sin(self._spin_angle)
        # Subtle axial tilt 12 deg
        ctilt = 0.978
        stilt = 0.208

        back_col = QColor(secondary.red(), secondary.green(), secondary.blue(), 42)
        mid_col = QColor(primary.red(), primary.green(), primary.blue(), 155)
        front_col = QColor(215, 252, 255, 235)

        painter.setPen(Qt.PenStyle.NoPen)

        # Draw faint meridian ellipse guides
        guide_col = QColor(primary.red(), primary.green(), primary.blue(), 42)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(guide_col, 0.85))
        painter.drawEllipse(QPointF(cx, cy), radius * 0.45, radius)
        painter.drawEllipse(QPointF(cx, cy), radius * 0.82, radius)
        painter.drawEllipse(QPointF(cx, cy), radius, radius * 0.36)

        painter.setPen(Qt.PenStyle.NoPen)
        for x0, y0, z0, kind in _GLOBE_POINTS:
            # Rotate around Y axis (spin)
            xr = x0 * cyaw - z0 * syaw
            zr = x0 * syaw + z0 * cyaw
            # Slight X-axis tilt so northern hemisphere angles naturally
            yr = y0 * ctilt - zr * stilt
            zt = y0 * stilt + zr * ctilt

            if zt < -0.22 and kind == 0:
                continue

            px = cx + xr * radius
            py = cy - yr * radius

            if kind == 0:
                if zt > 0.15:
                    painter.setBrush(back_col)
                    painter.drawRect(QRectF(px - 0.8, py - 0.8, 1.6, 1.6))
                continue

            if zt < 0.0:
                painter.setBrush(back_col)
                painter.drawRect(QRectF(px - 0.9, py - 0.9, 1.8, 1.8))
            elif zt < 0.52:
                painter.setBrush(mid_col)
                painter.drawEllipse(QPointF(px, py), 1.45, 1.45)
            else:
                painter.setBrush(front_col)
                painter.drawEllipse(QPointF(px, py), 1.85, 1.85)

    def _paint_equator_waveform(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        widget_w: float,
        base_r: float,
        primary: QColor,
    ) -> None:
        half_span = min(widget_w * 0.46, base_r * 1.98)
        energy = 0.10 + self._amp_smooth * (0.45 + 0.55 * self._cur_deform_gain)
        max_bar_h = base_r * (0.14 + energy * 0.44)

        # Horizontal glowing laser rail behind spectrum bars
        rail_grad = QLinearGradient(cx - half_span, cy, cx + half_span, cy)
        rail_edge = QColor(primary.red(), primary.green(), primary.blue(), 0)
        rail_mid = QColor(primary.red(), primary.green(), primary.blue(), 220)
        rail_core = QColor(235, 253, 255, 255)
        rail_grad.setColorAt(0.0, rail_edge)
        rail_grad.setColorAt(0.18, rail_mid)
        rail_grad.setColorAt(0.50, rail_core)
        rail_grad.setColorAt(0.82, rail_mid)
        rail_grad.setColorAt(1.0, rail_edge)
        painter.setPen(QPen(rail_grad, 2.4))
        painter.drawLine(QPointF(cx - half_span, cy), QPointF(cx + half_span, cy))

        # 54 symmetric vertical spectrum bars across the center beam
        bar_col = QColor(primary)
        core_col = QColor(224, 252, 255, 235)
        ph = self._phase * 2.1

        for i in range(_SPEC_BARS):
            u = (i / (_SPEC_BARS - 1)) * 2.0 - 1.0  # -1 .. +1
            dist = abs(u)
            # Symmetric Gaussian + multi-lobe harmonic envelope matching K2 spectrum silhouette
            env = math.exp(-dist * dist * 3.1) * (0.68 + 0.32 * math.cos(dist * 7.5))
            wave = (
                0.55
                + 0.25 * math.sin(ph + dist * 11.0)
                + 0.20 * math.cos(ph * 1.43 - dist * 17.0)
            )
            bh = max(1.5, max_bar_h * env * wave)
            bx = cx + u * half_span
            alpha = int(max(30, min(245, (1.0 - dist * 0.85) * (155 + 90 * self._amp_smooth))))

            bar_col.setAlpha(alpha)
            painter.setPen(QPen(bar_col, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(bx, cy - bh), QPointF(bx, cy + bh))

            if bh > 5.0 and dist < 0.72:
                core_col.setAlpha(int(alpha * 0.85))
                painter.setPen(QPen(core_col, 1.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(QPointF(bx, cy - bh * 0.45), QPointF(bx, cy + bh * 0.45))
