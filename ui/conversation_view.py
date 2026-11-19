"""
R.O.S.E (Project Mirror) — MIRROR K2 Virtualized Conversation Panel.

Features:
- Chamfered glowing electric-cyan HUD frame matching the reference console
- Header with live status indicator + animated mini waveform spectrum glyph
- Capped 500-message ConversationModel + 50ms batched append + virtualized delegate
- Compact terminal-style holographic transcript (gold SYS, cyan YOU, ice R.O.S.E, red ERR)
- Docked bottom command input (`Type a command...`) + quick submit icon & `SEND >`
"""

from __future__ import annotations

import collections
import datetime as dt
import math
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPointF,
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
    QPainter,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ui.theme import CONVERSATION_ROLE_COLORS, HudDot, HudFrame, METRICS, PALETTE

MAX_MESSAGES = 500
BATCH_INTERVAL_MS = 50


def parse_log_entry(raw_text: str) -> tuple[str, str, str, str]:
    """Parse log lines like 'YOU: hello', 'ROSE: hi', 'SYS: ...', 'ERR: ...'."""
    text = str(raw_text or "").strip()
    now_str = dt.datetime.now().strftime("%H:%M:%S")
    upper = text.upper()
    if upper.startswith("YOU:") or upper.startswith("USER:"):
        return "you", "YOU", text.split(":", 1)[1].strip(), now_str
    if upper.startswith("ROSE:") or upper.startswith("JARVIS:") or upper.startswith("AI:"):
        return "rose", "R.O.S.E", text.split(":", 1)[1].strip(), now_str
    if upper.startswith("ERR:") or upper.startswith("ERROR:"):
        return "err", "ERR", text.split(":", 1)[1].strip(), now_str
    if upper.startswith("SYS:"):
        return "sys", "SYS", text.split(":", 1)[1].strip(), now_str
    return "sys", "SYS", text, now_str


def _parse_rgba(spec: str) -> QColor:
    s = str(spec or "").strip()
    if s.startswith("rgba(") and s.endswith(")"):
        parts = [p.strip() for p in s[5:-1].split(",")]
        if len(parts) == 4:
            r, g, b = int(float(parts[0])), int(float(parts[1])), int(float(parts[2]))
            a = int(round(float(parts[3]) * 255.0)) if float(parts[3]) <= 1.0 else int(float(parts[3]))
            return QColor(r, g, b, max(0, min(255, a)))
    return QColor(s)


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    prefix: str
    body: str
    timestamp: str


class _BatchBridge(QObject):
    flush_requested = Signal()


class ConversationModel(QAbstractListModel):
    """Fixed-cap virtualized list model storing at most 500 parsed messages."""

    RoleMessage = Qt.ItemDataRole.UserRole + 1

    def __init__(self, max_messages: int = MAX_MESSAGES, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._max = max(10, int(max_messages))
        self._items: collections.deque[ConversationMessage] = collections.deque()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._items):
            return None
        msg = self._items[row]
        if role == self.RoleMessage:
            return msg
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{msg.prefix}: {msg.body}"
        return None

    def append_batch(self, raw_texts: list[str]) -> int:
        if not raw_texts:
            return 0
        parsed = [ConversationMessage(*parse_log_entry(t)) for t in raw_texts if str(t or "").strip()]
        if not parsed:
            return 0

        if len(parsed) >= self._max:
            parsed = parsed[-self._max:]
            self.beginResetModel()
            self._items.clear()
            self._items.extend(parsed)
            self.endResetModel()
            return len(parsed)

        overflow = (len(self._items) + len(parsed)) - self._max
        if overflow > 0:
            self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
            for _ in range(overflow):
                self._items.popleft()
            self.endRemoveRows()

        start = len(self._items)
        end = start + len(parsed) - 1
        self.beginInsertRows(QModelIndex(), start, end)
        self._items.extend(parsed)
        self.endInsertRows()
        return len(parsed)

    def clear(self) -> None:
        if not self._items:
            return
        self.beginResetModel()
        self._items.clear()
        self.endResetModel()

    def messages(self) -> list[ConversationMessage]:
        return list(self._items)


