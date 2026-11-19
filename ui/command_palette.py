"""Ctrl+K Command Palette overlay for R.O.S.E PySide6 UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.theme import PALETTE


@dataclass
class PaletteCommand:
    id: str
    title: str
    category: str
    shortcut: str = ""
    keywords: str = ""
    callback: Optional[Callable[[], Any]] = None


class CommandPaletteDialog(QDialog):
    """Centered translucent quick launcher activated with Ctrl+K."""

    def __init__(
        self,
        commands: Optional[list[PaletteCommand]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._commands: list[PaletteCommand] = list(commands or [])
        self._filtered: list[PaletteCommand] = list(self._commands)

        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedWidth(540)
        self.setMaximumHeight(440)
        self.setWindowTitle("R.O.S.E Command Palette")

        self._build_ui()
        self.search_input.installEventFilter(self)
        self._populate_list("")

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 14)
        outer.setSpacing(10)

        top = QHBoxLayout()
        badge = QLabel("COMMAND PALETTE", self)
        badge.setStyleSheet(
            f"font-size: 10px; font-weight: 700; letter-spacing: 1.5px; color: {PALETTE.accent_cyan};"
        )
        hint = QLabel("Enter to run  |  Esc to close", self)
        hint.setStyleSheet(f"font-size: 11px; color: {PALETTE.text_muted};")
        top.addWidget(badge)
        top.addStretch(1)
        top.addWidget(hint)
        outer.addLayout(top)

        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("Type a command or action (mute, call, jobs, debug, settings...)")
        self.search_input.setFixedHeight(40)
        self.search_input.setStyleSheet(
            "QLineEdit {"
            "  background-color: rgba(10, 15, 26, 0.92);"
            f"  color: {PALETTE.text_primary};"
            "  border: 1px solid rgba(34, 211, 238, 0.42);"
            "  border-radius: 10px;"
            "  padding: 0 12px;"
            "  font-size: 13px;"
            "}"
        )
        self.search_input.textChanged.connect(self._populate_list)
        outer.addWidget(self.search_input)

        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setStyleSheet(
            "QListWidget {"
            "  background: transparent;"
            "  border: none;"
            "  outline: none;"
            "}"
            "QListWidget::item {"
            "  background-color: rgba(17, 24, 39, 0.55);"
            "  border: 1px solid rgba(255, 255, 255, 0.06);"
            "  border-radius: 10px;"
            "  padding: 6px 10px;"
            "  margin-bottom: 4px;"
            "}"
            "QListWidget::item:selected {"
            "  background-color: rgba(34, 211, 238, 0.18);"
            "  border: 1px solid rgba(34, 211, 238, 0.48);"
            "}"
        )
        self.list_widget.itemActivated.connect(self._run_selected_item)
        outer.addWidget(self.list_widget, 1)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(rect, 18.0, 18.0)
        painter.fillPath(path, QColor(12, 18, 32, 242))
        painter.setPen(QPen(QColor(139, 92, 246, 110), 1.0))
        painter.drawPath(path)

    def set_commands(self, commands: list[PaletteCommand]) -> None:
        self._commands = list(commands or [])
        self._populate_list(self.search_input.text())

    def commands(self) -> list[PaletteCommand]:
        return list(self._commands)

    def _populate_list(self, query: str) -> None:
        q = str(query or "").strip().lower()
        tokens = [tok for tok in q.split() if tok]
        self._filtered = []
        for cmd in self._commands:
            haystack = f"{cmd.title} {cmd.category} {cmd.shortcut} {cmd.keywords}".lower()
            if all(tok in haystack for tok in tokens):
                self._filtered.append(cmd)

        self.list_widget.clear()
        for idx, cmd in enumerate(self._filtered):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, idx)
            item.setSizeHint(QSize(0, 42))

            row = QWidget(self.list_widget)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 4, 10, 4)
            rl.setSpacing(10)

            cat_lbl = QLabel(cmd.category.upper(), row)
            cat_lbl.setStyleSheet(
                f"font-size: 9px; font-weight: 700; color: {PALETTE.accent_cyan};"
                "background-color: rgba(38, 230, 255, 0.16); border-radius: 4px; padding: 2px 6px;"
            )
            title_lbl = QLabel(cmd.title, row)
            title_lbl.setStyleSheet(f"font-size: 12px; font-weight: 500; color: {PALETTE.text_primary};")

            rl.addWidget(cat_lbl)
            rl.addWidget(title_lbl, 1)
            if cmd.shortcut:
                sc_lbl = QLabel(cmd.shortcut, row)
                sc_lbl.setStyleSheet(
                    f"font-size: 10px; color: {PALETTE.text_secondary};"
                    "background-color: rgba(255,255,255,0.06); border-radius: 5px; padding: 2px 6px;"
                )
                rl.addWidget(sc_lbl)

            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, row)

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _run_selected_item(self, item: Optional[QListWidgetItem] = None) -> None:
        cur = item or self.list_widget.currentItem()
        if cur is None:
            return
        idx = cur.data(Qt.ItemDataRole.UserRole)
        if not isinstance(idx, int) or idx < 0 or idx >= len(self._filtered):
            return
        cmd = self._filtered[idx]
        self.accept()
        if cmd.callback is not None:
            cmd.callback()

    def execute_by_id(self, command_id: str) -> bool:
        for cmd in self._commands:
            if cmd.id == command_id:
                self.accept()
                if cmd.callback is not None:
                    cmd.callback()
                return True
        return False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.search_input and isinstance(event, QKeyEvent):
            if event.type() == QEvent.Type.KeyPress:
                key = event.key()
                if key == Qt.Key.Key_Down:
                    cnt = self.list_widget.count()
                    if cnt > 0:
                        nxt = (self.list_widget.currentRow() + 1) % cnt
                        self.list_widget.setCurrentRow(nxt)
                    return True
                if key == Qt.Key.Key_Up:
                    cnt = self.list_widget.count()
                    if cnt > 0:
                        prv = (self.list_widget.currentRow() - 1) % cnt
                        self.list_widget.setCurrentRow(prv)
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self._run_selected_item()
                    return True
                if key == Qt.Key.Key_Escape:
                    self.reject()
                    return True
        return super().eventFilter(watched, event)

    def open_centered(self, anchor: Optional[QWidget] = None) -> None:
        self.search_input.clear()
        self._populate_list("")
        if anchor is not None:
            geo = anchor.frameGeometry()
            x = geo.center().x() - (self.width() // 2)
            y = geo.top() + max(60, int(geo.height() * 0.16))
            self.move(x, y)
        self.show()
        self.raise_()
        self.search_input.setFocus()


__all__ = ["PaletteCommand", "CommandPaletteDialog"]

