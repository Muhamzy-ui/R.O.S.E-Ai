"""
R.O.S.E (Project Mirror) — MIRROR K2 Futuristic AI Command Center Design System.

Preserves all public symbols (`PALETTE`, `METRICS`, `ORB_STATE_PROFILES`,
`CONVERSATION_ROLE_COLORS`, `VOICES`, `build_shared_qss`) while providing
shared Qt chamfered holographic panel geometry, bundled Grift font loading,
and electric-cyan / deep-navy styling matching The K2 MIRROR command room.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFontDatabase,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QFrame, QWidget

_BASE_DIR = Path(__file__).resolve().parent.parent
_FONTS_DIR = _BASE_DIR / "Fonts"
_FONTS_LOADED = False
_RESOLVED_DISPLAY_FONT = "Segoe UI"


def load_rose_fonts() -> str:
    """Resolve HUD fonts on Windows Desktop and populate offscreen Qt fontdb if empty."""
    global _FONTS_LOADED, _RESOLVED_DISPLAY_FONT
    if _FONTS_LOADED:
        return _RESOLVED_DISPLAY_FONT
    _FONTS_LOADED = True

    try:
        if not QFontDatabase.families():
            win_fonts = Path("C:/Windows/Fonts")
            for fname in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "bahnschrift.ttf", "consola.ttf", "consolab.ttf"):
                fp = win_fonts / fname
                if fp.exists():
                    try:
                        QFontDatabase.addApplicationFont(str(fp))
                    except Exception:
                        pass
    except Exception:
        pass
    _RESOLVED_DISPLAY_FONT = "Bahnschrift"
    return _RESOLVED_DISPLAY_FONT


class HudDot(QWidget):
    """Crisp vector glowing status indicator dot (avoids font glyph fallback issues)."""

    def __init__(self, color: str = "#26E6FF", diameter: int = 7, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._color = QColor(color)
        self._d = float(max(4, diameter))
        box = int(self._d + 6)
        self.setFixedSize(box, box)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def set_color(self, color: str) -> None:
        c = QColor(color)
        if c != self._color:
            self._color = c
            self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx, cy = self.width() * 0.5, self.height() * 0.5
        r = self._d * 0.5
        halo = QColor(self._color)
        halo.setAlpha(72)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(cx, cy), r + 2.2, r + 2.2)
        p.setBrush(self._color)
        p.drawEllipse(QPointF(cx, cy), r, r)


@dataclass(frozen=True)
class ThemePalette:
    # Deep obsidian-navy command-room surfaces
    bg_root: str = "#02060D"
    bg_navy: str = "#041122"
    bg_violet: str = "#081C3A"  # Deep holographic cobalt shadow depth

    # Translucent glass fills
    glass_surface: str = "rgba(4, 14, 30, 0.78)"
    glass_card: str = "rgba(5, 16, 34, 0.72)"
    glass_card_hover: str = "rgba(8, 25, 52, 0.86)"
    glass_elevated: str = "rgba(6, 20, 42, 0.90)"
    glass_input: str = "rgba(4, 13, 28, 0.88)"

    # Glowing HUD borders
    border_subtle: str = "rgba(0, 214, 255, 0.34)"
    border_medium: str = "rgba(38, 229, 255, 0.62)"
    border_accent: str = "#38ECFF"
    border_violet: str = "rgba(46, 168, 255, 0.60)"

    # Typography colors
    text_primary: str = "#EAF7FF"
    text_secondary: str = "#9EDAF0"
    text_muted: str = "#5D97B8"

    # Electric cyan & dual-tone blue accents + telemetry semantics
    accent_cyan: str = "#26E6FF"
    accent_violet: str = "#3B9BFF"
    accent_indigo: str = "#0E4A8A"
    success: str = "#22F08A"
    success_soft: str = "rgba(34, 240, 138, 0.16)"
    warning: str = "#FFB84D"
    warning_soft: str = "rgba(255, 184, 77, 0.16)"
    danger: str = "#FF4D6D"
    danger_soft: str = "rgba(255, 77, 109, 0.16)"
    info_soft: str = "rgba(38, 230, 255, 0.16)"
    violet_soft: str = "rgba(59, 155, 255, 0.16)"


@dataclass(frozen=True)
class ThemeMetrics:
    radius_xl: int = 12
    radius_lg: int = 8
    radius_md: int = 6
    radius_sm: int = 4
    radius_pill: int = 999

    space_xs: int = 4
    space_sm: int = 8
    space_md: int = 10
    space_lg: int = 14
    space_xl: int = 18

    font_family_ui: str = '"Segoe UI", "Inter", system-ui, sans-serif'
    font_family_display: str = '"Bahnschrift", "Segoe UI Semibold", "Segoe UI", sans-serif'
    font_family_mono: str = '"Cascadia Mono", "Consolas", "Courier New", monospace'


PALETTE = ThemePalette()
METRICS = ThemeMetrics()

VOICES = [
    "Aoede",
    "Kore",
    "Leda",
    "Zephyr",
    "Charon",
    "Puck",
    "Fenrir",
    "Orus",
]

# Right-hand CONVERSATION transcript colors matched to MIRROR K2 command HUD
CONVERSATION_ROLE_COLORS = {
    "you": {
        "label": "YOU",
        "accent": "#4AEFFF",
        "bg": "rgba(18, 86, 130, 0.24)",
        "border": "rgba(74, 239, 255, 0.42)",
    },
    "rose": {
        "label": "R.O.S.E",
        "accent": "#EAF7FF",
        "bg": "rgba(8, 28, 56, 0.34)",
        "border": "rgba(38, 230, 255, 0.34)",
    },
    "sys": {
        "label": "SYS",
        "accent": "#F6D05C",
        "bg": "rgba(255, 198, 64, 0.08)",
        "border": "rgba(246, 208, 92, 0.32)",
    },
    "err": {
        "label": "ERR",
        "accent": "#FF5D7A",
        "bg": "rgba(255, 77, 109, 0.15)",
        "border": "rgba(255, 77, 109, 0.48)",
    },
}

ORB_STATE_PROFILES = {
    "IDLE": {
        "primary": "#26E6FF",
        "secondary": "#1A7FFF",
        "scale": 0.96,
        "spin_speed": 0.48,
        "deform_gain": 0.08,
        "breath_amp": 0.014,
        "ripple": False,
        "fps": 24,
        "caption": "Online • Ready",
    },
    "INITIALISING": {
        "primary": "#38BDF8",
        "secondary": "#0EA5E9",
        "scale": 0.94,
        "spin_speed": 1.15,
        "deform_gain": 0.14,
        "breath_amp": 0.020,
        "ripple": False,
        "fps": 30,
        "caption": "Initialising Neural Core...",
    },
    "LISTENING": {
        "primary": "#38F2FF",
        "secondary": "#2695FF",
        "scale": 1.00,
        "spin_speed": 0.85,
        "deform_gain": 0.36,
        "breath_amp": 0.018,
        "ripple": False,
        "fps": 60,
        "caption": "Listening...",
    },
    "THINKING": {
        "primary": "#60B5FF",
        "secondary": "#22E8FF",
        "scale": 0.99,
        "spin_speed": 1.65,
        "deform_gain": 0.24,
        "breath_amp": 0.022,
        "ripple": True,
        "fps": 60,
        "caption": "Thinking...",
    },
    "SPEAKING": {
        "primary": "#7DF9FF",
        "secondary": "#2EB8FF",
        "scale": 1.04,
        "spin_speed": 1.28,
        "deform_gain": 0.56,
        "breath_amp": 0.024,
        "ripple": True,
        "fps": 60,
        "caption": "Speaking...",
    },
    "CALL": {
        "primary": "#22F08A",
        "secondary": "#26E6FF",
        "scale": 1.03,
        "spin_speed": 1.18,
        "deform_gain": 0.44,
        "breath_amp": 0.020,
        "ripple": True,
        "fps": 60,
        "caption": "Live Call",
    },
    "MUTED": {
        "primary": "#4A6A82",
        "secondary": "#263C52",
        "scale": 0.92,
        "spin_speed": 0.18,
        "deform_gain": 0.04,
        "breath_amp": 0.008,
        "ripple": False,
        "fps": 20,
        "caption": "Muted",
    },
    "PAUSED": {
        "primary": "#FFB84D",
        "secondary": "#F59E0B",
        "scale": 0.92,
        "spin_speed": 0.20,
        "deform_gain": 0.06,
        "breath_amp": 0.010,
        "ripple": False,
        "fps": 20,
        "caption": "Paused",
    },
    "ERROR": {
        "primary": "#FF4D6D",
        "secondary": "#DC2626",
        "scale": 0.96,
        "spin_speed": 0.38,
        "deform_gain": 0.18,
        "breath_amp": 0.014,
        "ripple": True,
        "fps": 30,
        "caption": "Attention Required",
    },
}


def make_chamfer_path(
    rect: QRectF,
    c_tl: float = 10.0,
    c_tr: float = 10.0,
    c_br: float = 10.0,
    c_bl: float = 10.0,
) -> QPainterPath:
    """Create a 45-degree chamfered octangular HUD panel QPainterPath."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    max_c = max(0.0, min(w, h) * 0.45)
    tl = min(max(0.0, c_tl), max_c)
    tr = min(max(0.0, c_tr), max_c)
    br = min(max(0.0, c_br), max_c)
    bl = min(max(0.0, c_bl), max_c)

    p = QPainterPath()
    p.moveTo(x + tl, y)
    p.lineTo(x + w - tr, y)
    if tr > 0:
        p.lineTo(x + w, y + tr)
    p.lineTo(x + w, y + h - br)
    if br > 0:
        p.lineTo(x + w - br, y + h)
    p.lineTo(x + bl, y + h)
    if bl > 0:
        p.lineTo(x, y + h - bl)
    p.lineTo(x, y + tl)
    if tl > 0:
        p.lineTo(x + tl, y)
    p.closeSubpath()
    return p