class ConversationDelegate(QStyledItemDelegate):
    """Crisp MIRROR terminal/holographic message painter."""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._font_prefix = QFont("Cascadia Mono", 9)
        self._font_prefix.setWeight(QFont.Weight.Bold)
        self._font_body = QFont("Segoe UI", 9)
        self._font_body.setPixelSize(12)
        self._font_prefix.setPixelSize(11)
        self._font_time = QFont("Cascadia Mono", 8)
        self._font_time.setPixelSize(9)

        self._role_colors: dict[str, tuple[QColor, QColor, QColor]] = {}
        for role_key, spec in CONVERSATION_ROLE_COLORS.items():
            self._role_colors[role_key] = (
                QColor(spec["accent"]),
                _parse_rgba(spec["bg"]),
                _parse_rgba(spec["border"]),
            )
        self._body_color = QColor(PALETTE.text_primary)
        self._time_color = QColor(PALETTE.text_muted)

    def _measure(self, msg: ConversationMessage, view_width: int) -> tuple[int, int]:
        usable_w = max(140, view_width - 14)
        if msg.role == "sys":
            # Compact single-stream telemetry format: "SYS: ROSE ready. Listening..."
            fm = QFontMetrics(self._font_body)
            line_text = f"{msg.prefix}: {msg.body}"
            br = fm.boundingRect(0, 0, usable_w - 8, 900, Qt.TextFlag.TextWordWrap, line_text)
            return usable_w, max(19, br.height() + 5)

        fm_b = QFontMetrics(self._font_body)
        header_h = 15
        text_rect = fm_b.boundingRect(0, 0, max(100, usable_w - 18), 1600, Qt.TextFlag.TextWordWrap, msg.body)
        total_h = 6 + header_h + 2 + text_rect.height() + 7
        return usable_w, max(36, total_h)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        msg = index.data(ConversationModel.RoleMessage)
        if not isinstance(msg, ConversationMessage):
            return QSize(200, 24)
        vw = option.rect.width() if option.rect.width() > 60 else 280
        w, h = self._measure(msg, vw)
        return QSize(w, h)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        msg = index.data(ConversationModel.RoleMessage)
        if not isinstance(msg, ConversationMessage):
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        accent_c, bg_c, border_c = self._role_colors.get(
            msg.role, self._role_colors["sys"]
        )

        # Compact glowing HUD line for SYS messages (matches reference right panel)
        if msg.role == "sys":
            r = option.rect.adjusted(6, 2, -6, -1)
            painter.setFont(self._font_body)
            painter.setPen(accent_c)
            line = f"{msg.prefix}: {msg.body}"
            painter.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, line)
            painter.restore()
            return

        # Structured glowing accent-bar card for YOU / R.O.S.E / ERR
        card = QRectF(option.rect.adjusted(5, 3, -5, -3))
        painter.setPen(QPen(border_c, 0.9))
        painter.setBrush(bg_c)
        painter.drawRoundedRect(card, 4.0, 4.0)

        # Crisp glowing left accent stripe
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent_c)
        painter.drawRect(QRectF(card.left(), card.top() + 3, 2.5, max(4.0, card.height() - 6)))

        pad_x = card.left() + 9.0
        pad_r = card.right() - 7.0
        top_y = card.top() + 4.0

        painter.setFont(self._font_prefix)
        painter.setPen(accent_c)
        painter.drawText(QRectF(pad_x, top_y, 110, 15), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, msg.prefix)

        painter.setFont(self._font_time)
        painter.setPen(self._time_color)
        painter.drawText(QRectF(pad_r - 70, top_y, 70, 15), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, msg.timestamp)

        painter.setFont(self._font_body)
        painter.setPen(self._body_color if msg.role != "err" else accent_c)
        body_rect = QRectF(pad_x, top_y + 16.0, max(60.0, pad_r - pad_x), max(14.0, card.bottom() - top_y - 18.0))
        painter.drawText(body_rect, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, msg.body)
        painter.restore()


class _TranscriptListView(QListView):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._last_w = 0

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w = self.viewport().width()
        if abs(w - self._last_w) >= 6:
            self._last_w = w
            self.scheduleDelayedItemsLayout()


