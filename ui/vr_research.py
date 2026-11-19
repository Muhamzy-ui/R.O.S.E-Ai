"""
R.O.S.E (Project MIRROR) - Pure PySide6 Visual VR Research & Place Discovery HUD.
Displays live step-by-step search activity, active neighborhood/city location,
plain-English recommendations, a dark CartoDB local area map, and rich result/place
cards with per-item verified photography.
Zero QtWebEngine / zero QGraphicsEffect — strictly native QtWidgets + QPainter.
"""

from __future__ import annotations

import os
import threading
import urllib.parse
import webbrowser
from typing import Any, Callable

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.theme import METRICS, PALETTE, HudDot, HudFrame, build_shared_qss, load_rose_fonts


def open_external_url(url: str) -> None:
    target = (url or "").strip()
    if not target:
        return
    if not (target.startswith("http://") or target.startswith("https://")):
        target = "https://" + target
    try:
        if os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]
            return
    except Exception:
        pass
    try:
        webbrowser.open(target)
    except Exception:
        pass


def crop_center_pixmap(raw_bytes: bytes | None, target_w: int, target_h: int) -> QPixmap | None:
    """Decode bytes and scale+crop center cleanly without stretching."""
    if not raw_bytes:
        return None
    img = QImage()
    if not img.loadFromData(raw_bytes):
        return None
    if img.width() < 40 or img.height() < 40:
        return None
    src = QPixmap.fromImage(img)
    scaled = src.scaled(
        target_w,
        target_h,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x_off = max(0, (scaled.width() - target_w) // 2)
    y_off = max(0, (scaled.height() - target_h) // 2)
    cropped = scaled.copy(x_off, y_off, min(target_w, scaled.width()), min(target_h, scaled.height()))

    # Subtle bottom gradient vignette so overlay badges stay ultra legible
    out = QPixmap(cropped.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(0, 0, out.width(), out.height(), 8, 8)
    p.setClipPath(path)
    p.drawPixmap(0, 0, cropped)
    p.fillRect(0, int(out.height() * 0.55), out.width(), int(out.height() * 0.45), QColor(2, 8, 20, 125))
    p.end()
    return out


def _clip_words(text: str, limit: int = 165) -> str:
    s = " ".join(str(text or "").split()).strip()
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0].strip()
    return (cut if len(cut) >= int(limit * 0.6) else s[:limit]).rstrip(",;:") + "…"


class ResultCardWidget(HudFrame):
    """Glass card for a single restaurant, date spot, product, or web finding."""

    def __init__(self, index: int, item: dict[str, Any], location_hint: str = "", parent: QWidget | None = None):
        super().__init__(accent=PALETTE.accent_cyan, chamfer=10.0, fill_alpha=185, parent=parent)
        self.index = index
        self.item = dict(item)
        self.location_hint = location_hint
        self._photo_w = 320
        self._photo_h = 132
        self._raw_photo: bytes | None = None

        self.setFixedHeight(394)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # Photo banner (Ignored horizontal policy prevents grid column expansion past 50%)
        self._photo_box = QLabel()
        self._photo_box.setFixedHeight(self._photo_h)
        self._photo_box.setMinimumWidth(140)
        self._photo_box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._photo_box.setScaledContents(False)
        self._photo_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._photo_box.setStyleSheet(
            f"background-color: rgba(4, 18, 38, 0.92); border: 1px solid {PALETTE.border_subtle}; "
            f"border-radius: 8px; color: {PALETTE.text_secondary}; font-size: 11px; font-weight: 600;"
        )
        root.addWidget(self._photo_box)

        # Rank + Badges row
        badges_row = QHBoxLayout()
        badges_row.setSpacing(6)

        rank_lbl = QLabel(f"#{index + 1}")
        rank_lbl.setStyleSheet(
            f"background: {PALETTE.info_soft}; color: {PALETTE.accent_cyan}; "
            f"border: 1px solid {PALETTE.border_medium}; border-radius: 9px; "
            f"padding: 2px 7px; font-size: 10px; font-weight: 700;"
        )
        badges_row.addWidget(rank_lbl)

        area = str(item.get("area") or "").strip()
        if area:
            prefix = "📍 " if item.get("is_place") else "📅 "
            area_lbl = QLabel(f"{prefix}{area[:28]}")
            area_lbl.setStyleSheet(
                f"background: rgba(8, 28, 56, 0.85); color: {PALETTE.text_secondary}; "
                f"border: 1px solid {PALETTE.border_subtle}; border-radius: 9px; "
                f"padding: 2px 8px; font-size: 10px; font-weight: 600;"
            )
            badges_row.addWidget(area_lbl)

        tag = str(item.get("badge") or item.get("category") or "").strip()
        if tag:
            tag_lbl = QLabel(tag[:24])
            tag_lbl.setStyleSheet(
                f"background: {PALETTE.success_soft}; color: {PALETTE.success}; "
                f"border: 1px solid rgba(34, 240, 138, 0.45); border-radius: 9px; "
                f"padding: 2px 8px; font-size: 10px; font-weight: 600;"
            )
            badges_row.addWidget(tag_lbl)

        badges_row.addStretch(1)
        root.addLayout(badges_row)

        # Title
        title_text = str(item.get("title") or "Result").strip()
        title_lbl = QLabel(title_text)
        title_lbl.setWordWrap(True)
        title_lbl.setMaximumHeight(40)
        title_lbl.setStyleSheet(
            f"color: {PALETTE.text_primary}; font-size: 13.5px; font-weight: 700; "
            f"font-family: {METRICS.font_family_display}; background: transparent; border: none;"
        )
        root.addWidget(title_lbl)

        # Subtitle / Price / Rating
        sub = str(item.get("subtitle") or "").strip()
        if sub:
            sub_lbl = QLabel(_clip_words(sub, 95))
            sub_lbl.setWordWrap(True)
            sub_lbl.setMaximumHeight(22)
            sub_lbl.setStyleSheet(
                f"color: {PALETTE.warning}; font-size: 11px; font-weight: 600; "
                f"background: transparent; border: none;"
            )
            root.addWidget(sub_lbl)

        # Bullet highlights (3 crisp complete bullets cleanly wrapped without mid-word clipping)
        bullets = item.get("bullets") or []
        summary = str(item.get("summary") or "").strip()
        lines: list[str] = []
        if isinstance(bullets, list) and bullets:
            lines = [f"• {_clip_words(str(b), 160)}" for b in bullets[:3] if str(b).strip()]
        elif summary:
            lines = [_clip_words(summary, 240)]

        desc_lbl = QLabel("\n".join(lines) if lines else "• Verified web match")
        desc_lbl.setWordWrap(True)
        desc_lbl.setMaximumHeight(112)
        desc_lbl.setStyleSheet(
            f"color: {PALETTE.text_secondary}; font-size: 11px; line-height: 1.3; "
            f"background: transparent; border: none;"
        )
        root.addWidget(desc_lbl)

        root.addStretch(1)

        # Footer source + action buttons
        footer = QHBoxLayout()
        footer.setSpacing(6)

        src = str(item.get("source") or "").strip()
        if src:
            src_lbl = QLabel(src[:28])
            src_lbl.setStyleSheet(
                f"color: {PALETTE.text_muted}; font-size: 10px; background: transparent; border: none;"
            )
            footer.addWidget(src_lbl)
        footer.addStretch(1)

        url = str(item.get("url") or "").strip()
        if url:
            open_btn = QPushButton("Open Site")
            open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            open_btn.setFixedHeight(26)
            open_btn.setStyleSheet(
                f"QPushButton {{ background: rgba(12, 34, 66, 0.9); color: {PALETTE.text_primary}; "
                f"border: 1px solid {PALETTE.border_subtle}; border-radius: 6px; padding: 2px 9px; font-size: 10.5px; font-weight: 600; }}"
                f"QPushButton:hover {{ border-color: {PALETTE.accent_cyan}; color: {PALETTE.accent_cyan}; }}"
            )
            open_btn.clicked.connect(lambda _=False, u=url: open_external_url(u))
            footer.addWidget(open_btn)

        if item.get("is_place") or item.get("maps_query"):
            maps_q = str(item.get("maps_query") or f"{title_text} {area or location_hint}").strip()
            maps_url = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote_plus(maps_q)
            dir_btn = QPushButton("Directions")
            dir_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            dir_btn.setFixedHeight(26)
            dir_btn.setStyleSheet(
                f"QPushButton {{ background: rgba(0, 120, 180, 0.35); color: {PALETTE.accent_cyan}; "
                f"border: 1px solid {PALETTE.border_medium}; border-radius: 6px; padding: 2px 9px; font-size: 10.5px; font-weight: 700; }}"
                f"QPushButton:hover {{ background: rgba(38, 230, 255, 0.25); }}"
            )
            dir_btn.clicked.connect(lambda _=False, u=maps_url: open_external_url(u))
            footer.addWidget(dir_btn)

        root.addLayout(footer)

        if item.get("image_bytes"):
            self.set_image_bytes(item.get("image_bytes"))
        else:
            self._set_placeholder()

    def _set_placeholder(self) -> None:
        title = str(self.item.get("title") or "").strip()
        tag = str(self.item.get("badge") or "VERIFIED MATCH").upper()
        area = str(self.item.get("area") or self.location_hint or "").strip()
        lines = [tag]
        if title:
            lines.append(title[:36])
        if area:
            lines.append(f"📍 {area[:34]}")
        self._photo_box.setPixmap(QPixmap())
        self._photo_box.setText("\n".join(lines))

    def set_image_bytes(self, raw_bytes: bytes | None) -> None:
        if not raw_bytes:
            self._set_placeholder()
            return
        self._raw_photo = raw_bytes
        pix = crop_center_pixmap(raw_bytes, 336, self._photo_h)
        if pix is not None and not pix.isNull():
            self._photo_box.setText("")
            self._photo_box.setPixmap(pix)
        else:
            self._set_placeholder()


class VrResearchWindow(QWidget):
    """MIRROR Dark-Glass Live Visual Search & Local Place Discovery Window."""

    def __init__(
        self,
        on_search_cb: Callable[[str], None] | None = None,
        on_close_cb: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(None)
        load_rose_fonts()
        self._on_search_cb = on_search_cb
        self._on_close_cb = on_close_cb
        self._drag_pos: QPoint | None = None
        self._current_query = ""
        self._current_location = ""
        self._is_place_mode = False
        self._card_widgets: list[ResultCardWidget] = []
        self._map_raw_bytes: bytes | None = None

        self.setWindowTitle("R.O.S.E // Visual Web & Place Search")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(build_shared_qss())

        # Responsive sizing bounded comfortably to screen
        app = QApplication.instance()
        scr = app.primaryScreen() if app else None
        avail = scr.availableGeometry() if scr else QRect(0, 0, 1440, 900)
        w = min(1180, max(880, int(avail.width() * 0.80)))
        h = min(740, max(580, int(avail.height() * 0.80)))
        self.resize(w, h)
        self.move(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)

        self._shell = HudFrame(accent=PALETTE.accent_cyan, chamfer=12.0, fill_alpha=228, parent=self)
        shell_layout = QVBoxLayout(self._shell)
        shell_layout.setContentsMargins(14, 12, 14, 10)
        shell_layout.setSpacing(10)
        outer.addWidget(self._shell)

        # ── 1. Top Header Bar ────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(10)

        self._status_dot = HudDot(color=PALETTE.accent_cyan, diameter=9, parent=self._shell)
        header.addWidget(self._status_dot)

        brand = QLabel("R.O.S.E // LIVE VISUAL SEARCH")
        brand.setStyleSheet(
            f"color: {PALETTE.accent_cyan}; font-size: 14px; font-weight: 700; "
            f"letter-spacing: 1.6px; font-family: {METRICS.font_family_display};"
        )
        header.addWidget(brand)

        self._mode_pill = QLabel("WEB INTELLIGENCE")
        self._mode_pill.setStyleSheet(
            f"background: {PALETTE.info_soft}; color: {PALETTE.accent_cyan}; "
            f"border: 1px solid {PALETTE.border_medium}; border-radius: 10px; "
            f"padding: 3px 10px; font-size: 10.5px; font-weight: 700;"
        )
        header.addWidget(self._mode_pill)

        self._loc_pill = QLabel("📍 ACTIVE LOCATION")
        self._loc_pill.setStyleSheet(
            f"background: rgba(12, 36, 68, 0.85); color: {PALETTE.text_secondary}; "
            f"border: 1px solid {PALETTE.border_subtle}; border-radius: 10px; "
            f"padding: 3px 10px; font-size: 10.5px; font-weight: 600;"
        )
        header.addWidget(self._loc_pill)

        header.addStretch(1)

        btn_qss = (
            f"QPushButton {{ background: rgba(10, 32, 62, 0.92); color: {PALETTE.text_primary}; "
            f"border: 1px solid {PALETTE.border_subtle}; border-radius: 6px; padding: 4px 11px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ border-color: {PALETTE.accent_cyan}; color: {PALETTE.accent_cyan}; background: rgba(16, 48, 92, 0.95); }}"
        )

        self._chrome_btn = QPushButton("Open in Chrome")
        self._chrome_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._chrome_btn.setStyleSheet(btn_qss)
        self._chrome_btn.clicked.connect(self._open_google_search)
        header.addWidget(self._chrome_btn)

        self._maps_btn = QPushButton("Google Maps")
        self._maps_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._maps_btn.setStyleSheet(btn_qss)
        self._maps_btn.clicked.connect(self._open_google_maps)
        header.addWidget(self._maps_btn)

        min_btn = QPushButton("Hide")
        min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        min_btn.setStyleSheet(btn_qss)
        min_btn.clicked.connect(self.hide)
        header.addWidget(min_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(34)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QPushButton {{ background: {PALETTE.danger_soft}; border: 1px solid {PALETTE.danger}; "
            f"color: {PALETTE.danger}; border-radius: 6px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: rgba(255, 77, 109, 0.35); }}"
        )
        close_btn.clicked.connect(self._handle_close)
        header.addWidget(close_btn)

        shell_layout.addLayout(header)

        # ── 2. Query Bar + Live Search Step-by-Step Banner ───────────────
        search_bar = QHBoxLayout()
        search_bar.setSpacing(8)

        self._query_edit = QLineEdit()
        self._query_edit.setFixedHeight(34)
        self._query_edit.setStyleSheet(
            f"QLineEdit {{ background: rgba(4, 15, 34, 0.92); color: {PALETTE.text_primary}; "
            f"border: 1px solid {PALETTE.border_subtle}; border-radius: 7px; padding: 4px 11px; font-size: 12.5px; }}"
            f"QLineEdit:focus {{ border-color: {PALETTE.accent_cyan}; }}"
        )
        self._query_edit.setPlaceholderText("Ask or search anything (e.g. 'breakfast near me', 'date spot Abuja', 'iPhone vs Samsung')...")
        self._query_edit.returnPressed.connect(self._trigger_manual_search)
        search_bar.addWidget(self._query_edit, 1)

        search_go = QPushButton("Search")
        search_go.setObjectName("PrimaryBtn")
        search_go.setCursor(Qt.CursorShape.PointingHandCursor)
        search_go.setFixedHeight(34)
        search_go.setStyleSheet(
            f"QPushButton {{ background: rgba(0, 140, 200, 0.35); color: {PALETTE.accent_cyan}; "
            f"border: 1px solid {PALETTE.border_medium}; border-radius: 7px; padding: 4px 16px; font-size: 12px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: rgba(38, 230, 255, 0.25); }}"
        )
        search_go.clicked.connect(self._trigger_manual_search)
        search_bar.addWidget(search_go)

        shell_layout.addLayout(search_bar)

        progress_panel = HudFrame(accent=PALETTE.accent_cyan, chamfer=8.0, fill_alpha=165, parent=self._shell)
        prog_layout = QVBoxLayout(progress_panel)
        prog_layout.setContentsMargins(12, 8, 12, 8)
        prog_layout.setSpacing(5)

        prog_top = QHBoxLayout()
        prog_top.setSpacing(8)
        self._live_status_lbl = QLabel("Ready for live search.")
        self._live_status_lbl.setStyleSheet(
            f"color: {PALETTE.accent_cyan}; font-size: 12px; font-weight: 700;"
        )
        prog_top.addWidget(self._live_status_lbl, 1)

        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedWidth(180)
        self._progress_bar.setFixedHeight(8)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setStyleSheet(
            f"QProgressBar {{ background: rgba(6, 20, 42, 0.9); border: 1px solid {PALETTE.border_subtle}; border-radius: 4px; }}"
            f"QProgressBar::chunk {{ background: {PALETTE.accent_cyan}; border-radius: 3px; }}"
        )
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        prog_top.addWidget(self._progress_bar)
        prog_layout.addLayout(prog_top)

        self._steps_lbl = QLabel("Waiting for query...")
        self._steps_lbl.setWordWrap(True)
        self._steps_lbl.setStyleSheet(
            f"color: {PALETTE.text_secondary}; font-size: 11px; font-family: {METRICS.font_family_mono};"
        )
        prog_layout.addWidget(self._steps_lbl)

        shell_layout.addWidget(progress_panel)

        # ── 3. Main Two-Column Content ───────────────────────────────────
        body = QHBoxLayout()
        body.setSpacing(12)

        # Left column (~35%): Summary + Map + Optional Metrics
        left_col = QVBoxLayout()
        left_col.setSpacing(10)

        self._summary_card = HudFrame(accent=PALETTE.accent_cyan, chamfer=10.0, fill_alpha=182, parent=self._shell)
        sum_layout = QVBoxLayout(self._summary_card)
        sum_layout.setContentsMargins(12, 10, 12, 10)
        sum_layout.setSpacing(6)

        sum_hdr = QLabel("WHAT R.O.S.E FOUND")
        sum_hdr.setStyleSheet(
            f"color: {PALETTE.accent_cyan}; font-size: 11px; font-weight: 700; letter-spacing: 1.2px;"
        )
        sum_layout.addWidget(sum_hdr)

        self._answer_lbl = QLabel("Say what you're looking for — restaurants, date spots, shops, or deep questions — and I'll show every step live.")
        self._answer_lbl.setWordWrap(True)
        self._answer_lbl.setStyleSheet(
            f"color: {PALETTE.text_primary}; font-size: 13px; line-height: 1.4;"
        )
        sum_layout.addWidget(self._answer_lbl)

        self._highlights_lbl = QLabel("")
        self._highlights_lbl.setWordWrap(True)
        self._highlights_lbl.setStyleSheet(
            f"color: {PALETTE.text_secondary}; font-size: 11.5px; line-height: 1.38;"
        )
        sum_layout.addWidget(self._highlights_lbl)
        left_col.addWidget(self._summary_card)

        # Map / Primary Context Image card
        self._map_card = HudFrame(accent=PALETTE.accent_cyan, chamfer=10.0, fill_alpha=182, parent=self._shell)
        map_layout = QVBoxLayout(self._map_card)
        map_layout.setContentsMargins(10, 10, 10, 12)
        map_layout.setSpacing(8)

        map_top = QHBoxLayout()
        self._map_title_lbl = QLabel("📍 LIVE AREA MAP")
        self._map_title_lbl.setStyleSheet(
            f"color: {PALETTE.accent_cyan}; font-size: 11px; font-weight: 700; letter-spacing: 1.1px;"
        )
        map_top.addWidget(self._map_title_lbl)
        map_top.addStretch(1)
        self._map_coords_lbl = QLabel("")
        self._map_coords_lbl.setStyleSheet(f"color: {PALETTE.text_muted}; font-size: 10px;")
        map_top.addWidget(self._map_coords_lbl)
        map_layout.addLayout(map_top)

        self._map_img_lbl = QLabel("Loading map...")
        self._map_img_lbl.setFixedHeight(158)
        self._map_img_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._map_img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._map_img_lbl.setStyleSheet(
            f"background: rgba(3, 14, 28, 0.9); border: 1px solid {PALETTE.border_subtle}; "
            f"border-radius: 8px; color: {PALETTE.text_muted}; font-size: 11px;"
        )
        map_layout.addWidget(self._map_img_lbl)

        map_actions = QHBoxLayout()
        open_map_btn = QPushButton("Open Full Map in Chrome")
        open_map_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_map_btn.setFixedHeight(28)
        open_map_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(0, 120, 180, 0.30); color: {PALETTE.accent_cyan}; "
            f"border: 1px solid {PALETTE.border_medium}; border-radius: 6px; padding: 3px 10px; font-size: 11px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: rgba(38, 230, 255, 0.22); }}"
        )
        open_map_btn.clicked.connect(self._open_google_maps)
        map_actions.addWidget(open_map_btn)
        map_layout.addLayout(map_actions)
        left_col.addWidget(self._map_card)

        # Optional comparison bars card (shown ONLY when genuine comparison data exists)
        self._metrics_card = HudFrame(accent=PALETTE.accent_cyan, chamfer=10.0, fill_alpha=175, parent=self._shell)
        met_layout = QVBoxLayout(self._metrics_card)
        met_layout.setContentsMargins(12, 10, 12, 10)
        met_layout.setSpacing(6)
        self._metrics_title_lbl = QLabel("COMPARISON")
        self._metrics_title_lbl.setStyleSheet(
            f"color: {PALETTE.accent_cyan}; font-size: 11px; font-weight: 700; letter-spacing: 1.1px;"
        )
        met_layout.addWidget(self._metrics_title_lbl)
        self._metrics_rows_box = QVBoxLayout()
        self._metrics_rows_box.setSpacing(5)
        met_layout.addLayout(self._metrics_rows_box)
        self._metrics_card.setVisible(False)
        left_col.addWidget(self._metrics_card)

        left_col.addStretch(1)

        left_wrap = QWidget()
        left_wrap.setStyleSheet("background: transparent;")
        left_wrap.setLayout(left_col)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        left_scroll.setMinimumWidth(330)
        left_scroll.setMaximumWidth(410)
        left_scroll.setWidget(left_wrap)
        body.addWidget(left_scroll, 35)

        # Right column (~65%): Scrollable 2-Column Visual Results Grid
        self._cards_scroll = QScrollArea()
        self._cards_scroll.setWidgetResizable(True)
        self._cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cards_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._cards_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._cards_container = QWidget()
        self._cards_container.setStyleSheet("background: transparent;")
        self._cards_grid = QGridLayout(self._cards_container)
        self._cards_grid.setContentsMargins(2, 2, 6, 8)
        self._cards_grid.setHorizontalSpacing(10)
        self._cards_grid.setVerticalSpacing(12)
        self._cards_grid.setColumnStretch(0, 1)
        self._cards_grid.setColumnStretch(1, 1)
        self._cards_scroll.setWidget(self._cards_container)

        body.addWidget(self._cards_scroll, 65)
        shell_layout.addLayout(body, 1)

        # Bottom grip row
        bottom_row = QHBoxLayout()
        self._footer_hint = QLabel("Tip: Click any card's 'Open Site' or 'Directions' to open straight in Chrome.")
        self._footer_hint.setStyleSheet(f"color: {PALETTE.text_muted}; font-size: 10.5px;")
        bottom_row.addWidget(self._footer_hint, 1)
        grip = QSizeGrip(self)
        bottom_row.addWidget(grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        shell_layout.addLayout(bottom_row)

    # ── Window Drag & Controls ───────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= 54:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def _handle_close(self) -> None:
        self.hide()
        if callable(self._on_close_cb):
            try:
                self._on_close_cb()
            except Exception:
                pass

    def _trigger_manual_search(self) -> None:
        q = self._query_edit.text().strip()
        if q and callable(self._on_search_cb):
            threading.Thread(target=self._on_search_cb, args=(q,), daemon=True).start()

    def _open_google_search(self) -> None:
        q = self._current_query or self._query_edit.text().strip()
        if not q:
            return
        full_q = f"{q} {self._current_location}".strip() if "near me" in q.lower() and self._current_location else q
        open_external_url("https://www.google.com/search?q=" + urllib.parse.quote_plus(full_q))

    def _open_google_maps(self) -> None:
        q = self._current_query or "restaurants"
        loc = self._current_location or ""
        search_term = f"{q} near {loc}".strip() if loc else q
        open_external_url("https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote_plus(search_term))

    # ── Live State Updaters (Qt Main Thread Only) ────────────────────────

    def _clear_cards(self) -> None:
        self._card_widgets.clear()
        while self._cards_grid.count():
            child = self._cards_grid.takeAt(0)
            w = child.widget() if child else None
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def start_search_ui(self, query: str, location_display: str, mode_label: str, is_place_mode: bool) -> None:
        self._current_query = query
        self._current_location = location_display
        self._is_place_mode = is_place_mode
        self._query_edit.setText(query)
        self._mode_pill.setText(mode_label.upper())
        self._loc_pill.setText(f"📍 {location_display}" if location_display else "🌐 GLOBAL SEARCH")
        self._maps_btn.setVisible(is_place_mode)
        self._map_card.setVisible(is_place_mode)
        self._metrics_card.setVisible(False)
        self._status_dot.set_color(PALETTE.accent_cyan)
        self._live_status_lbl.setText(f"● Searching: {query}")
        self._progress_bar.setRange(0, 0)  # pulsing live scan
        self._steps_lbl.setText(f"● Step 1: Preparing search around {location_display or 'web'}...")
        self._answer_lbl.setText(f"Looking up real options for \"{query}\"...")
        self._highlights_lbl.setText("")
        self._map_img_lbl.setPixmap(QPixmap())
        self._map_img_lbl.setText("Loading live area map...")
        self._clear_cards()

        # Scanning placeholder card
        wait_lbl = QLabel("Scanning live sources and matching verified photos for each result...\nEach step updates right above in real time.")
        wait_lbl.setWordWrap(True)
        wait_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wait_lbl.setStyleSheet(f"color: {PALETTE.text_secondary}; font-size: 13px; padding: 38px;")
        self._cards_grid.addWidget(wait_lbl, 0, 0)

        self.show()
        self.raise_()

    def update_steps_ui(self, active_status: str, steps: list[str]) -> None:
        self._live_status_lbl.setText(active_status)
        if steps:
            self._steps_lbl.setText("\n".join(steps[-4:]))

    def set_map_ui(self, map_bytes: bytes | None, area_label: str, coords_text: str) -> None:
        self._map_raw_bytes = map_bytes
        short_area = (area_label or "")[:24].upper()
        self._map_title_lbl.setText(f"📍 AREA MAP • {short_area}" if short_area else "📍 LOCAL MAP")
        self._map_coords_lbl.setText(coords_text)
        if map_bytes:
            self._map_card.setVisible(True)
            pix = crop_center_pixmap(map_bytes, 330, 158)
            if pix is not None and not pix.isNull():
                self._map_img_lbl.setText("")
                self._map_img_lbl.setPixmap(pix)
                return
        self._map_img_lbl.setPixmap(QPixmap())
        self._map_img_lbl.setText("Map preview unavailable — click 'Open Full Map in Chrome' below.")

    def set_results_ui(self, board_data: dict[str, Any], items: list[dict[str, Any]], steps: list[str]) -> None:
        self._status_dot.set_color(PALETTE.success)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(100)
        self._live_status_lbl.setText(
            f"✓ Search Complete — Found {len(items)} result{'s' if len(items) != 1 else ''}"
        )
        if steps:
            self._steps_lbl.setText("\n".join(steps[-5:]))

        answer = str(board_data.get("answer") or board_data.get("headline") or "").strip()
        self._answer_lbl.setText(answer or "Here is what I found from live web sources.")

        bullets = board_data.get("highlights") or []
        if bullets:
            self._highlights_lbl.setText("\n".join(f"• {str(b).strip()}" for b in bullets[:5] if str(b).strip()))
        else:
            self._highlights_lbl.setText("")

        # Optional quantitative comparison chart ONLY when genuinely relevant
        chart_bars = board_data.get("chart_bars") or []
        show_chart = bool(board_data.get("show_chart")) and bool(chart_bars)
        self._metrics_card.setVisible(show_chart)
        while self._metrics_rows_box.count():
            child = self._metrics_rows_box.takeAt(0)
            if child and child.widget():
                child.widget().deleteLater()
        if show_chart:
            self._metrics_title_lbl.setText(str(board_data.get("chart_title") or "COMPARISON").upper())
            for bar in chart_bars[:5]:
                row = QHBoxLayout()
                lbl = QLabel(str(bar.get("label") or "")[:20])
                lbl.setStyleSheet(f"color: {PALETTE.text_secondary}; font-size: 11px;")
                pct = max(0, min(100, int(bar.get("pct") or 0)))
                val = QLabel(str(bar.get("display") or f"{pct}%"))
                val.setStyleSheet(f"color: {PALETTE.accent_cyan}; font-size: 11px; font-weight: 700;")
                pbar = QProgressBar()
                pbar.setFixedHeight(7)
                pbar.setTextVisible(False)
                pbar.setRange(0, 100)
                pbar.setValue(pct)
                wrap = QWidget()
                wl = QVBoxLayout(wrap)
                wl.setContentsMargins(0, 0, 0, 0)
                wl.setSpacing(2)
                row.addWidget(lbl, 1)
                row.addWidget(val)
                wl.addLayout(row)
                wl.addWidget(pbar)
                self._metrics_rows_box.addWidget(wrap)

        # Populate matched cards in 2-column grid
        self._clear_cards()
        if not items:
            empty = QLabel("No matching results loaded for this query yet. Try clicking 'Open in Chrome' or searching with another phrase.")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"color: {PALETTE.text_secondary}; font-size: 13px; padding: 36px;")
            self._cards_grid.addWidget(empty, 0, 0)
            return

        for idx, item in enumerate(items[:8]):
            card = ResultCardWidget(idx, item, location_hint=self._current_location, parent=self._cards_container)
            self._card_widgets.append(card)
            self._cards_grid.addWidget(card, idx // 2, idx % 2)

        self.show()
        self.raise_()

    def update_card_image_ui(self, index: int, raw_bytes: bytes | None) -> None:
        if 0 <= index < len(self._card_widgets):
            self._card_widgets[index].set_image_bytes(raw_bytes)


class VrResearchController:
    """Thread-safe controller connecting background `web_research` to `VrResearchWindow`."""

    def __init__(self, tk_or_qt_root: Any = None, on_search_cb: Callable[[str], None] | None = None):
        self._root = tk_or_qt_root
        self._on_search_cb = on_search_cb
        self._window: VrResearchWindow | None = None
        self.last_query: str = ""
        self.last_location: str = ""
        self.last_steps: list[str] = []
        self.last_board_data: dict[str, Any] = {}
        self.last_items: list[dict[str, Any]] = []
        self.last_map_bytes: bytes | None = None

    def _can_use_qt(self) -> bool:
        return QApplication.instance() is not None and hasattr(self._root, "after")

    def _dispatch(self, fn: Callable[[], None]) -> None:
        if self._can_use_qt():
            try:
                self._root.after(0, fn)
                return
            except Exception:
                pass
        if QApplication.instance() is not None and threading.current_thread() is threading.main_thread():
            fn()

    def _ensure_window(self) -> VrResearchWindow | None:
        if QApplication.instance() is None:
            return None
        if self._window is None:
            self._window = VrResearchWindow(on_search_cb=self._on_search_cb)
        return self._window

    def start_research(self, query: str, location_display: str, mode_label: str, is_place_mode: bool) -> None:
        self.last_query = query
        self.last_location = location_display
        self.last_steps = [f"● Step 1: Location locked → {location_display or 'Global'}"]
        self.last_board_data = {}
        self.last_items = []
        self.last_map_bytes = None

        def _ui() -> None:
            win = self._ensure_window()
            if win is not None:
                win.start_search_ui(query, location_display, mode_label, is_place_mode)

        self._dispatch(_ui)

    def push_step(self, active_status: str, steps: list[str]) -> None:
        self.last_steps = list(steps)

        def _ui() -> None:
            win = self._ensure_window()
            if win is not None:
                win.update_steps_ui(active_status, steps)

        self._dispatch(_ui)

    def set_map(self, map_bytes: bytes | None, area_label: str, coords_text: str) -> None:
        self.last_map_bytes = map_bytes

        def _ui() -> None:
            win = self._ensure_window()
            if win is not None:
                win.set_map_ui(map_bytes, area_label, coords_text)

        self._dispatch(_ui)

    def apply_results(self, board_data: dict[str, Any], items: list[dict[str, Any]], steps: list[str]) -> None:
        self.last_board_data = dict(board_data)
        self.last_items = [dict(x) for x in items]
        self.last_steps = list(steps)

        def _ui() -> None:
            win = self._ensure_window()
            if win is not None:
                win.set_results_ui(board_data, items, steps)

        self._dispatch(_ui)

    def update_card_image(self, index: int, raw_bytes: bytes | None) -> None:
        if 0 <= index < len(self.last_items):
            self.last_items[index]["image_bytes"] = raw_bytes

        def _ui() -> None:
            win = self._ensure_window()
            if win is not None:
                win.update_card_image_ui(index, raw_bytes)

        self._dispatch(_ui)

    def close(self) -> None:
        def _ui() -> None:
            if self._window is not None:
                self._window.hide()

        self._dispatch(_ui)
