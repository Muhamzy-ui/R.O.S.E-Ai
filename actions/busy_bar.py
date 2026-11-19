"""
R.O.S.E "BUSY BAR" 72x16 LED Dot-Matrix Desk Display & Autonomous Local API Controller.
Inspired by Huw Prosser's (@huwprosser) Busy Bar TikTok video:
- Displays an authentic 72x16 glowing red/orange LED dot-matrix "BUSY BAR" widget on the Windows desktop
  (supporting custom text like 'Hello, World!', status banners, and live Pomodoro/exam countdown timers).
- Also sends the real HTTP POST payload to a physical Flipper Busy Bar (http://10.0.4.20/api/display/draw)
  or any local network IoT device if connected!
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
import requests


# 5x7 pixel bitmap font for authentic 72x16 LED dot-matrix rendering
FONT_5X7 = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11100", "10010", "10001", "10001", "10001", "10010", "11100"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "J": ["00111", "00010", "00010", "00010", "10010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "10101", "01010"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "10000", "11110", "00001", "00001", "11110"],
    "6": ["01110", "10000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00001", "01110"],
    "!": ["00100", "00100", "00100", "00100", "00100", "00000", "00100"],
    ":": ["00000", "00100", "00100", "00000", "00100", "00100", "00000"],
    ",": ["00000", "00000", "00000", "00000", "00100", "00100", "01000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "00100", "00100"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}


class RoseBusyBarWindow:
    """72x16 LED Dot-Matrix Desk Gadget Window (Plus Physical Busy Bar HTTP Client)."""

    COLS = 72
    ROWS = 16

    def __init__(self, root: tk.Tk):
        self.root = root
        self.win: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self.running = False
        self.text = "Hello, World!"
        self.color = "#ff3b1f"
        self.countdown_end: float | None = None
        self.scroll_x = 0

    def show_message(self, text: str = "Hello, World!", color: str = "#ff3b1f", countdown_sec: int = 0) -> None:
        self.text = (text or "Hello, World!").strip()
        self.color = color or "#ff3b1f"
        if countdown_sec > 0:
            self.countdown_end = time.time() + countdown_sec
        else:
            self.countdown_end = None
        self.scroll_x = 0
        from ui.tk_bridge import ensure_tk_root, schedule_tk_call

        tk_root = ensure_tk_root(self.root)
        if tk_root is not None:
            self.root = tk_root
        schedule_tk_call(self.root, self._open_on_main)

    def close(self) -> None:
        self.running = False
        if self.win and self.win.winfo_exists():
            from ui.tk_bridge import schedule_tk_call

            schedule_tk_call(self.root, self.win.destroy)
        self.win = None

    def _open_on_main(self):
        if self.win and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            return

        self.win = tk.Toplevel(self.root)
        self.win.title("R.O.S.E — BUSY BAR (72×16 LED MATRIX)")
        sw = self.root.winfo_screenwidth()
        w, h = 640, 165
        x = (sw - w) // 2
        y = 28
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.win.configure(bg="#181a1f")
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        # Top hardware casing bar (white/grey body + orange rotary dial like physical Busy Bar!)
        hdr = tk.Frame(self.win, bg="#d8dde6", height=28)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)
        tk.Label(
            hdr,
            text="  ● BUSY BAR  ·  72×16 LED MATRIX  ·  API /api/display/draw",
            fg="#1a1c22",
            bg="#d8dde6",
            font=("Consolas", 9, "bold"),
        ).pack(side="left", padx=6)
        tk.Button(
            hdr,
            text="✕",
            command=self.close,
            bg="#ff4500",
            fg="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=8,
            cursor="hand2",
            font=("Consolas", 9, "bold"),
        ).pack(side="right", padx=6, pady=3)

        self.canvas = tk.Canvas(self.win, bg="#050507", highlightthickness=2, highlightbackground="#2c303a")
        self.canvas.pack(fill="both", expand=True, padx=10, pady=10)

        self.running = True
        self._render_matrix()

    def _text_to_bitmap(self, msg: str) -> list[list[int]]:
        cols_list: list[list[int]] = []
        for ch in msg.upper():
            glyph = FONT_5X7.get(ch, FONT_5X7[" "])
            for col_idx in range(5):
                col_bits = [1 if glyph[row_idx][col_idx] == "1" else 0 for row_idx in range(7)]
                cols_list.append(col_bits)
            cols_list.append([0] * 7)  # 1px spacing
        return cols_list

    def _render_matrix(self):
        if not self.running or not self.win or not self.win.winfo_exists() or not self.canvas:
            return

        c = self.canvas
        c.delete("all")
        cw = max(580, c.winfo_width())
        ch = max(100, c.winfo_height())

        display_str = self.text
        if self.countdown_end is not None:
            rem = max(0, int(self.countdown_end - time.time()))
            mins, secs = divmod(rem, 60)
            display_str = f"{self.text} {mins:02d}:{secs:02d}"

        bmp_cols = self._text_to_bitmap(display_str)
        total_bmp_w = len(bmp_cols)

        cell_w = cw / self.COLS
        cell_h = ch / self.ROWS
        dot_r = min(cell_w, cell_h) * 0.38

        # Center short text or scroll long text
        if total_bmp_w <= self.COLS - 4:
            offset_x = (self.COLS - total_bmp_w) // 2
        else:
            offset_x = -self.scroll_x
            self.scroll_x = (self.scroll_x + 1) % (total_bmp_w + self.COLS)

        row_start = (self.ROWS - 7) // 2

        for gx in range(self.COLS):
            for gy in range(self.ROWS):
                cx = (gx + 0.5) * cell_w
                cy = (gy + 0.5) * cell_h
                lit = False
                bmp_x = gx - offset_x
                bmp_y = gy - row_start
                if 0 <= bmp_x < total_bmp_w and 0 <= bmp_y < 7:
                    lit = bool(bmp_cols[bmp_x][bmp_y])

                fill_col = self.color if lit else "#121318"
                c.create_oval(cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r, fill=fill_col, outline="")

        self.win.after(85, self._render_matrix)


_BUSY_BAR_INSTANCE: RoseBusyBarWindow | None = None


def init_busy_bar(root: tk.Tk) -> RoseBusyBarWindow:
    global _BUSY_BAR_INSTANCE
    _BUSY_BAR_INSTANCE = RoseBusyBarWindow(root)
    return _BUSY_BAR_INSTANCE


def busy_bar_control(
    action: str = "display",
    text: str = "Hello, World!",
    countdown_minutes: int = 0,
    device_ip: str = "10.0.4.20",
) -> str:
    """
    Controls both the on-screen 72x16 LED Matrix Busy Bar AND any physical Busy Bar over HTTP API (/api/display/draw).
    - action='display': Displays custom text (e.g. 'Hello, World!', 'BUSY // CODING', 'DO NOT DISTURB')
    - action='timer': Starts a live countdown timer on the Busy Bar LED matrix
    - action='close': Closes the Busy Bar display
    """
    global _BUSY_BAR_INSTANCE
    act = (action or "display").strip().lower()

    if act == "close":
        if _BUSY_BAR_INSTANCE:
            _BUSY_BAR_INSTANCE.close()
        return "Closed the Busy Bar display."

    sec = max(0, int(countdown_minutes or 0) * 60)
    if _BUSY_BAR_INSTANCE:
        _BUSY_BAR_INSTANCE.show_message(text=text, countdown_sec=sec)

    # Also attempt to push to a physical Busy Bar over USB virtual LAN (10.0.4.20) or specified IP
    hw_note = ""
    if device_ip:
        payload = {
            "app_id": "rose_ai",
            "elements": [
                {
                    "id": "main_text",
                    "type": "text",
                    "text": text,
                    "x": 36,
                    "y": 8,
                    "align": "center",
                    "font": "medium",
                    "color": "#FF3B1F",
                }
            ],
        }
        try:
            r = requests.post(
                f"http://{device_ip}/api/display/draw",
                json=payload,
                timeout=1.2,
            )
            if r.ok:
                hw_note = f" Also pushed payload to physical Busy Bar at {device_ip}!"
        except Exception:
            pass

    if sec > 0:
        return f"Displayed '{text}' with a {countdown_minutes}-minute countdown on the 72x16 LED Busy Bar!{hw_note}"
    return f"Displayed '{text}' on the 72x16 LED Busy Bar!{hw_note}"