class _MiniWaveformWidget(QWidget):
    """Live animated 9-bar cyan audio spectrum glyph beside CONVERSATION status."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(36, 16)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._phase = 0.0
        self._level = 0.2
        self._active = True

    def pulse(self, dt_sec: float, level: float, active: bool) -> None:
        self._phase += dt_sec * (7.0 if active else 2.0)
        self._level += (max(0.08, level) - self._level) * 0.35
        self._active = active
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        mid = h * 0.5
        bars = 9
        step = w / bars
        col = QColor(PALETTE.accent_cyan if self._active else PALETTE.text_muted)
        p.setPen(QPen(col, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        weights = (0.35, 0.55, 0.85, 0.65, 1.0, 0.68, 0.85, 0.52, 0.35)
        for i in range(bars):
            x = step * (i + 0.5)
            wave = 0.5 + 0.5 * math.sin(self._phase + i * 0.9)
            bh = max(1.5, (h * 0.42) * weights[i] * (0.28 + 0.72 * self._level * wave))
            p.drawLine(QPointF(x, mid - bh), QPointF(x, mid + bh))


class _PlayTriangleButton(QPushButton):
    """Circular glowing cyan ▶ submit glyph matching reference bottom-right."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__("", parent)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Submit command")

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(1.5, 1.5, float(self.width()) - 3.0, float(self.height()) - 3.0)
        hovered = self.underMouse()
        p.setPen(QPen(QColor(PALETTE.accent_cyan if hovered else PALETTE.border_accent), 1.4))
        p.setBrush(QColor(16, 110, 175, 225 if hovered else 185))
        p.drawEllipse(r)

        cx, cy = r.center().x() + 1.0, r.center().y()
        tri = QPolygonF([
            QPointF(cx - 4.5, cy - 5.2),
            QPointF(cx + 5.5, cy),
            QPointF(cx - 4.5, cy + 5.2),
        ])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#EAF7FF"))
        p.drawPolygon(tri)


