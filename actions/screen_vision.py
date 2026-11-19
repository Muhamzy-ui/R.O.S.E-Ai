"""
R.O.S.E Multi-Monitor Screen Vision Analyzer for Windows.
Supports:
- Reading ALL connected monitors together in crisp per-monitor resolution (`all_monitors` / `both_monitors`)
- Reading a single display (`monitor_1`, `monitor_2`, `monitor_3`)
- Reading only the active foreground window (`active_window`, with clean fallback to monitors)
- Dual backend support: Gemini Vision (3.5/3.1/3.8 Flash) or local LM Studio Vision.
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import io
import json
import mimetypes
import tempfile
import time
from pathlib import Path

import requests
from PIL import Image, ImageGrab, ImageStat

from app_config import get_app_config_value, is_local_backend


VISION_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
)
VISION_MAX_DIMENSION = 2048
VISION_MAX_INLINE_BYTES = 4_500_000

_DPI_SET = False


def _ensure_dpi_aware() -> None:
    global _DPI_SET
    if _DPI_SET:
        return
    _DPI_SET = True
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-Monitor v2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _screen_permission_message() -> str:
    return (
        "Could not capture the Windows screen right now. Make sure the Windows desktop is unlocked "
        "(not on Lock Screen / UAC prompt) and that R.O.S.E is running inside your normal desktop session."
    )


def enumerate_monitors() -> list[dict]:
    """Lists all connected Windows monitors sorted left-to-right cleanly."""
    _ensure_dpi_aware()
    monitors: list[dict] = []
    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(wt.RECT),
        ctypes.c_double,
    )

    def _cb(_hmon, _hdc, lprect, _lparam):
        r = lprect.contents
        w = int(r.right - r.left)
        h = int(r.bottom - r.top)
        if w > 0 and h > 0:
            monitors.append({
                "left": int(r.left),
                "top": int(r.top),
                "right": int(r.right),
                "bottom": int(r.bottom),
                "width": w,
                "height": h,
            })
        return 1

    try:
        ctypes.windll.user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(_cb), 0)
    except Exception:
        pass

    if not monitors:
        try:
            user32 = ctypes.windll.user32
            w = int(user32.GetSystemMetrics(0) or 1920)
            h = int(user32.GetSystemMetrics(1) or 1080)
            monitors.append({"left": 0, "top": 0, "right": w, "bottom": h, "width": w, "height": h})
        except Exception:
            pass

    monitors.sort(key=lambda m: (m["left"], m["top"]))
    for idx, m in enumerate(monitors, start=1):
        m["index"] = idx
        orient = "Portrait" if m["height"] > m["width"] else "Landscape"
        m["label"] = f"Monitor {idx} ({m['width']}x{m['height']} {orient})"
    return monitors


def _grab_bbox_gdi_fallback(left: int, top: int, right: int, bottom: int) -> Image.Image | None:
    """Win32 BitBlt fallback capture for asymmetric multi-monitor arrays where ImageGrab fails."""
    w = int(right - left)
    h = int(bottom - top)
    if w <= 0 or h <= 0:
        return None
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hdc_screen = 0
    hdc_mem = 0
    hbmp = 0
    old_obj = 0
    try:
        hdc_screen = user32.GetDC(0)
        if not hdc_screen:
            return None
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        if not hdc_mem or not hbmp:
            return None
        old_obj = gdi32.SelectObject(hdc_mem, hbmp)
        SRCCOPY = 0x00CC0020
        CAPTUREBLT = 0x40000000
        ok = gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, left, top, SRCCOPY | CAPTUREBLT)
        if not ok:
            return None

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wt.DWORD),
                ("biWidth", wt.LONG),
                ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD),
            ]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # top-down DIB
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB

        buf_len = w * h * 4
        buf = ctypes.create_string_buffer(buf_len)
        rows = gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
        if rows <= 0:
            return None
        return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
    except Exception:
        return None
    finally:
        if old_obj and hdc_mem:
            try:
                gdi32.SelectObject(hdc_mem, old_obj)
            except Exception:
                pass
        if hbmp:
            try:
                gdi32.DeleteObject(hbmp)
            except Exception:
                pass
        if hdc_mem:
            try:
                gdi32.DeleteDC(hdc_mem)
            except Exception:
                pass
        if hdc_screen:
            try:
                user32.ReleaseDC(0, hdc_screen)
            except Exception:
                pass


def _capture_bbox(left: int, top: int, right: int, bottom: int) -> Image.Image | None:
    _ensure_dpi_aware()
    bbox = (int(left), int(top), int(right), int(bottom))
    try:
        return ImageGrab.grab(bbox=bbox, all_screens=True)
    except Exception:
        pass
    try:
        full = ImageGrab.grab(all_screens=True)
        vx = ctypes.windll.user32.GetSystemMetrics(76)
        vy = ctypes.windll.user32.GetSystemMetrics(77)
        return full.crop((bbox[0] - vx, bbox[1] - vy, bbox[2] - vx, bbox[3] - vy))
    except Exception:
        pass
    return _grab_bbox_gdi_fallback(*bbox)


def _get_foreground_window_rect() -> tuple[tuple[int, int, int, int] | None, str, str]:
    _ensure_dpi_aware()
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, "", ""

    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    window_title = buf.value or ""

    owner = ""
    try:
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            import psutil
            owner = psutil.Process(pid.value).name()
        except Exception:
            owner = ""
    except Exception:
        owner = ""

    try:
        rect = wt.RECT()
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wt.HWND(hwnd),
            ctypes.c_uint(9),  # DWMWA_EXTENDED_FRAME_BOUNDS
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr == 0 and (rect.right - rect.left) > 40 and (rect.bottom - rect.top) > 40:
            return (rect.left, rect.top, rect.right, rect.bottom), owner, window_title
    except Exception:
        pass

    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom), owner, window_title


def _capture_targets(target: str) -> tuple[bool, str, list[dict]]:
    """
    Captures one or multiple crisp screenshots according to target:
    - all_monitors / all_screens / both_monitors / both / all: Every connected monitor
    - monitor_1 / monitor_2 / monitor_3: Specific monitor
    - active_window: Foreground window (falls back to Monitor 1 / all monitors cleanly)
    """
    tgt = (target or "all_monitors").strip().lower()
    monitors = enumerate_monitors()
    _, owner, win_title = _get_foreground_window_rect()
    fg_info = " / ".join(p for p in (owner, win_title) if p).strip()

    captures: list[dict] = []

    if tgt == "active_window":
        bbox, owner, win_title = _get_foreground_window_rect()
        if bbox and (bbox[2] - bbox[0]) > 40 and (bbox[3] - bbox[1]) > 40:
            im = _capture_bbox(*bbox)
            if im is not None:
                tmp = tempfile.NamedTemporaryFile(prefix="rose-win-", suffix=".png", delete=False)
                tmp.close()
                im.save(tmp.name, format="PNG")
                captures.append({
                    "image_path": tmp.name,
                    "label": f"Active Window ({fg_info or 'Foreground'})",
                    "owner_name": owner,
                    "window_title": win_title,
                })
                return True, "", captures
        # Graceful fallback to monitors if foreground window unavailable
        tgt = "all_monitors"

    # Specific monitor_N requested
    m_idx = None
    for n in (1, 2, 3, 4):
        if tgt in (f"monitor_{n}", f"monitor{n}", f"screen_{n}", f"screen{n}", str(n)):
            m_idx = n
            break

    selected_monitors = (
        [m for m in monitors if m["index"] == m_idx]
        if m_idx is not None
        else monitors[:3]
    )
    if not selected_monitors:
        selected_monitors = monitors[:1]

    for m in selected_monitors:
        im = _capture_bbox(m["left"], m["top"], m["right"], m["bottom"])
        if im is None:
            continue
        tmp = tempfile.NamedTemporaryFile(prefix=f"rose-mon{m['index']}-", suffix=".png", delete=False)
        tmp.close()
        im.save(tmp.name, format="PNG")
        captures.append({
            "image_path": tmp.name,
            "label": m["label"],
            "owner_name": owner,
            "window_title": win_title,
        })

    if not captures:
        return False, _screen_permission_message(), []
    return True, "", captures


def _image_looks_blank(image_path: Path) -> bool:
    try:
        with Image.open(image_path) as img:
            stat = ImageStat.Stat(img.convert("RGB"))
            max_seen = max(ch[1] for ch in stat.extrema)
            mean_total = sum(stat.mean) / max(1, len(stat.mean))
            return max_seen <= 6 or mean_total <= 2.5
    except Exception:
        return False


def _prepare_image_bytes(image_path: Path) -> tuple[bytes, str]:
    mime_type, _ = mimetypes.guess_type(str(image_path))
    if not mime_type:
        mime_type = "image/png"
    try:
        with Image.open(image_path) as img:
            work = img.copy()
        if work.mode not in {"RGB", "L"}:
            work = work.convert("RGB")
        if max(work.size) > VISION_MAX_DIMENSION:
            work.thumbnail((VISION_MAX_DIMENSION, VISION_MAX_DIMENSION), Image.Resampling.LANCZOS)
        png_buffer = io.BytesIO()
        work.save(png_buffer, format="PNG", optimize=True)
        png_bytes = png_buffer.getvalue()
        if len(png_bytes) <= VISION_MAX_INLINE_BYTES:
            return png_bytes, "image/png"
        jpg_buffer = io.BytesIO()
        rgb = work.convert("RGB") if work.mode != "RGB" else work
        rgb.save(jpg_buffer, format="JPEG", quality=88, optimize=True)
        return jpg_buffer.getvalue(), "image/jpeg"
    except Exception:
        return image_path.read_bytes(), mime_type


def _vision_prompt(query: str, labels: list[str], fg_context: str) -> str:
    user_query = (query or "What is on my screens right now?").strip()
    displays_desc = ", ".join(labels) if labels else "Screen"
    return (
        "You are R.O.S.E's AI Screen Vision analyzer on Windows.\n"
        f"You have been given {len(labels)} crisp screenshot(s): {displays_desc}.\n"
        f"Current active foreground window: {fg_context or 'Desktop'}\n\n"
        "Your tasks:\n"
        "1. If multiple monitors are shown, clearly identify what is on EACH monitor (e.g., 'Monitor 1: ...', 'Monitor 2: ...').\n"
        "2. Read exact visible text, job listings, errors, code, URLs, buttons, prices, and headings accurately.\n"
        "3. Directly and helpfully answer the user's request/question based on all visible screens.\n"
        "4. Do not hallucinate text you cannot read clearly.\n\n"
        f"User question: {user_query}\n\n"
        "Respond in crisp, helpful, professional English."
    )


def _extract_gemini_text(response) -> str:
    text = str(getattr(response, "text", "") or "").strip()
    if text:
        return text
    chunks: list[str] = []
    for candidate in (getattr(response, "candidates", None) or []):
        content = getattr(candidate, "content", None)
        for part in (getattr(content, "parts", None) or []):
            t = str(getattr(part, "text", "") or "").strip()
            if t:
                chunks.append(t)
    return "\n".join(chunks).strip()


def _analyze_with_gemini(query: str, captures: list[dict]) -> str:
    api_key = str(get_app_config_value("gemini_api_key", "") or "").strip()
    if not api_key:
        return "Gemini API key is not configured for screen vision."

    from google import genai  # type: ignore
    from google.genai import types  # type: ignore

    labels = [c["label"] for c in captures]
    fg_ctx = " / ".join(p for p in (captures[0].get("owner_name", ""), captures[0].get("window_title", "")) if p)
    prompt = _vision_prompt(query, labels, fg_ctx)

    contents: list = [types.Part.from_text(text=prompt)]
    for cap in captures:
        p = Path(cap["image_path"])
        img_bytes, mime = _prepare_image_bytes(p)
        contents.append(types.Part.from_text(text=f"[Screenshot: {cap['label']}]"))
        contents.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=25000, retry_options=types.HttpRetryOptions(attempts=1)),
    )

    last_err = None
    for model_name in VISION_MODELS:
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(temperature=0.2),
            )
            txt = _extract_gemini_text(resp)
            if txt:
                return txt
        except Exception as exc:
            last_err = exc
            continue

    raise RuntimeError(f"Vision analysis failed across Gemini models: {last_err}")


def _analyze_with_lmstudio(query: str, captures: list[dict]) -> str:
    base = str(get_app_config_value("lmstudio_base_url", "http://127.0.0.1:1234/v1") or "").rstrip("/")
    api_key = str(get_app_config_value("lmstudio_api_key", "lm-studio") or "lm-studio")
    vision_model = str(
        get_app_config_value("lmstudio_vision_model", "")
        or get_app_config_value("lmstudio_model", "local-model")
        or "local-model"
    ).strip()

    labels = [c["label"] for c in captures]
    fg_ctx = " / ".join(p for p in (captures[0].get("owner_name", ""), captures[0].get("window_title", "")) if p)
    msg_parts: list[dict] = [{"type": "text", "text": _vision_prompt(query, labels, fg_ctx)}]
    for cap in captures:
        img_bytes, mime = _prepare_image_bytes(Path(cap["image_path"]))
        b64 = base64.b64encode(img_bytes).decode("ascii")
        msg_parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        data=json.dumps({"model": vision_model, "temperature": 0.2, "stream": False, "messages": [{"role": "user", "content": msg_parts}]}),
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(f"LM Studio {resp.status_code}: {resp.text[:250]}")
    data = resp.json()
    content = ((data.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "")
    if isinstance(content, str):
        return content.strip()
    return "\n".join(str(p.get("text", "")) for p in (content or []) if isinstance(p, dict)).strip()


def analyze_screen(query: str, target: str = "all_monitors") -> str:
    """
    Captures and analyzes all monitors ('all_monitors', default), a specific display
    ('monitor_1', 'monitor_2', 'monitor_3'), or only the 'active_window'.
    """
    ok, detail, captures = _capture_targets(target)
    if not ok:
        return detail or _screen_permission_message()

    try:
        non_blank = [c for c in captures if Path(c["image_path"]).exists() and not _image_looks_blank(Path(c["image_path"]))]
        if not non_blank:
            return (
                "Captured screenshot appears black or blank (this happens if the PC screen is asleep, locked, "
                "or displaying DRM-protected video). " + _screen_permission_message()
            )

        if is_local_backend():
            analysis = _analyze_with_lmstudio(query, non_blank)
        else:
            analysis = _analyze_with_gemini(query, non_blank)

        captured_labels = " + ".join(c["label"] for c in non_blank)
        return f"[Viewed: {captured_labels}]\n{analysis}"
    except Exception as exc:
        return f"Captured screen(s) ({', '.join(c['label'] for c in captures)}) but analysis failed: {exc}"
    finally:
        for cap in captures:
            try:
                p = Path(cap["image_path"])
                if p.exists():
                    p.unlink()
            except Exception:
                pass
