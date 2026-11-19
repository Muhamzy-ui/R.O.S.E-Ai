"""
R.O.S.E (Project Mirror) - PySide6 Job Command Center Window (Phase 3)

Built with the shared Phase 8 modern visual system (`ui/theme.py`):
- Atmospheric dark-navy-to-violet surface + rounded 16px translucent glass cards
- Zero blocking network/LLM calls on the Qt UI thread (worker threads + Signals)
- Source Health strip (shows "0 — check feed" on empty feeds, "disabled" for Reddit without PRAW)
- Ranked job feed: Top 5 >= 70% pinned at top, <40% hidden behind "Show all", excluded hidden by default
- Real job-type classification only (no fake "QUICK FIX"; hidden when unknown)
- Application Tracker (4-day follow-up alarms), Analytics, and Custom Gig Pitch Drafter
"""

from __future__ import annotations

import datetime as dt
import os
import re
import socket
import sys
import threading
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui.theme import PALETTE, build_shared_qss

SINGLE_INSTANCE_PORT = 48215


class _BackendBridge(QObject):
    """Thread-safe Qt Signals bridging background scan / pitch threads to GUI thread."""
    scan_finished = Signal(list, list)       # ranked_jobs, source_health
    scan_failed = Signal(str)
    pitch_ready = Signal(object, str)        # button_ref, pitch_text
    custom_pitch_ready = Signal(str)         # pitch_text


def _badge_label(text: str, fg: str, bg: str, border: str = "transparent") -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {fg}; background-color: {bg}; border: 1px solid {border}; "
        f"border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 600;"
    )
    return lbl