class ConversationPanel(HudFrame):
    """Right HUD CONVERSATION container with state waveform & docked command bar."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(accent=PALETTE.accent_cyan, chamfer=11.0, fill_alpha=184, parent=parent)
        self.setObjectName("ConversationPanel")
        self.setMinimumWidth(270)
        self.setMaximumWidth(350)

        self._lock = threading.Lock()
        self._pending_raw: list[str] = []
        self._auto_scroll = True

        self.on_command_submit: Optional[Callable[[str], Any]] = None

        self.model = ConversationModel(max_messages=MAX_MESSAGES, parent=self)
        self.delegate = ConversationDelegate(self)

        self._bridge = _BatchBridge()
        self._bridge.flush_requested.connect(self._schedule_batch_on_ui)

        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.setInterval(BATCH_INTERVAL_MS)
        self._batch_timer.timeout.connect(self._flush_pending_batch)

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        # Header row: ● CONVERSATION        LISTENING ╿|||╿
        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        dot = HudDot(PALETTE.accent_cyan, diameter=6, parent=self)
        title = QLabel("CONVERSATION", self)
        title.setStyleSheet(
            f"color: {PALETTE.text_secondary}; font-family: {METRICS.font_family_display}; "
            "font-size: 10.5px; font-weight: 700; letter-spacing: 1.5px; background: transparent;"
        )
        self.state_badge_lbl = QLabel("LISTENING", self)
        self.state_badge_lbl.setStyleSheet(
            f"color: #46F0FF; font-family: {METRICS.font_family_display}; "
            "font-size: 9.5px; font-weight: 700; letter-spacing: 1.1px; background: transparent;"
        )
        self.mini_wave = _MiniWaveformWidget(self)

        hdr.addWidget(dot)
        hdr.addWidget(title)
        hdr.addStretch(1)
        hdr.addWidget(self.state_badge_lbl)
        hdr.addWidget(self.mini_wave)
        root.addLayout(hdr)

        # Hairline glowing horizontal rule under header
        sep = QFrame(self)
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: rgba(38, 230, 255, 0.28); border: none;")
        root.addWidget(sep)

        # Virtualized chat stream
        self.view = _TranscriptListView(self)
        self.view.setModel(self.model)
        self.view.setItemDelegate(self.delegate)
        self.view.setUniformItemSizes(False)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._open_context_menu)
        self.view.setStyleSheet("QListView { background: transparent; border: none; outline: none; }")
        self.view.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        root.addWidget(self.view, 1)

        # Bottom separator & docked command bar (matches reference panel footer)
        sep_bot = QFrame(self)
        sep_bot.setFixedHeight(1)
        sep_bot.setStyleSheet("background-color: rgba(38, 230, 255, 0.25); border: none;")
        root.addWidget(sep_bot)

        cmd_box = QFrame(self)
        cmd_box.setObjectName("ConvCommandBox")
        cmd_box.setStyleSheet(
            "QFrame#ConvCommandBox {"
            "  background-color: rgba(4, 14, 32, 0.88);"
            f"  border: 1px solid {PALETTE.border_subtle};"
            "  border-radius: 6px;"
            "}"
        )
        cmd_lay = QHBoxLayout(cmd_box)
        cmd_lay.setContentsMargins(6, 4, 5, 4)
        cmd_lay.setSpacing(6)

        self.command_input = QLineEdit(cmd_box)
        self.command_input.setPlaceholderText("Type a command...")
        self.command_input.setFixedHeight(30)
        self.command_input.setStyleSheet(
            "QLineEdit {"
            "  background: transparent; border: none;"
            f"  color: {PALETTE.text_primary}; font-size: 12px; padding: 0 4px;"
            "}"
        )
        self.command_input.returnPressed.connect(self._emit_command)

        self.play_send_btn = _PlayTriangleButton(cmd_box)
        self.play_send_btn.clicked.connect(self._emit_command)

        cmd_lay.addWidget(self.command_input, 1)
        cmd_lay.addWidget(self.play_send_btn)
        root.addWidget(cmd_box)

        # Bottom-right "SEND >" action row (cleanly preserves send_btn + right-click copy/clear accessibility)
        foot_row = QHBoxLayout()
        foot_row.setContentsMargins(2, 0, 2, 0)
        foot_row.setSpacing(6)

        self.clear_subtle_btn = QPushButton("CLEAR", self)
        self.clear_subtle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_subtle_btn.setFixedHeight(20)
        self.clear_subtle_btn.setStyleSheet(
            f"QPushButton {{ color: {PALETTE.text_muted}; background: transparent; border: none; font-size: 9px; font-weight: 700; letter-spacing: 0.9px; }}"
            f"QPushButton:hover {{ color: {PALETTE.text_secondary}; }}"
        )
        self.clear_subtle_btn.clicked.connect(self.clear)

        self.copy_subtle_btn = QPushButton("COPY", self)
        self.copy_subtle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_subtle_btn.setFixedHeight(20)
        self.copy_subtle_btn.setStyleSheet(self.clear_subtle_btn.styleSheet())
        self.copy_subtle_btn.clicked.connect(self.copy_all)

        self.send_btn = QPushButton("SEND >", self)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setFixedHeight(22)
        self.send_btn.setStyleSheet(
            "QPushButton {"
            f"  color: #46F0FF; background: transparent; border: none;"
            f"  font-family: {METRICS.font_family_display};"
            "  font-size: 10.5px; font-weight: 700; letter-spacing: 1.2px; padding: 0 4px;"
            "}"
            "QPushButton:hover { color: #FFFFFF; }"
        )
        self.send_btn.clicked.connect(self._emit_command)

        foot_row.addWidget(self.copy_subtle_btn)
        foot_row.addWidget(self.clear_subtle_btn)
        foot_row.addStretch(1)
        foot_row.addWidget(self.send_btn)
        root.addLayout(foot_row)

    def _emit_command(self) -> None:
        text = self.command_input.text().strip()
        if not text:
            return
        self.command_input.clear()
        if self.on_command_submit:
            self.on_command_submit(text)

    def set_state_badge(self, state: str, level: float = 0.15, dt_sec: float = 0.03) -> None:
        st = str(state or "ONLINE").strip().upper()
        if st == "IDLE":
            label = "ONLINE"
        elif st == "CALL":
            label = "LIVE CALL"
        else:
            label = st
        if self.state_badge_lbl.text() != label:
            self.state_badge_lbl.setText(label)
        active = st in ("LISTENING", "SPEAKING", "THINKING", "CALL")
        self.mini_wave.pulse(dt_sec, level, active)

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
        self._apply_batch([cleaned])

    def _flush_pending_batch(self) -> None:
        with self._lock:
            if not self._pending_raw:
                return
            batch = self._pending_raw[:]
            self._pending_raw.clear()
        self._apply_batch(batch)

    def _apply_batch(self, batch: list[str]) -> None:
        added = self.model.append_batch(batch)
        if added > 0 and self._auto_scroll:
            self.view.scrollToBottom()

    def flush_now(self) -> None:
        if self._batch_timer.isActive():
            self._batch_timer.stop()
        self._flush_pending_batch()

    def pending_queue_size(self) -> int:
        with self._lock:
            return len(self._pending_raw)

    def message_count(self) -> int:
        return self.model.rowCount()

    def clear(self) -> None:
        with self._lock:
            self._pending_raw.clear()
        self.model.clear()

    def copy_all(self) -> None:
        lines = [f"[{m.timestamp}] {m.prefix}: {m.body}" for m in self.model.messages()]
        cb = QApplication.clipboard()
        if cb and lines:
            cb.setText("\n".join(lines))

    def _open_context_menu(self, pos) -> None:
        menu = QMenu(self)
        act_copy = menu.addAction("Copy all messages")
        act_clear = menu.addAction("Clear transcript")
        chosen = menu.exec(self.view.viewport().mapToGlobal(pos))
        if chosen == act_copy:
            self.copy_all()
        elif chosen == act_clear:
            self.clear()
