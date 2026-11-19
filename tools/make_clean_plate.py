"""Offline one-time script to build assets/ui/clean_plate.png from assets/ui/reference_bg.png.

Reads ui_regions.json, masks ONLY the baked-in dynamic values (clock digits/hands,
left-panel values/bars/weather icon/camera text, 'Listening...' pedestal label,
header online badge & voice core text, baked conversation lines & input placeholder),
removes them via targeted luminance/chroma masking + cv2.inpaint / sampled track
restoration, and saves:
  - assets/ui/clean_plate.png (1672x941)
  - assets/ui/clean_plate_compare.png (side-by-side before/after inspection plate)

IMPORTANT: cv2 is ONLY for this offline script. Never import cv2 in runtime UI code.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
REGIONS_PATH = ROOT / "ui_regions.json"
REF_PATH = ROOT / "assets" / "ui" / "reference_bg.png"
CLEAN_PATH = ROOT / "assets" / "ui" / "clean_plate.png"
COMPARE_PATH = ROOT / "assets" / "ui" / "clean_plate_compare.png"


def _clip_rect(rect: list[int], width: int, height: int) -> tuple[int, int, int, int]:
    x, y, w, h = [int(round(v)) for v in rect]
    x0 = max(0, min(width, x))
    y0 = max(0, min(height, y))
    x1 = max(x0, min(width, x + w))
    y1 = max(y0, min(height, y + h))
    return x0, y0, x1, y1


def _add_bright_text_mask(
    mask: np.ndarray,
    bgr: np.ndarray,
    rect: list[int],
    *,
    lum_delta: float = 28.0,
    min_lum: float = 78.0,
    dilate_px: int = 3,
    percentile_bg: float = 25.0,
) -> None:
    """Mask bright text/glyph strokes inside rect relative to local panel background."""
    h_img, w_img = bgr.shape[:2]
    x0, y0, x1, y1 = _clip_rect(rect, w_img, h_img)
    if x1 <= x0 or y1 <= y0:
        return
    roi = bgr[y0:y1, x0:x1].astype(np.float32)
    lum = 0.114 * roi[:, :, 0] + 0.587 * roi[:, :, 1] + 0.299 * roi[:, :, 2]
    # Sample per-row dark background baseline to preserve horizontal glass bands
    row_bg = np.percentile(lum, percentile_bg, axis=1, keepdims=True)
    thresh = np.maximum(min_lum, row_bg + lum_delta)
    hit = (lum > thresh).astype(np.uint8) * 255
    if dilate_px > 0 and np.any(hit):
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilate_px + 1, 2 * dilate_px + 1))
        hit = cv2.dilate(hit, k)
    mask[y0:y1, x0:x1] = np.maximum(mask[y0:y1, x0:x1], hit)


def _add_solid_rect_mask(mask: np.ndarray, rect: list[int], pad: int = 0) -> None:
    h_img, w_img = mask.shape[:2]
    x0, y0, x1, y1 = _clip_rect(rect, w_img, h_img)
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(w_img, x1 + pad)
    y1 = min(h_img, y1 + pad)
    if x1 > x0 and y1 > y0:
        mask[y0:y1, x0:x1] = 255


def _add_clock_hands_mask(mask: np.ndarray, bgr: np.ndarray, dial_rect: list[int]) -> None:
    """Mask ONLY the hands inside the circular bezel of the analog clock."""
    h_img, w_img = bgr.shape[:2]
    x0, y0, x1, y1 = _clip_rect(dial_rect, w_img, h_img)
    cx = (x0 + x1) * 0.5
    cy = (y0 + y1) * 0.5
    yy, xx = np.mgrid[y0:y1, x0:x1]
    dist = np.hypot(xx - cx, yy - cy)
    inner = dist <= 19.5  # stay well inside outer dial bezel ring
    roi = bgr[y0:y1, x0:x1].astype(np.float32)
    lum = 0.114 * roi[:, :, 0] + 0.587 * roi[:, :, 1] + 0.299 * roi[:, :, 2]
    sub = inner & (lum > 72.0)
    hit = sub.astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    hit = cv2.dilate(hit, k)
    hit[~inner] = 0
    mask[y0:y1, x0:x1] = np.maximum(mask[y0:y1, x0:x1], hit)


def _add_yellow_conv_text_mask(mask: np.ndarray, bgr: np.ndarray, rect: list[int]) -> None:
    """Mask baked SYS yellow text lines in Conversation panel without smudging backdrop."""
    h_img, w_img = bgr.shape[:2]
    x0, y0, x1, y1 = _clip_rect(rect, w_img, h_img)
    roi = bgr[y0:y1, x0:x1].astype(np.float32)
    b, g, r = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
    lum = 0.114 * b + 0.587 * g + 0.299 * r
    # Yellow/amber text + its warm anti-aliased fringe
    warm_hit = ((r > 62.0) & (g > 55.0) & ((r - b) > 5.0)) | (lum > 92.0)
    hit = warm_hit.astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    hit = cv2.dilate(hit, k)
    mask[y0:y1, x0:x1] = np.maximum(mask[y0:y1, x0:x1], hit)


def _restore_empty_bar_tracks(clean_bgr: np.ndarray, src_bgr: np.ndarray, sys_reg: dict) -> None:
    """Restore dark recessed glass tracks on the 4 system status bars without color bleed."""
    cx, cy, cw, ch = sys_reg["cpu_bar"]
    cy0 = max(0, cy - 1)
    cy1 = cy + ch + 1
    patch_h = cy1 - cy0
    sample_x0 = cx + int(cw * 0.52)
    sample_x1 = cx + int(cw * 0.90)
    patch = src_bgr[cy0:cy1, sample_x0:sample_x1].copy()
    if patch.size == 0:
        return
    empty_track = cv2.resize(patch, (cw, patch_h), interpolation=cv2.INTER_CUBIC)
    alpha = np.ones((patch_h, cw, 1), dtype=np.float32)
    if patch_h >= 4:
        alpha[0, :, 0] = 0.45
        alpha[-1, :, 0] = 0.45
    alpha[:, 0, 0] = 0.5
    alpha[:, -1, 0] = 0.5

    for key in ("cpu_bar", "ram_bar", "disk_bar", "battery_bar"):
        bx, by, bw, bh = sys_reg[key]
        by0 = max(0, by - 1)
        by1 = by + bh + 1
        th = by1 - by0
        track = empty_track if (bw, th) == (cw, patch_h) else cv2.resize(patch, (bw, th), interpolation=cv2.INTER_CUBIC)
        t_alpha = alpha if (bw, th) == (cw, patch_h) else cv2.resize(alpha, (bw, th), interpolation=cv2.INTER_LINEAR)[..., None]
        cur = clean_bgr[by0:by1, bx : bx + bw].astype(np.float32)
        blended = track.astype(np.float32) * t_alpha + cur * (1.0 - t_alpha)
        clean_bgr[by0:by1, bx : bx + bw] = np.clip(blended, 0, 255).astype(np.uint8)


def build_clean_plate() -> tuple[Path, Path]:
    with REGIONS_PATH.open("r", encoding="utf-8") as f:
        reg = json.load(f)

    src = cv2.imread(str(REF_PATH), cv2.IMREAD_COLOR)
    if src is None:
        raise FileNotFoundError(f"Reference image not found at {REF_PATH}")

    h, w = src.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)

    # 1. Header dynamic items (keep 'SYSTEM SETTINGS' & 'R.O.S.E' title untouched)
    _add_bright_text_mask(mask, src, reg["header"]["settings_voice_text"], lum_delta=20.0, min_lum=68.0, dilate_px=3)
    _add_bright_text_mask(mask, src, reg["header"]["online_dot"], lum_delta=12.0, min_lum=48.0, dilate_px=7)
    _add_bright_text_mask(mask, src, reg["header"]["online_text"], lum_delta=16.0, min_lum=66.0, dilate_px=4)

    # 2. Time panel (keep '• TIME' label, outer border, and outer dial bezel untouched)
    tp = reg["time_panel"]
    _add_bright_text_mask(mask, src, tp["header_city"], lum_delta=22.0, min_lum=78.0, dilate_px=3)
    _add_clock_hands_mask(mask, src, tp["clock_dial"])
    _add_bright_text_mask(mask, src, tp["clock_digits"], lum_delta=24.0, min_lum=80.0, dilate_px=4)
    _add_bright_text_mask(mask, src, tp["seconds_digits"], lum_delta=22.0, min_lum=78.0, dilate_px=3)
    _add_bright_text_mask(mask, src, tp["date_text"], lum_delta=22.0, min_lum=75.0, dilate_px=3)
    _add_bright_text_mask(mask, src, tp["weekday_text"], lum_delta=22.0, min_lum=75.0, dilate_px=3)

    # 3. Weather panel (keep '• WEATHER' and divider border untouched)
    wp = reg["weather_panel"]
    _add_bright_text_mask(mask, src, wp["header_city"], lum_delta=22.0, min_lum=78.0, dilate_px=3)
    _add_bright_text_mask(mask, src, wp["icon"], lum_delta=18.0, min_lum=70.0, dilate_px=4)
    _add_bright_text_mask(mask, src, wp["temp"], lum_delta=24.0, min_lum=82.0, dilate_px=4)
    _add_bright_text_mask(mask, src, wp["city_right"], lum_delta=20.0, min_lum=74.0, dilate_px=3)
    _add_bright_text_mask(mask, src, wp["lines"], lum_delta=20.0, min_lum=72.0, dilate_px=3)

    # 4. System status (keep labels/icons untouched; bars restored separately with empty track)
    ss = reg["system_status"]
    _add_bright_text_mask(mask, src, ss["uptime_value"], lum_delta=20.0, min_lum=72.0, dilate_px=3)
    for k in ("cpu_pct", "ram_pct", "disk_pct", "battery_pct", "net_up", "net_down"):
        _add_bright_text_mask(mask, src, ss[k], lum_delta=20.0, min_lum=70.0, dilate_px=3)

    # 5. Health summary (5 lines, keep 5 icons left of x=78 untouched)
    _add_bright_text_mask(mask, src, reg["health_summary"]["all_lines"], lum_delta=20.0, min_lum=72.0, dilate_px=3)

    # 6. Camera vision (keep NO STILL thumbnail bezel, IDENTIFY/LIVE buttons, and title untouched)
    cvr = reg["camera_vision"]
    _add_bright_text_mask(mask, src, cvr["status"], lum_delta=22.0, min_lum=76.0, dilate_px=3)
    _add_bright_text_mask(mask, src, cvr["text"], lum_delta=18.0, min_lum=70.0, dilate_px=3)

    # 7. Center pedestal status ("• Listening..." only, keep "R.O.S.E" & pedestal rings untouched)
    _add_bright_text_mask(
        mask,
        src,
        reg["center"]["pedestal_status"],
        lum_delta=20.0,
        min_lum=68.0,
        dilate_px=4,
        percentile_bg=20.0,
    )

    # 8. Conversation panel dynamic items (keep frame, "CONVERSATION", mini-wave border, input border, buttons)
    conv = reg["conversation"]
    _add_bright_text_mask(mask, src, conv["status"], lum_delta=22.0, min_lum=76.0, dilate_px=3)
    _add_yellow_conv_text_mask(mask, src, conv["messages_clean_mask"])
    # Mask faint "Type a command..." placeholder smoothly
    _add_bright_text_mask(
        mask,
        src,
        conv["input_placeholder_mask"],
        lum_delta=10.0,
        min_lum=52.0,
        dilate_px=3,
        percentile_bg=20.0,
    )

    # Execute high-quality Telea inpainting on the targeted mask
    clean = cv2.inpaint(src, mask, 4, cv2.INPAINT_TELEA)

    # Apply light bilateral smoothing ONLY inside inpainted mask pixels to blend grain
    smoothed = cv2.GaussianBlur(clean, (5, 5), 0)
    alpha = (cv2.GaussianBlur(mask.astype(np.float32), (5, 5), 0) / 255.0)[..., None] * 0.45
    clean = np.clip(smoothed.astype(np.float32) * alpha + clean.astype(np.float32) * (1.0 - alpha), 0, 255).astype(
        np.uint8
    )

    # Restore crisp empty recessed glass bar tracks for CPU/RAM/DISK/BATTERY
    _restore_empty_bar_tracks(clean, src, ss)

    CLEAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(CLEAN_PATH), clean)

    # Build before/after side-by-side comparison (and zoomed crops strip below? keep clean 2-up at full res or half res)
    compare = np.hstack([src, clean])
    cv2.imwrite(str(COMPARE_PATH), compare)

    masked_px = int((mask > 0).sum())
    print(f"Clean plate written: {CLEAN_PATH} ({w}x{h}, {masked_px} pixels inpainted, {100.0*masked_px/(w*h):.2f}% of canvas)")
    print(f"Before/after comparison written: {COMPARE_PATH} ({compare.shape[1]}x{compare.shape[0]})")
    return CLEAN_PATH, COMPARE_PATH


if __name__ == "__main__":
    build_clean_plate()