def draw_hud_panel(
    painter: QPainter,
    rect: QRectF,
    accent: str | QColor | None = None,
    fill_alpha: int = 178,
    chamfer: float = 10.0,
    focus: float = 0.0,
    corner_brackets: bool = True,
) -> None:
    """Paint a translucent navy MIRROR HUD frame with glowing cyan borders & brackets."""
    if rect.width() < 8 or rect.height() < 8:
        return
    base = QColor(accent) if accent is not None else QColor(PALETTE.accent_cyan)
    f = max(0.0, min(1.0, float(focus)))

    inner = rect.adjusted(2.5, 2.5, -2.5, -2.5)
    path = make_chamfer_path(inner, chamfer, chamfer, chamfer, chamfer)

    # 1) Soft holographic outer aura (cheap vector strokes, zero blur filters)
    aura_outer = QColor(base)
    aura_outer.setAlpha(int(26 + 34 * f))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(aura_outer, 5.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.MiterJoin))
    painter.drawPath(path)

    aura_mid = QColor(base)
    aura_mid.setAlpha(int(58 + 50 * f))
    painter.setPen(QPen(aura_mid, 2.5))
    painter.drawPath(path)

    # 2) Deep navy translucent glass backdrop
    grad = QLinearGradient(inner.topLeft(), inner.bottomRight())
    grad.setColorAt(0.0, QColor(5, 20, 44, min(245, fill_alpha + 14)))
    grad.setColorAt(0.55, QColor(4, 14, 31, fill_alpha))
    grad.setColorAt(1.0, QColor(3, 10, 22, min(245, fill_alpha + 18)))
    painter.fillPath(path, grad)

    # Subtle internal top cyan sheen
    sheen = QLinearGradient(inner.left(), inner.top(), inner.left(), inner.top() + min(42.0, inner.height() * 0.35))
    c_sheen = QColor(base)
    c_sheen.setAlpha(int(36 + 28 * f))
    sheen.setColorAt(0.0, c_sheen)
    sheen.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.fillPath(path, sheen)

    # 3) Razor crisp outer contour
    edge = QColor(base)
    edge.setAlpha(int(175 + 75 * f))
    painter.setPen(QPen(edge, 1.25))
    painter.drawPath(path)

    if corner_brackets:
        hi = QColor(base).lighter(130)
        hi.setAlpha(int(215 + 40 * f))
        painter.setPen(QPen(hi, 1.85, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        l = min(14.0, inner.width() * 0.16, inner.height() * 0.18)
        x0, y0, x1, y1 = inner.left(), inner.top(), inner.right(), inner.bottom()
        c = chamfer
        # Accent chamfer edges + extended corner ticks
        painter.drawLine(QPointF(x0 + c, y0), QPointF(x0 + c + l, y0))
        painter.drawLine(QPointF(x0, y0 + c), QPointF(x0 + c, y0))
        painter.drawLine(QPointF(x0, y0 + c), QPointF(x0, y0 + c + l))

        painter.drawLine(QPointF(x1 - c - l, y0), QPointF(x1 - c, y0))
        painter.drawLine(QPointF(x1 - c, y0), QPointF(x1, y0 + c))
        painter.drawLine(QPointF(x1, y0 + c), QPointF(x1, y0 + c + l))

        painter.drawLine(QPointF(x0 + c, y1), QPointF(x0 + c + l, y1))
        painter.drawLine(QPointF(x0, y1 - c), QPointF(x0 + c, y1))
        painter.drawLine(QPointF(x0, y1 - c - l), QPointF(x0, y1 - c))

        painter.drawLine(QPointF(x1 - c - l, y1), QPointF(x1 - c, y1))
        painter.drawLine(QPointF(x1 - c, y1), QPointF(x1, y1 - c))
        painter.drawLine(QPointF(x1, y1 - c - l), QPointF(x1, y1 - c))


class HudFrame(QFrame):
    """Lightweight QFrame painting the chamfered MIRROR holographic HUD border."""

    def __init__(
        self,
        accent: Optional[str] = None,
        chamfer: float = 10.0,
        fill_alpha: int = 176,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._hud_accent = accent or PALETTE.accent_cyan
        self._hud_chamfer = chamfer
        self._hud_fill_alpha = fill_alpha
        self._hud_focus = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent; border: none;")

    def set_hud_accent(self, color: str) -> None:
        if color != self._hud_accent:
            self._hud_accent = color
            self.update()

    def set_hud_focus(self, focus: float) -> None:
        val = max(0.0, min(1.0, float(focus)))
        if abs(val - self._hud_focus) > 0.01:
            self._hud_focus = val
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        draw_hud_panel(
            painter,
            QRectF(self.rect()),
            accent=self._hud_accent,
            fill_alpha=self._hud_fill_alpha,
            chamfer=self._hud_chamfer,
            focus=self._hud_focus,
        )


def build_shared_qss() -> str:
    """Global Qt StyleSheet matching the MIRROR electric-cyan command center."""
    return f"""
    QWidget {{
        color: {PALETTE.text_primary};
        font-family: {METRICS.font_family_ui};
        selection-background-color: rgba(38, 230, 255, 0.30);
        selection-color: #FFFFFF;
    }}

    QToolTip {{
        background-color: rgba(4, 15, 34, 0.96);
        color: {PALETTE.text_primary};
        border: 1px solid {PALETTE.border_medium};
        border-radius: 5px;
        padding: 5px 9px;
        font-size: 11px;
    }}

    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {{
        background-color: {PALETTE.glass_input};
        color: {PALETTE.text_primary};
        border: 1px solid {PALETTE.border_subtle};
        border-radius: 6px;
        padding: 6px 10px;
        font-size: 12px;
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
        border: 1px solid {PALETTE.accent_cyan};
        background-color: rgba(6, 22, 46, 0.94);
    }}

    QComboBox QAbstractItemView {{
        background-color: #051329;
        color: {PALETTE.text_primary};
        border: 1px solid {PALETTE.border_medium};
        selection-background-color: rgba(38, 230, 255, 0.25);
    }}

    QScrollBar:vertical {{
        background: rgba(4, 14, 30, 0.35);
        width: 5px;
        margin: 4px 1px 4px 1px;
        border-radius: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: rgba(38, 230, 255, 0.35);
        min-height: 24px;
        border-radius: 2px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: rgba(38, 230, 255, 0.68);
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
        height: 0px;
    }}

    QScrollBar:horizontal {{
        height: 5px;
        background: transparent;
    }}
    QScrollBar::handle:horizontal {{
        background: rgba(38, 230, 255, 0.32);
        border-radius: 2px;
    }}

    QMenu {{
        background-color: rgba(5, 16, 36, 0.96);
        border: 1px solid {PALETTE.border_medium};
        border-radius: 6px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 16px;
        border-radius: 4px;
        font-size: 12px;
        color: {PALETTE.text_primary};
    }}
    QMenu::item:selected {{
        background-color: rgba(38, 230, 255, 0.22);
        color: {PALETTE.accent_cyan};
    }}

    QDialog {{
        background-color: {PALETTE.bg_navy};
    }}
    """