class JobCenterWindow(QMainWindow):
    """Modern PySide6 Job Command Center sharing the Phase 8 design system."""

    def __init__(self, backend: Any = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._backend = backend
        if self._backend is None:
            from actions.job_command_center import get_job_command_center
            self._backend = get_job_command_center()

        self._bridge = _BackendBridge()
        self._bridge.scan_finished.connect(self._on_scan_finished)
        self._bridge.scan_failed.connect(self._on_scan_failed)
        self._bridge.pitch_ready.connect(self._on_pitch_ready)
        self._bridge.custom_pitch_ready.connect(self._on_custom_pitch_ready)

        self._drag_pos = None
        self._current_tab = "radar"
        self._filter_type = "all"
        self._filter_text = ""
        self._show_low_score = False
        self._show_excluded = False
        self._tracker_filter = "all"

        self._ranked_jobs: list[dict[str, Any]] = list(getattr(self._backend, "cached_radar", []) or [])
        self._source_health: list[dict[str, Any]] = list(getattr(self._backend, "source_health", []) or [])

        self._setup_window()
        self._build_ui()
        self._render_health_strip()
        self._render_radar_cards()
        self._update_followup_pill()
        self._start_auto_refresh_timer()

        # Kick off warm background refresh on launch if no results cached yet
        if not self._ranked_jobs:
            QTimer.singleShot(150, lambda: self._trigger_scan(force=False))

    def _setup_window(self) -> None:
        self.setWindowTitle("R.O.S.E Job Command Center")
        self.setMinimumSize(940, 650)
        self.resize(1100, 760)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(build_shared_qss())

    def _build_ui(self) -> None:
        outer = QWidget()
        outer.setAttribute(Qt.WA_TranslucentBackground, True)
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(10, 10, 10, 10)

        shell = QFrame()
        shell.setObjectName("RoseWindowShell")
        shell_lay = QVBoxLayout(shell)
        shell_lay.setContentsMargins(18, 14, 18, 16)
        shell_lay.setSpacing(12)
        outer_lay.addWidget(shell)
        self.setCentralWidget(outer)

        # ── 1. Custom Frameless Title Bar ────────────────────────────────────
        title_bar = QFrame()
        title_bar.setStyleSheet("background: transparent;")
        tb_lay = QHBoxLayout(title_bar)
        tb_lay.setContentsMargins(2, 0, 2, 2)
        tb_lay.setSpacing(10)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {PALETTE.accent_cyan}; font-size: 13px;")
        tb_lay.addWidget(dot)

        title_lbl = QLabel("R.O.S.E  ·  Job Command Center")
        title_lbl.setObjectName("TitleHeading")
        tb_lay.addWidget(title_lbl)

        self._followup_pill = _badge_label("", PALETTE.warning, PALETTE.warning_soft, "rgba(245,158,11,0.3)")
        self._followup_pill.hide()
        tb_lay.addWidget(self._followup_pill)

        tb_lay.addStretch(1)

        self._scan_status_lbl = QLabel("Ready")
        self._scan_status_lbl.setObjectName("MutedLabel")
        tb_lay.addWidget(self._scan_status_lbl)

        self._scan_btn = QPushButton("↻ Scan Now")
        self._scan_btn.setObjectName("PrimaryBtn")
        self._scan_btn.setCursor(Qt.PointingHandCursor)
        self._scan_btn.clicked.connect(lambda: self._trigger_scan(force=True))
        tb_lay.addWidget(self._scan_btn)

        for sym, tip, slot in (
            ("─", "Minimize", self.showMinimized),
            ("□", "Maximize / Restore", self._toggle_maximize),
            ("✕", "Close", self.close),
        ):
            wbtn = QPushButton(sym)
            wbtn.setObjectName("GlassBtn")
            wbtn.setFixedSize(30, 28)
            wbtn.setToolTip(tip)
            wbtn.setCursor(Qt.PointingHandCursor)
            wbtn.clicked.connect(slot)
            tb_lay.addWidget(wbtn)

        shell_lay.addWidget(title_bar)

        # ── 2. Source Health Strip ───────────────────────────────────────────
        self._health_frame = QFrame()
        self._health_frame.setObjectName("GlassPanel")
        self._health_lay = QHBoxLayout(self._health_frame)
        self._health_lay.setContentsMargins(12, 8, 12, 8)
        self._health_lay.setSpacing(8)
        shell_lay.addWidget(self._health_frame)

        # ── 3. Navigation Tabs & Live Chrome Shortcuts ───────────────────────
        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        self._nav_btns: dict[str, QPushButton] = {}
        tabs = (
            ("radar", "Ranked Radar"),
            ("tracker", "Application Tracker"),
            ("analytics", "Analytics"),
            ("custom", "Paste Custom Gig"),
        )
        for key, label in tabs:
            btn = QPushButton(label)
            btn.setObjectName("NavTabBtn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("active", key == "radar")
            btn.clicked.connect(lambda _chk=False, k=key: self._switch_tab(k))
            nav_row.addWidget(btn)
            self._nav_btns[key] = btn

        nav_row.addStretch(1)

        # Chrome live search quick pills (no Interview button per Phase 8)
        for lbl, plat in (("Google ATS", "google"), ("HackerNews", "hackernews"), ("Upwork", "upwork")):
            sbtn = QPushButton(f"↗ {lbl}")
            sbtn.setObjectName("GlassBtn")
            sbtn.setCursor(Qt.PointingHandCursor)
            sbtn.clicked.connect(lambda _c=False, p=plat: self._launch_chrome_search(p))
            nav_row.addWidget(sbtn)

        shell_lay.addLayout(nav_row)

        # ── 4. Stacked Tab Pages ─────────────────────────────────────────────
        self._stack = QStackedWidget()
        shell_lay.addWidget(self._stack, 1)

        self._radar_page = self._build_radar_page()
        self._tracker_page = self._build_tracker_page()
        self._analytics_page = self._build_analytics_page()
        self._custom_page = self._build_custom_page()

        self._stack.addWidget(self._radar_page)
        self._stack.addWidget(self._tracker_page)
        self._stack.addWidget(self._analytics_page)
        self._stack.addWidget(self._custom_page)

        self._update_followup_pill()

    # ── Page 1: Ranked Radar ─────────────────────────────────────────────────

    def _build_radar_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(10)

        ctrl = QFrame()
        ctrl.setObjectName("GlassPanel")
        c_lay = QHBoxLayout(ctrl)
        c_lay.setContentsMargins(12, 8, 12, 8)
        c_lay.setSpacing(8)

        self._type_btns: dict[str, QPushButton] = {}
        for k, lbl in (
            ("all", "All"),
            ("Full-Time", "Full-Time"),
            ("Contract", "Contract"),
            ("Freelance", "Freelance"),
            ("Part-Time", "Part-Time"),
        ):
            b = QPushButton(lbl)
            b.setObjectName("GlassBtn")
            b.setProperty("active", k == "all")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, tk=k: self._set_type_filter(tk))
            c_lay.addWidget(b)
            self._type_btns[k] = b

        c_lay.addSpacing(8)
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search skills, company, title...")
        self._search_input.setMinimumWidth(220)
        self._search_input.textChanged.connect(self._on_search_changed)
        c_lay.addWidget(self._search_input, 1)

        self._chk_show_low = QCheckBox("Show all (<40%)")
        self._chk_show_low.stateChanged.connect(self._on_toggle_filters)
        c_lay.addWidget(self._chk_show_low)

        self._chk_show_excl = QCheckBox("Show excluded")
        self._chk_show_excl.stateChanged.connect(self._on_toggle_filters)
        c_lay.addWidget(self._chk_show_excl)

        lay.addWidget(ctrl)

        self._radar_summary_lbl = QLabel("")
        self._radar_summary_lbl.setObjectName("MutedLabel")
        lay.addWidget(self._radar_summary_lbl)

        self._radar_scroll = QScrollArea()
        self._radar_scroll.setWidgetResizable(True)
        self._radar_cards_host = QWidget()
        self._radar_cards_lay = QVBoxLayout(self._radar_cards_host)
        self._radar_cards_lay.setContentsMargins(0, 0, 6, 8)
        self._radar_cards_lay.setSpacing(10)
        self._radar_scroll.setWidget(self._radar_cards_host)
        lay.addWidget(self._radar_scroll, 1)
        return page

    # ── Page 2: Application Tracker ──────────────────────────────────────────

    def _build_tracker_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(10)

        ctrl = QFrame()
        ctrl.setObjectName("GlassPanel")
        c_lay = QHBoxLayout(ctrl)
        c_lay.setContentsMargins(12, 8, 12, 8)
        c_lay.setSpacing(6)

        self._tr_btns: dict[str, QPushButton] = {}
        for k, lbl in (
            ("all", "All"),
            ("applied", "Applied"),
            ("follow_up_due", "Follow-Up Due"),
            ("interviewing", "Interviewing"),
            ("offer", "Offers"),
        ):
            b = QPushButton(lbl)
            b.setObjectName("GlassBtn")
            b.setProperty("active", k == "all")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, tk=k: self._set_tracker_filter(tk))
            c_lay.addWidget(b)
            self._tr_btns[k] = b
        c_lay.addStretch(1)
        lay.addWidget(ctrl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._tracker_host = QWidget()
        self._tracker_lay = QVBoxLayout(self._tracker_host)
        self._tracker_lay.setContentsMargins(0, 0, 6, 8)
        self._tracker_lay.setSpacing(10)
        scroll.setWidget(self._tracker_host)
        lay.addWidget(scroll, 1)
        return page

    # ── Page 3: Analytics ────────────────────────────────────────────────────

    def _build_analytics_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(12)

        self._analytics_host = QWidget()
        self._analytics_lay = QVBoxLayout(self._analytics_host)
        self._analytics_lay.setContentsMargins(0, 0, 0, 0)
        self._analytics_lay.setSpacing(12)
        lay.addWidget(self._analytics_host)
        lay.addStretch(1)
        return page

    # ── Page 4: Paste Custom Gig ─────────────────────────────────────────────

    def _build_custom_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(10)

        box = QFrame()
        box.setObjectName("GlassPanel")
        b_lay = QVBoxLayout(box)
        b_lay.setContentsMargins(16, 14, 16, 14)
        b_lay.setSpacing(10)

        h = QLabel("Custom Role / Direct Client Gig Analyzer")
        h.setObjectName("SectionHeading")
        b_lay.addWidget(h)

        row = QHBoxLayout()
        self._cg_title = QLineEdit()
        self._cg_title.setPlaceholderText("Role title (e.g. Python / Django Backend Developer)")
        self._cg_comp = QLineEdit()
        self._cg_comp.setPlaceholderText("Company / Client name")
        self._cg_pay = QLineEdit()
        self._cg_pay.setPlaceholderText("Pay (optional, e.g. $2,500/mo)")
        row.addWidget(self._cg_title, 2)
        row.addWidget(self._cg_comp, 1)
        row.addWidget(self._cg_pay, 1)
        b_lay.addLayout(row)

        self._cg_desc = QTextEdit()
        self._cg_desc.setPlaceholderText("Paste the job description or client message here...")
        self._cg_desc.setFixedHeight(140)
        b_lay.addWidget(self._cg_desc)

        self._cg_btn = QPushButton("Draft Profile-Grounded Pitch, Copy & Track")
        self._cg_btn.setObjectName("PrimaryBtn")
        self._cg_btn.setCursor(Qt.PointingHandCursor)
        self._cg_btn.clicked.connect(self._on_submit_custom_gig)
        b_lay.addWidget(self._cg_btn, 0, Qt.AlignLeft)

        self._cg_out = QTextEdit()
        self._cg_out.setReadOnly(True)
        self._cg_out.setPlaceholderText("Generated grounded pitch will appear here...")
        b_lay.addWidget(self._cg_out, 1)

        lay.addWidget(box, 1)
        return page

    # ── Render Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _clear_layout(lay: QVBoxLayout | QHBoxLayout) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    @staticmethod
    def _refresh_style(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _render_health_strip(self) -> None:
        self._clear_layout(self._health_lay)
        lbl = QLabel("Sources:")
        lbl.setObjectName("MutedLabel")
        self._health_lay.addWidget(lbl)

        health_items = self._source_health or [
            {"source": s, "status": "idle", "message": "not scanned yet"}
            for s in ("RemoteOK", "WeWorkRemotely", "HackerNews", "Himalayas", "Jobspresso", "Reddit")
        ]
        for h in health_items:
            src = h.get("source", "?")
            st = h.get("status", "idle")
            msg = h.get("message", "")
            last_ok = (h.get("last_success") or "")[11:16]
            if st == "ok":
                fg, bg, bdr = PALETTE.success, PALETTE.success_soft, "rgba(16,185,129,0.28)"
                text = f"{src}: {msg}" + (f" ({last_ok})" if last_ok else "")
            elif st == "empty":
                fg, bg, bdr = PALETTE.warning, PALETTE.warning_soft, "rgba(245,158,11,0.32)"
                text = f"{src}: 0 — check feed"
            elif st == "disabled":
                fg, bg, bdr = PALETTE.text_muted, "rgba(100,116,139,0.12)", PALETTE.border_subtle
                text = f"{src}: disabled"
            elif st == "error":
                fg, bg, bdr = PALETTE.danger, PALETTE.danger_soft, "rgba(239,68,68,0.28)"
                text = f"{src}: error"
            else:
                fg, bg, bdr = PALETTE.text_secondary, "rgba(255,255,255,0.04)", PALETTE.border_subtle
                text = f"{src}: pending"
            badge = _badge_label(text, fg, bg, bdr)
            if msg:
                badge.setToolTip(msg)
            self._health_lay.addWidget(badge)

        self._health_lay.addStretch(1)

    def _render_radar_cards(self) -> None:
        self._clear_layout(self._radar_cards_lay)

        q = self._filter_text.strip().lower()
        visible: list[dict[str, Any]] = []
        hidden_excl_cnt = 0
        hidden_low_cnt = 0

        for j in self._ranked_jobs:
            if j.get("blacklisted") or j.get("eligibility_status") == "excluded":
                if not self._show_excluded:
                    hidden_excl_cnt += 1
                    continue
            if j.get("hidden_low_score") and not self._show_low_score:
                hidden_low_cnt += 1
                continue
            if self._filter_type != "all" and j.get("job_type", "") != self._filter_type:
                continue
            if q:
                hay = f"{j.get('title', '')} {j.get('company', '')} {j.get('match_reason', '')} {j.get('platform', '')} {j.get('snippet', '')}".lower()
                if q not in hay:
                    continue
            visible.append(j)

        top_picks_cnt = sum(1 for j in visible if j.get("is_top_pick"))
        self._radar_summary_lbl.setText(
            f"Showing {len(visible)} ranked jobs ({top_picks_cnt} Top Picks ≥70%) · "
            f"Hidden: {hidden_low_cnt} low-match (<40%), {hidden_excl_cnt} excluded/blacklisted"
        )

        if not visible:
            empty = QFrame()
            empty.setObjectName("GlassPanel")
            el = QVBoxLayout(empty)
            el.setContentsMargins(24, 28, 24, 28)
            msg = QLabel("No jobs match the current filters.")
            msg.setObjectName("SectionHeading")
            sub = QLabel("Try clicking 'Scan Now' to fetch live feeds, or enable 'Show all (<40%)'.")
            sub.setObjectName("MutedLabel")
            el.addWidget(msg, 0, Qt.AlignCenter)
            el.addWidget(sub, 0, Qt.AlignCenter)
            self._radar_cards_lay.addWidget(empty)
            self._radar_cards_lay.addStretch(1)
            return

        for job in visible[:80]:
            self._radar_cards_lay.addWidget(self._build_job_card(job))

        self._radar_cards_lay.addStretch(1)

    def _build_job_card(self, job: dict[str, Any]) -> QFrame:
        card = QFrame()
        is_top = bool(job.get("is_top_pick"))
        card.setObjectName("TopPickCard" if is_top else "JobCard")
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(16, 12, 16, 12)
        c_lay.setSpacing(6)

        # Top row: Top Pick / Score / Real Job Type / Title / Platform
        top = QHBoxLayout()
        top.setSpacing(8)

        if is_top:
            top.addWidget(_badge_label("★ TOP PICK", "#FDE68A", "rgba(245,158,11,0.18)", "rgba(245,158,11,0.4)"))

        score = int(job.get("score", 0))
        if score >= 70:
            sc_fg, sc_bg, sc_bdr = PALETTE.success, PALETTE.success_soft, "rgba(16,185,129,0.3)"
        elif score >= 45:
            sc_fg, sc_bg, sc_bdr = PALETTE.accent_cyan, PALETTE.info_soft, "rgba(34,211,238,0.28)"
        else:
            sc_fg, sc_bg, sc_bdr = PALETTE.warning, PALETTE.warning_soft, "rgba(245,158,11,0.28)"
        top.addWidget(_badge_label(f"{score}% Match", sc_fg, sc_bg, sc_bdr))

        jtype = str(job.get("job_type") or "").strip()
        if jtype:
            top.addWidget(_badge_label(jtype, "#DDD6FE", PALETTE.violet_soft, PALETTE.border_violet))

        title_lbl = QLabel(str(job.get("title") or "Untitled"))
        title_lbl.setStyleSheet("font-size: 14px; font-weight: 600; color: #F8FAFC;")
        title_lbl.setWordWrap(True)
        top.addWidget(title_lbl, 1)

        plat_lbl = _badge_label(str(job.get("platform") or "Feed"), PALETTE.text_secondary, "rgba(255,255,255,0.05)", PALETTE.border_subtle)
        top.addWidget(plat_lbl)
        c_lay.addLayout(top)

        # Company & one-line explanation reason
        meta = QLabel(f"{job.get('company', 'Unknown')}  ·  {job.get('match_reason', '')}")
        meta.setStyleSheet(f"color: {PALETTE.accent_cyan}; font-size: 12px; font-weight: 500;")
        meta.setWordWrap(True)
        c_lay.addWidget(meta)

        snip = str(job.get("snippet") or "").strip()
        if snip:
            s_lbl = QLabel(snip[:210] + ("..." if len(snip) > 210 else ""))
            s_lbl.setObjectName("MutedLabel")
            s_lbl.setWordWrap(True)
            c_lay.addWidget(s_lbl)

        # Action row
        act_row = QHBoxLayout()
        act_row.setSpacing(8)

        open_btn = QPushButton("🌐 Open Link")
        open_btn.setObjectName("GlassBtn")
        open_btn.setCursor(Qt.PointingHandCursor)
        open_btn.clicked.connect(lambda _c=False, u=job.get("url", ""): self._open_url(u))
        act_row.addWidget(open_btn)

        pitch_btn = QPushButton("📋 Copy Pitch")
        pitch_btn.setObjectName("GlassBtn")
        pitch_btn.setCursor(Qt.PointingHandCursor)
        pitch_btn.clicked.connect(lambda _c=False, j=job, pb=pitch_btn: self._copy_pitch_async(j, pb))
        act_row.addWidget(pitch_btn)

        tracked = any(a.get("id") == job.get("id") for a in self._backend.get_applications("all"))
        mark_btn = QPushButton("✓ Applied (Undo)" if tracked else "✅ Mark Applied")
        mark_btn.setObjectName("GlassBtn")
        mark_btn.setProperty("active", tracked)
        mark_btn.setCursor(Qt.PointingHandCursor)
        mark_btn.clicked.connect(lambda _c=False, j=job, mb=mark_btn: self._toggle_applied(j, mb))
        act_row.addWidget(mark_btn)

        act_row.addStretch(1)
        c_lay.addLayout(act_row)
        return card

    def _render_tracker_cards(self) -> None:
        self._clear_layout(self._tracker_lay)
        apps = self._backend.get_applications(filter_status=self._tracker_filter)
        if not apps:
            box = QFrame()
            box.setObjectName("GlassPanel")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(20, 24, 20, 24)
            t = QLabel("No tracked applications under this filter.")
            t.setObjectName("SectionHeading")
            bl.addWidget(t, 0, Qt.AlignCenter)
            self._tracker_lay.addWidget(box)
            self._tracker_lay.addStretch(1)
            return

        for app in apps:
            card = QFrame()
            card.setObjectName("JobCard")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(16, 12, 16, 12)
            cl.setSpacing(6)

            st = app.get("status", "applied")
            top = QHBoxLayout()
            if st == "follow_up_due":
                top.addWidget(_badge_label("🔔 FOLLOW-UP DUE", PALETTE.warning, PALETTE.warning_soft, "rgba(245,158,11,0.35)"))
            elif st in ("interviewing", "offer"):
                top.addWidget(_badge_label(st.upper(), PALETTE.success, PALETTE.success_soft, "rgba(16,185,129,0.35)"))
            else:
                top.addWidget(_badge_label(st.upper(), PALETTE.accent_cyan, PALETTE.info_soft, PALETTE.border_accent))

            tl = QLabel(f"{app.get('title', '')} — {app.get('company', '')}")
            tl.setStyleSheet("font-weight: 600; font-size: 14px;")
            top.addWidget(tl, 1)

            dt_lbl = QLabel(f"Applied: {(app.get('date_applied') or '')[:10]}")
            dt_lbl.setObjectName("MutedLabel")
            top.addWidget(dt_lbl)
            cl.addLayout(top)

            sub = QLabel(f"Platform: {app.get('platform', 'Radar')}  ·  Pay: {app.get('pay', 'pay unlisted')}")
            sub.setObjectName("MutedLabel")
            cl.addWidget(sub)

            br = QHBoxLayout()
            if app.get("url"):
                ob = QPushButton("🌐 Open URL")
                ob.setObjectName("GlassBtn")
                ob.clicked.connect(lambda _c=False, u=app["url"]: self._open_url(u))
                br.addWidget(ob)

            fub = QPushButton("✍ Copy Follow-Up Email")
            fub.setObjectName("GlassBtn")
            fub.clicked.connect(lambda _c=False, ar=app, b=fub: self._copy_followup(ar, b))
            br.addWidget(fub)

            for ns, lbl in (("interviewing", "→ Interviewing"), ("offer", "→ Offer")):
                sb = QPushButton(lbl)
                sb.setObjectName("GlassBtn")
                sb.clicked.connect(lambda _c=False, aid=app["id"], st_val=ns: self._update_app_status(aid, st_val))
                br.addWidget(sb)

            br.addStretch(1)
            db = QPushButton("🗑 Delete")
            db.setObjectName("DangerBtn")
            db.clicked.connect(lambda _c=False, aid=app["id"]: self._delete_app(aid))
            br.addWidget(db)
            cl.addLayout(br)
            self._tracker_lay.addWidget(card)

        self._tracker_lay.addStretch(1)

    def _render_analytics_view(self) -> None:
        self._clear_layout(self._analytics_lay)
        stats = self._backend.get_analytics()

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        for label, val, col in (
            ("TOTAL APPLIED", str(stats["total_applied"]), PALETTE.accent_cyan),
            ("INTERVIEWING", str(stats["interviewing"]), PALETTE.success),
            ("CALLBACK RATE", f"{stats['interview_rate_pct']}%", PALETTE.accent_violet),
            ("OFFERS", str(stats["offers"]), PALETTE.success),
            ("FOLLOW-UPS DUE", str(stats["follow_ups_due"]), PALETTE.warning),
        ):
            card = QFrame()
            card.setObjectName("GlassPanel")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 14, 14, 14)
            t = QLabel(label)
            t.setObjectName("MutedLabel")
            v = QLabel(val)
            v.setStyleSheet(f"color: {col}; font-size: 26px; font-weight: 300;")
            cl.addWidget(t)
            cl.addWidget(v)
            kpi_row.addWidget(card, 1)
        self._analytics_lay.addLayout(kpi_row)

        info = QFrame()
        info.setObjectName("GlassPanel")
        il = QVBoxLayout(info)
        il.setContentsMargins(16, 14, 16, 14)
        il.setSpacing(6)
        hdr = QLabel("Platform Conversion & Integrity Status")
        hdr.setObjectName("SectionHeading")
        il.addWidget(hdr)

        bd = stats.get("platform_breakdown") or {}
        if not bd:
            il.addWidget(QLabel("Log an application in Ranked Radar to populate platform metrics."))
        else:
            for plat, cnt in bd.items():
                il.addWidget(QLabel(f"• {plat}: {cnt} application(s)"))

        notice = QLabel(
            "Foreign Agency Radar: Disabled — the legacy list used hardcoded sample entries; "
            "it stays disabled under R.O.S.E truthful-data rules until a real verified directory is configured."
        )
        notice.setObjectName("MutedLabel")
        notice.setWordWrap(True)
        il.addWidget(notice)
        self._analytics_lay.addWidget(info)

    # ── Event & Worker Handlers ──────────────────────────────────────────────

    def _start_auto_refresh_timer(self) -> None:
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(30 * 60 * 1000)  # 30 minutes
        self._refresh_timer.timeout.connect(lambda: self._trigger_scan(force=True))
        self._refresh_timer.start()

    def _trigger_scan(self, force: bool = True) -> None:
        self._scan_btn.setEnabled(False)
        self._scan_status_lbl.setText("Scanning official feeds...")

        def _worker():
            try:
                ranked, health = self._backend.scan_all_channels_sync(force=force)
                self._bridge.scan_finished.emit(ranked, health)
            except Exception as exc:
                self._bridge.scan_failed.emit(str(exc))

        threading.Thread(target=_worker, daemon=True, name="ROSE-QtJobScanner").start()

    def _on_scan_finished(self, ranked: list, health: list) -> None:
        self._ranked_jobs = ranked
        self._source_health = health
        self._scan_btn.setEnabled(True)
        self._scan_status_lbl.setText(f"Updated {dt.datetime.now().strftime('%H:%M')}")
        self._render_health_strip()
        self._render_radar_cards()

    def _on_scan_failed(self, err: str) -> None:
        self._scan_btn.setEnabled(True)
        self._scan_status_lbl.setText(f"Scan error: {err[:36]}")

    def _copy_pitch_async(self, job: dict[str, Any], btn: QPushButton) -> None:
        btn.setEnabled(False)
        btn.setText("Drafting...")

        def _worker():
            pitch = self._backend.generate_pitch(job)
            self._bridge.pitch_ready.emit(btn, pitch)

        threading.Thread(target=_worker, daemon=True, name="ROSE-PitchGen").start()

    def _safe_set_btn_text(self, btn: QPushButton, label: str) -> None:
        try:
            btn.setText(label)
        except RuntimeError:
            pass

    def _on_pitch_ready(self, btn: QPushButton, pitch: str) -> None:
        from actions.job_command_center import _copy_clipboard
        _copy_clipboard(pitch)
        if btn:
            try:
                btn.setEnabled(True)
                btn.setText("✓ Pitch Copied!")
                QTimer.singleShot(2200, lambda b=btn: self._safe_set_btn_text(b, "📋 Copy Pitch"))
            except RuntimeError:
                pass

    def _on_submit_custom_gig(self) -> None:
        title = self._cg_title.text().strip() or "Full Stack / Python / React Developer"
        comp = self._cg_comp.text().strip() or "Client"
        pay = self._cg_pay.text().strip() or "pay unlisted"
        desc = self._cg_desc.toPlainText().strip()
        job_dict = {
            "id": f"custom-{abs(hash(title + comp + desc)) % 1_000_000}",
            "title": title,
            "company": comp,
            "pay": pay,
            "snippet": desc,
            "description": desc,
            "platform": "Custom Lead",
            "url": "",
        }
        self._cg_btn.setEnabled(False)
        self._cg_btn.setText("Generating grounded pitch...")

        def _worker():
            pitch = self._backend.generate_pitch(job_dict)
            self._backend.track_application(job_dict, status="applied", notes=desc[:140])
            from actions.job_command_center import _copy_clipboard
            _copy_clipboard(pitch)
            self._bridge.custom_pitch_ready.emit(pitch)

        threading.Thread(target=_worker, daemon=True, name="ROSE-CustomPitch").start()

    def _on_custom_pitch_ready(self, pitch: str) -> None:
        self._cg_btn.setEnabled(True)
        self._cg_btn.setText("Draft Profile-Grounded Pitch, Copy & Track")
        self._cg_out.setPlainText(f"[COPIED TO CLIPBOARD & LOGGED TO TRACKER]\n\n{pitch}")
        self._update_followup_pill()

    def _toggle_applied(self, job: dict[str, Any], btn: QPushButton) -> None:
        jid = job.get("id")
        existing = self._backend.get_applications("all")
        if any(a.get("id") == jid for a in existing):
            self._backend.delete_application(jid)
            btn.setText("✅ Mark Applied")
            btn.setProperty("active", False)
        else:
            self._backend.track_application(job, status="applied")
            btn.setText("✓ Applied (Undo)")
            btn.setProperty("active", True)
        self._refresh_style(btn)
        self._update_followup_pill()

    def _copy_followup(self, app: dict[str, Any], btn: QPushButton) -> None:
        from actions.job_command_center import _copy_clipboard
        _copy_clipboard(self._backend.generate_follow_up(app))
        btn.setText("✓ Follow-Up Copied!")
        QTimer.singleShot(2000, lambda b=btn: self._safe_set_btn_text(b, "✍ Copy Follow-Up Email"))

    def _update_app_status(self, app_id: str, new_status: str) -> None:
        self._backend.update_application_status(app_id, new_status)
        self._render_tracker_cards()
        self._update_followup_pill()

    def _delete_app(self, app_id: str) -> None:
        self._backend.delete_application(app_id)
        self._render_tracker_cards()
        self._update_followup_pill()

    def _update_followup_pill(self) -> None:
        cnt = self._backend.get_analytics().get("follow_ups_due", 0)
        if cnt > 0:
            self._followup_pill.setText(f"🔔 {cnt} Follow-Up Due")
            self._followup_pill.show()
        else:
            self._followup_pill.hide()

    def select_tab(self, tab_key: str) -> None:
        self._switch_tab(tab_key)

    def _switch_tab(self, tab_key: str) -> None:
        self._current_tab = tab_key
        for k, b in self._nav_btns.items():
            b.setProperty("active", k == tab_key)
            self._refresh_style(b)
        idx = {"radar": 0, "tracker": 1, "analytics": 2, "custom": 3}.get(tab_key, 0)
        self._stack.setCurrentIndex(idx)
        if tab_key == "tracker":
            self._render_tracker_cards()
        elif tab_key == "analytics":
            self._render_analytics_view()

    def _set_type_filter(self, tkey: str) -> None:
        self._filter_type = tkey
        for k, b in self._type_btns.items():
            b.setProperty("active", k == tkey)
            self._refresh_style(b)
        self._render_radar_cards()

    def _on_search_changed(self, text: str) -> None:
        self._filter_text = text
        self._render_radar_cards()

    def _on_toggle_filters(self) -> None:
        self._show_low_score = self._chk_show_low.isChecked()
        self._show_excluded = self._chk_show_excl.isChecked()
        self._render_radar_cards()

    def _set_tracker_filter(self, st_key: str) -> None:
        self._tracker_filter = st_key
        for k, b in self._tr_btns.items():
            b.setProperty("active", k == st_key)
            self._refresh_style(b)
        self._render_tracker_cards()

    def _open_url(self, url: str) -> None:
        from actions.job_command_center import _open_browser_url
        _open_browser_url(url)

    def _launch_chrome_search(self, platform: str) -> None:
        from actions.job_command_center import launch_live_chrome_search
        launch_live_chrome_search(platform, self._filter_text)

    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # Dragging frameless window
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and event.position().y() < 56:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None


def try_focus_existing_window() -> bool:
    """Signals an already-running PySide6 Job Center instance to raise itself."""
    try:
        with socket.create_connection(("127.0.0.1", SINGLE_INSTANCE_PORT), timeout=0.4) as s:
            s.sendall(b"RAISE")
            return True
    except Exception:
        return False


class _FocusSignaler(QObject):
    focus_now = Signal()


def run_standalone() -> int:
    """Entry point when launched as a dedicated Qt process (`python -m ui.job_center`)."""
    if try_focus_existing_window():
        return 0

    app = QApplication.instance() or QApplication(sys.argv)
    win = JobCenterWindow()
    focus_sig = _FocusSignaler()
    focus_sig.focus_now.connect(lambda: (win.showNormal(), win.raise_(), win.activateWindow()))

    # Localhost single-instance listener so F10/voice command raises existing window
    def _listen():
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
            srv.listen(4)
            while True:
                conn, _ = srv.accept()
                conn.close()
                focus_sig.focus_now.emit()
        except Exception:
            pass

    threading.Thread(target=_listen, daemon=True).start()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run_standalone())
