"""
R.O.S.E Phase 5 — High-Detail Camera Identification, Sharp Still Capture & Visual Memory.

Key capabilities:
1. High-resolution sharp still capture (`capture_sharp_still`):
   - Flushes camera buffer, discards warmup frames for autofocus/auto-exposure,
     targets highest supported resolution (>= 1280x720), checks Laplacian variance,
     and asks the user to hold steady + retries once if blurry.
2. Full frame + high-detail center crop identification (`identify_held_item`):
   - Prompts Gemini Vision (or local LM Studio Vision) for strict JSON:
     {object_type, brand, model, confidence, evidence, alternatives, needs_better_view, suggestion, matched_memory_name}
   - Uses saved reference items in `camera_memory/items.json` as extra context.
   - If confidence < 70%, asks user to show the back/label/barcode and caches context
     for follow-up multi-view re-analysis (`action="reanalyze"` or callback).
3. "Remember this as <name>" (`remember_item`):
   - Saves a crisp reference JPEG and result metadata to `camera_memory/items.json`
     and verifies disk persistence (Rule A2).
4. Cheap capture-only UI preview bridge + on-demand Live Surveillance HUD (`RoseCameraWindow`).
"""

from __future__ import annotations

import base64
import datetime as dt
import io
import json
import math
import re
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

try:
    import tkinter as tk
except Exception:
    tk = None  # type: ignore

from PIL import Image

try:
    from PIL import ImageTk
except Exception:
    ImageTk = None  # type: ignore

from app_config import get_app_config_value, is_local_backend
from core.logging_setup import get_logger
from core.result import Result

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    cv2 = None  # type: ignore
    np = None  # type: ignore
    HAS_CV2 = False

LOGGER = get_logger()

BASE_DIR = Path(__file__).resolve().parent.parent
CAMERA_MEMORY_DIR = BASE_DIR / "camera_memory"
CAMERA_MEMORY_IMAGES_DIR = CAMERA_MEMORY_DIR / "images"
CAMERA_MEMORY_FILE = CAMERA_MEMORY_DIR / "items.json"
LAST_CAPTURE_PATH = CAMERA_MEMORY_DIR / "last_capture.jpg"
SECURITY_SNAP_DIR = Path.home() / "Pictures" / "ROSE_Security"

TARGET_WIDTH = 1280
TARGET_HEIGHT = 720
CANDIDATE_RESOLUTIONS = ((1920, 1080), (1280, 720))
DEFAULT_FLUSH_GRABS = 4
DEFAULT_WARMUP_FRAMES = 4
MIN_SHARPNESS_LAPLACIAN = 45.0
CONFIDENCE_THRESHOLD = 70

VISION_MODELS = (
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.1-flash-lite",
)

IDENTIFICATION_SCHEMA_PROMPT = """You are R.O.S.E's high-precision visual product identifier.
You are given TWO views of the same still capture (and optional remembered personal items or previous angle):
1. [FULL FRAME]: Full scene context.
2. [CENTER CROP]: High-detail zoom of the item the user is holding in the center.

CRITICAL RULES:
- Identify the SPECIFIC product: brand + exact model/variant (NOT just a generic category or color like "black phone" or "blue bottle").
- Carefully inspect visible logos, printed/embossed text, model numbers, camera-module layout (lens count, diagonal/vertical/pill/island arrangement, flash/LiDAR placement), ports, buttons, silhouette, cap/label design, and barcodes.
- Compare against any [REMEMBERED ITEMS] provided below if the held item matches one of them.
- If multiple models look very alike (e.g. iPhone 11 vs XR vs 12, or AirPods Gen 2 vs Gen 3), state that clearly and list them in "alternatives".
- NEVER invent, guess, or fabricate a model name you cannot support with visible evidence. If unsure, lower "confidence" honestly (<70), explain visible evidence only, set "needs_better_view": true, and give a specific "suggestion" (e.g. "Show the back camera bump, printed label, or barcode cleanly").

Respond with ONLY valid JSON matching this exact structure:
{
  "object_type": "concise category e.g. smartphone, water bottle, powerbank, earbuds case",
  "brand": "brand name or 'Unknown'",
  "model": "specific model/product name or 'Unknown'",
  "confidence": 0,
  "evidence": ["specific visible clues observed"],
  "alternatives": ["similar candidate models if any"],
  "needs_better_view": false,
  "suggestion": "concise request if confidence < 70 or more detail needed, else empty string",
  "matched_memory_name": "name from REMEMBERED ITEMS if clearly a match, else empty string"
}"""


# ── Pure Image & Sharpness Utilities ──────────────────────────────────────────

def measure_frame_sharpness(frame_bgr: Any) -> dict[str, float]:
    """
    Computes Laplacian variance across full frame and centered 60% ROI.
    Returns dict with `score`, `full_var`, and `center_var`.
    """
    if not HAS_CV2 or frame_bgr is None:
        return {"score": 0.0, "full_var": 0.0, "center_var": 0.0}
    gray = frame_bgr if len(frame_bgr.shape) == 2 else cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    full_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    h, w = gray.shape[:2]
    if h >= 80 and w >= 80:
        y1, y2 = int(h * 0.20), int(h * 0.80)
        x1, x2 = int(w * 0.20), int(w * 0.80)
        center_roi = gray[y1:y2, x1:x2]
        center_var = float(cv2.Laplacian(center_roi, cv2.CV_64F).var()) if center_roi.size else full_var
    else:
        center_var = full_var
    combined = (0.75 * center_var) + (0.25 * full_var)
    return {
        "score": round(combined, 2),
        "full_var": round(full_var, 2),
        "center_var": round(center_var, 2),
    }


def compute_sharpness(frame_bgr: Any) -> float:
    """Returns scalar Laplacian variance sharpness score for `frame_bgr`."""
    return measure_frame_sharpness(frame_bgr)["score"]


def make_center_crop(frame_bgr: Any, crop_ratio: float = 0.55) -> Any:
    """Extracts centered ROI (`crop_ratio` x `crop_ratio`) preserving full sensor detail."""
    if frame_bgr is None:
        return None
    h, w = frame_bgr.shape[:2]
    ratio = max(0.25, min(0.90, float(crop_ratio)))
    cw = max(16, int(w * ratio))
    ch = max(16, int(h * ratio))
    x1 = max(0, (w - cw) // 2)
    y1 = max(0, (h - ch) // 2)
    return frame_bgr[y1 : y1 + ch, x1 : x1 + cw].copy()


def _bgr_to_jpeg_bytes(frame_bgr: Any, max_dim: int = 1600, quality: int = 92) -> tuple[bytes, int, int]:
    """Encodes a BGR numpy array to high-detail JPEG without shrinking crisp detail unnecessarily."""
    if not HAS_CV2:
        raise RuntimeError("OpenCV (cv2) is not installed.")
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)
    if max(pil_img.size) > max_dim:
        pil_img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    pil_img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), pil_img.width, pil_img.height


def encode_vision_images(frame_bgr: Any) -> tuple[bytes, bytes, dict[str, Any]]:
    """Prepares high-res full frame JPEG + high-detail center crop JPEG for vision inference."""
    full_bytes, fw, fh = _bgr_to_jpeg_bytes(frame_bgr, max_dim=1600, quality=92)
    crop = make_center_crop(frame_bgr, crop_ratio=0.55)
    # If native center crop is under 640px, upscale gently with LANCZOS so fine printed labels stay readable
    if crop is not None and HAS_CV2 and max(crop.shape[:2]) < 640:
        scale = min(2.0, 720.0 / max(1, max(crop.shape[:2])))
        crop = cv2.resize(
            crop,
            (int(crop.shape[1] * scale), int(crop.shape[0] * scale)),
            interpolation=cv2.INTER_LANCZOS4,
        )
    crop_bytes, cw, ch = _bgr_to_jpeg_bytes(crop, max_dim=1280, quality=94)
    return full_bytes, crop_bytes, {
        "full_size": (fw, fh),
        "crop_size": (cw, ch),
        "full_bytes": len(full_bytes),
        "crop_bytes": len(crop_bytes),
    }


def save_preview_thumbnail(frame_bgr: Any, dest: Path = LAST_CAPTURE_PATH, max_dim: int = 480) -> str:
    """Saves a lightweight capture preview JPEG used by UI panels (never on 30fps loop)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    jpeg_bytes, _, _ = _bgr_to_jpeg_bytes(frame_bgr, max_dim=max_dim, quality=85)
    dest.write_bytes(jpeg_bytes)
    return str(dest)


# ── Remembered Item Persistence (`camera_memory/items.json`) ──────────────────

def load_camera_memory(memory_file: Path = CAMERA_MEMORY_FILE) -> list[dict[str, Any]]:
    if not memory_file.exists():
        return []
    try:
        raw = json.loads(memory_file.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except Exception as exc:
        LOGGER.warning("Could not read camera memory %s: %s", memory_file, exc)
        return []


def save_camera_memory(items: list[dict[str, Any]], memory_file: Path = CAMERA_MEMORY_FILE) -> None:
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    memory_file.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "item").lower()).strip("_")
    return slug[:48] or "item"


# ── JSON Normalization & Spoken Response Formatting ───────────────────────────

def _extract_json_block(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"Vision response did not contain a valid JSON object: {cleaned[:160]}")


def normalize_identification_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerces raw vision JSON into the strict Phase 5 schema with confidence gating."""
    obj_type = str(raw.get("object_type") or "item").strip() or "item"
    brand = str(raw.get("brand") or "Unknown").strip() or "Unknown"
    model = str(raw.get("model") or "Unknown").strip() or "Unknown"
    try:
        conf = int(round(float(raw.get("confidence", 0) or 0)))
    except Exception:
        conf = 0
    conf = max(0, min(100, conf))

    ev_raw = raw.get("evidence") or []
    evidence = [str(x).strip() for x in (ev_raw if isinstance(ev_raw, list) else [ev_raw]) if str(x).strip()]

    alt_raw = raw.get("alternatives") or []
    alternatives = [str(x).strip() for x in (alt_raw if isinstance(alt_raw, list) else [alt_raw]) if str(x).strip()]

    needs_better = bool(raw.get("needs_better_view", False)) or (conf < CONFIDENCE_THRESHOLD)
    suggestion = str(raw.get("suggestion") or "").strip()
    if conf < CONFIDENCE_THRESHOLD and not suggestion:
        suggestion = "Show the back, the printed label, or the barcode cleanly so I can re-analyze."

    matched_memory = str(raw.get("matched_memory_name") or "").strip()

    return {
        "object_type": obj_type,
        "brand": brand,
        "model": model,
        "confidence": conf,
        "evidence": evidence[:5],
        "alternatives": alternatives[:4],
        "needs_better_view": needs_better,
        "suggestion": suggestion,
        "matched_memory_name": matched_memory,
    }


def _display_product_name(res: dict[str, Any]) -> str:
    brand = str(res.get("brand") or "").strip()
    model = str(res.get("model") or "").strip()
    obj = str(res.get("object_type") or "item").strip()
    parts: list[str] = []
    if brand and brand.lower() not in ("unknown", "none", "n/a"):
        parts.append(brand)
    if model and model.lower() not in ("unknown", "none", "n/a"):
        if not parts or parts[0].lower() not in model.lower():
            parts.append(model)
    if parts:
        return " ".join(parts)
    return obj or "item"


def _indefinite_article(phrase: str) -> str:
    first = (phrase or "").strip()[:1].lower()
    return "an" if first in {"a", "e", "i", "o", "u"} else "a"


def format_spoken_identification(res: dict[str, Any]) -> str:
    """Formats structured identification JSON into natural, honest spoken English."""
    conf = int(res.get("confidence", 0))
    label = _display_product_name(res)
    obj_type = str(res.get("object_type") or "item").strip()
    evidence = [e for e in (res.get("evidence") or []) if e]
    alternatives = [a for a in (res.get("alternatives") or []) if a]
    needs_better = bool(res.get("needs_better_view", False))
    suggestion = str(res.get("suggestion") or "").strip()
    matched_memory = str(res.get("matched_memory_name") or "").strip()

    ev_clause = f", from {', '.join(evidence[:2])}" if evidence else ""
    brand_or_model_known = (
        str(res.get("brand") or "").strip().lower() not in ("", "unknown", "none", "n/a")
        or str(res.get("model") or "").strip().lower() not in ("", "unknown", "none", "n/a")
    )

    if matched_memory and conf >= CONFIDENCE_THRESHOLD:
        detail = f" ({label})" if brand_or_model_known and label.lower() != matched_memory.lower() else ""
        msg = f"That looks like your saved '{matched_memory}'{detail} — about {conf}% sure{ev_clause}."
        return msg

    if conf >= CONFIDENCE_THRESHOLD and brand_or_model_known:
        art = _indefinite_article(label)
        msg = f"That looks like {art} {label} — about {conf}% sure{ev_clause}."
        if alternatives and needs_better:
            msg += f" Could also be {' or '.join(alternatives[:2])}. Want to show me the back to confirm?"
        elif needs_better and suggestion:
            msg += f" {suggestion}"
        return msg

    # Confidence < 70% or model unclear -> honest "likely / not sure" + request back/label/barcode
    if brand_or_model_known:
        lead = f"I'm not completely sure yet — likely {_indefinite_article(label)} {label} (about {conf}% confident{ev_clause})."
    else:
        lead = f"I can see {_indefinite_article(obj_type)} {obj_type}, but I'm not sure of the exact brand or model yet (about {conf}% confident{ev_clause})."

    if alternatives:
        lead += f" Similar matches could be {', '.join(alternatives[:2])}."

    ask = suggestion or "Please show me the back, the printed label, or the barcode cleanly, then I'll re-analyze."
    if not any(k in ask.lower() for k in ("back", "label", "barcode")):
        ask = "Could you show me the back, the printed label, or the barcode cleanly so I can re-analyze?"
    return f"{lead} {ask}".strip()


# ── Backend Vision Callers (Gemini Cloud + Local LM Studio) ───────────────────

def _call_gemini_vision_json(
    prompt: str,
    image_parts: list[tuple[str, bytes]],
    api_key: str,
) -> dict[str, Any]:
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore

    contents: list[Any] = [types.Part.from_text(text=prompt)]
    for label, jpg_bytes in image_parts:
        if jpg_bytes:
            contents.append(types.Part.from_text(text=label))
            contents.append(types.Part.from_bytes(data=jpg_bytes, mime_type="image/jpeg"))

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=28000, retry_options=types.HttpRetryOptions(attempts=1)),
    )
    last_err: Exception | None = None
    for model_name in VISION_MODELS:
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            raw_text = str(getattr(resp, "text", "") or "").strip()
            if not raw_text and getattr(resp, "candidates", None):
                chunks = []
                for cand in resp.candidates or []:
                    c_obj = getattr(cand, "content", None)
                    for part in getattr(c_obj, "parts", None) or []:
                        t = str(getattr(part, "text", "") or "").strip()
                        if t:
                            chunks.append(t)
                raw_text = "\n".join(chunks).strip()
            if raw_text:
                return _extract_json_block(raw_text)
        except Exception as exc:
            last_err = exc
            continue
    raise RuntimeError(f"Gemini vision failed across models: {last_err}")


def _call_lmstudio_vision_json(
    prompt: str,
    image_parts: list[tuple[str, bytes]],
) -> dict[str, Any]:
    import requests

    base = str(get_app_config_value("lmstudio_base_url", "http://127.0.0.1:1234/v1") or "").rstrip("/")
    api_key = str(get_app_config_value("lmstudio_api_key", "lm-studio") or "lm-studio")
    vision_model = str(
        get_app_config_value("lmstudio_vision_model", "")
        or get_app_config_value("lmstudio_model", "local-model")
        or "local-model"
    ).strip()

    msg_parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for label, jpg_bytes in image_parts:
        if jpg_bytes:
            msg_parts.append({"type": "text", "text": label})
            b64 = base64.b64encode(jpg_bytes).decode("ascii")
            msg_parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        data=json.dumps({
            "model": vision_model,
            "temperature": 0.1,
            "stream": False,
            "messages": [{"role": "user", "content": msg_parts}],
        }),
        timeout=90,
    )
    if not resp.ok:
        raise RuntimeError(f"LM Studio {resp.status_code}: {resp.text[:220]}")
    data = resp.json()
    content = ((data.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "")
    if not isinstance(content, str):
        content = "\n".join(str(p.get("text", "")) for p in (content or []) if isinstance(p, dict))
    return _extract_json_block(content)


# ── Core Camera Controller (Hardware + Mock + Memory + UI Bridge) ─────────────

class CameraService:
    """
    Hardware-independent, thread-safe camera still capture, vision analyzer, and
    saved item memory service. Never runs a 30fps loop unless the live HUD is opened.
    """

    def __init__(self, memory_file: Path = CAMERA_MEMORY_FILE):
        self.memory_file = memory_file
        self.images_dir = memory_file.parent / "images"
        self.last_preview_path = memory_file.parent / "last_capture.jpg"
        self._lock = threading.Lock()
        self._capture_busy = threading.Lock()
        self._last_capture_frame: Any = None
        self._last_capture_meta: dict[str, Any] = {}
        self._last_analysis: dict[str, Any] | None = None
        self._last_capture_ts: float = 0.0
        self._pending_recheck: dict[str, Any] | None = None
        self._preview_listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._hud: RoseCameraWindow | None = None

    def register_preview_listener(self, cb: Callable[[str, dict[str, Any]], None]) -> None:
        with self._lock:
            if cb not in self._preview_listeners:
                self._preview_listeners.append(cb)

    def _emit_preview(self, preview_path: str, payload: dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._preview_listeners)
        for cb in listeners:
            try:
                cb(preview_path, dict(payload))
            except Exception as exc:
                LOGGER.debug("Camera preview listener error: %s", exc)

    def _grab_frames_from_webcam(
        self,
        source: int | str = 0,
        flush_grabs: int = DEFAULT_FLUSH_GRABS,
        warmup_frames: int = DEFAULT_WARMUP_FRAMES,
    ) -> tuple[bool, Any, dict[str, Any]]:
        if not HAS_CV2:
            return False, None, {"error": "OpenCV (cv2) is not installed."}

        # If live camera HUD already holds the exclusive Windows DirectShow capture device, borrow cleanly
        if self._hud is not None and self._hud.running and self._hud.cap is not None:
            return self._hud.capture_still_from_active(
                flush_grabs=flush_grabs,
                warmup_frames=warmup_frames,
            )

        cap = None
        try:
            if isinstance(source, int):
                cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
                if not cap or not cap.isOpened():
                    cap = cv2.VideoCapture(source)
            else:
                cap = cv2.VideoCapture(str(source))

            if not cap or not cap.isOpened():
                return False, None, {"error": "No webcam detected or camera is in use by another app."}

            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
            except Exception:
                pass

            # Negotiate highest supported resolution (targeting 1280x720 or higher)
            probe_frame = None
            for rw, rh in CANDIDATE_RESOLUTIONS:
                try:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, rw)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, rh)
                except Exception:
                    pass
                for _ in range(max(1, flush_grabs)):
                    cap.grab()
                ok_probe, probe_frame = cap.read()
                if ok_probe and probe_frame is not None:
                    ph, pw = probe_frame.shape[:2]
                    if pw >= TARGET_WIDTH and ph >= TARGET_HEIGHT:
                        break

            # Discard warmup frames for auto-exposure & autofocus stabilization
            final_frame = probe_frame
            for _ in range(max(1, warmup_frames)):
                ok_w, w_frame = cap.read()
                if ok_w and w_frame is not None:
                    final_frame = w_frame

            if final_frame is None:
                return False, None, {"error": "Webcam opened but returned an empty frame."}

            fh, fw = final_frame.shape[:2]
            return True, final_frame, {"width": int(fw), "height": int(fh)}
        except Exception as exc:
            LOGGER.exception("Webcam capture failed")
            return False, None, {"error": f"Camera capture error: {exc}"}
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

    def capture_sharp_still(
        self,
        source: int | str = 0,
        min_sharpness: float = MIN_SHARPNESS_LAPLACIAN,
        flush_grabs: int = DEFAULT_FLUSH_GRABS,
        warmup_frames: int = DEFAULT_WARMUP_FRAMES,
        frame_provider: Callable[[int], Any] | None = None,
        status_cb: Callable[[str], None] | None = None,
        retry_delay_sec: float = 0.65,
    ) -> tuple[bool, str, dict[str, Any]]:
        """
        Captures one high-resolution sharp still frame.
        Flushes buffer, discards warmup frames, measures Laplacian variance,
        and if blurry asks the user to hold steady and retries ONCE.
        """
        if not HAS_CV2:
            return False, "OpenCV (cv2) is not installed.", {}

        with self._capture_busy:
            last_sharp = 0.0
            last_meta: dict[str, Any] = {}
            attempts_meta: list[dict[str, Any]] = []

            for attempt in (1, 2):
                if frame_provider is not None:
                    provided = frame_provider(attempt)
                    # Mock provider may return either a single ndarray or list of frames (simulating buffer + warmup)
                    if isinstance(provided, (list, tuple)):
                        frames = [f for f in provided if f is not None]
                        if not frames:
                            return False, "Camera mock provider returned no frames.", {}
                        # Flush + skip initial warmup frames when batch is supplied
                        skip = min(len(frames) - 1, max(0, flush_grabs + warmup_frames)) if len(frames) > 2 else len(frames) - 1
                        frame = frames[skip] if skip < len(frames) else frames[-1]
                    else:
                        frame = provided
                    if frame is None:
                        return False, "Could not read frame from mock camera source.", {}
                    fh, fw = frame.shape[:2]
                    meta = {"width": int(fw), "height": int(fh), "flushed": flush_grabs, "warmed_up": warmup_frames}
                else:
                    ok_grab, frame, meta = self._grab_frames_from_webcam(
                        source=source,
                        flush_grabs=flush_grabs,
                        warmup_frames=warmup_frames,
                    )
                    if not ok_grab or frame is None:
                        return False, str(meta.get("error") or "Could not capture a camera frame."), {}

                sharp_info = measure_frame_sharpness(frame)
                sharp_score = sharp_info["score"]
                last_sharp = sharp_score
                meta.update({
                    "sharpness": sharp_score,
                    "full_laplacian": sharp_info["full_var"],
                    "center_laplacian": sharp_info["center_var"],
                    "attempt": attempt,
                })
                last_meta = meta
                attempts_meta.append(dict(meta))

                if sharp_score >= min_sharpness:
                    preview_path = save_preview_thumbnail(frame, dest=self.last_preview_path)
                    with self._lock:
                        self._last_capture_frame = frame.copy()
                        self._last_capture_meta = dict(meta)
                        self._last_capture_ts = time.time()
                    result_meta = {
                        **meta,
                        "frame": frame,
                        "retried": attempt > 1,
                        "attempts": attempts_meta,
                        "preview_path": preview_path,
                    }
                    return True, "", result_meta

                # First attempt blurry -> notify user to hold steady and retry once
                if attempt == 1:
                    if status_cb:
                        try:
                            status_cb("That image looked blurry — please hold steady for a second while I retry.")
                        except Exception:
                            pass
                    if frame_provider is None and retry_delay_sec > 0:
                        time.sleep(retry_delay_sec)

            # Attempt 2 also failed sharpness threshold
            preview_path = save_preview_thumbnail(frame, dest=self.last_preview_path)
            self._emit_preview(
                preview_path,
                {
                    "status": "blurry",
                    "title": "Blurry Capture",
                    "confidence": 0,
                    "evidence": [f"Sharpness {last_sharp:.1f} < {min_sharpness:.0f}"],
                    "suggestion": "Hold steady in good light and retry.",
                    "captured_at": dt.datetime.now().strftime("%H:%M:%S"),
                },
            )
            return False, (
                f"Both captures were too blurry (sharpness {last_sharp:.1f}, threshold {min_sharpness:.0f}). "
                "Please hold the object steady in better lighting and try again."
            ), {
                **last_meta,
                "retried": True,
                "attempts": attempts_meta,
                "preview_path": preview_path,
            }

    def _build_identification_prompt(
        self,
        question: str,
        memory_items: list[dict[str, Any]],
        previous_context: dict[str, Any] | None,
    ) -> tuple[str, list[tuple[str, bytes]]]:
        lines = [IDENTIFICATION_SCHEMA_PROMPT]
        q_clean = (question or "What exact product am I holding?").strip()
        lines.append(f"\nUser request: {q_clean}")

        extra_images: list[tuple[str, bytes]] = []

        if previous_context and isinstance(previous_context.get("analysis"), dict):
            prev_a = previous_context["analysis"]
            lines.append(
                "\n[PREVIOUS VIEW CONTEXT — user has now rotated/adjusted the item to help confirm]\n"
                f"- Prior guess: {_display_product_name(prev_a)} ({prev_a.get('confidence', 0)}% confidence)\n"
                f"- Prior evidence: {', '.join(prev_a.get('evidence') or ['none'])}\n"
                f"- Prior alternatives: {', '.join(prev_a.get('alternatives') or ['none'])}"
            )
            prev_jpg = previous_context.get("preview_bytes")
            if prev_jpg:
                extra_images.append(("[PREVIOUS ANGLE REFERENCE]", prev_jpg))

        if memory_items:
            lines.append("\n[REMEMBERED PERSONAL ITEMS IN CAMERA MEMORY]")
            for idx, item in enumerate(memory_items[:12], 1):
                res = item.get("result") or {}
                lbl = _display_product_name(res)
                evs = ", ".join((res.get("evidence") or [])[:3])
                lines.append(f"{idx}. Name='{item.get('name')}' | Product='{lbl}' | Clues='{evs}'")
            # Attach up to 2 most recent saved reference photos as extra visual context
            attached = 0
            for item in reversed(memory_items):
                if attached >= 2:
                    break
                img_p = Path(str(item.get("image_path") or ""))
                if img_p.exists() and img_p.is_file():
                    try:
                        extra_images.append(
                            (f"[REMEMBERED ITEM REFERENCE: {item.get('name')}]", img_p.read_bytes())
                        )
                        attached += 1
                    except Exception:
                        pass

        return "\n".join(lines), extra_images

    def analyze_item_frames(
        self,
        frame_bgr: Any,
        question: str = "What exact product am I holding?",
        previous_context: dict[str, Any] | None = None,
        analyzer_fn: Callable[[str, list[tuple[str, bytes]]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Runs vision model on [FULL FRAME] + [CENTER CROP] + memory/prior context."""
        full_jpg, crop_jpg, img_meta = encode_vision_images(frame_bgr)
        memory_items = load_camera_memory(self.memory_file)
        prompt, extra_parts = self._build_identification_prompt(question, memory_items, previous_context)

        image_parts: list[tuple[str, bytes]] = [
            ("[FULL FRAME]", full_jpg),
            ("[CENTER CROP — close-up of held item]", crop_jpg),
            *extra_parts,
        ]

        if analyzer_fn is not None:
            raw_json = analyzer_fn(prompt, image_parts)
        elif is_local_backend():
            raw_json = _call_lmstudio_vision_json(prompt, image_parts)
        else:
            api_key = str(get_app_config_value("gemini_api_key", "") or "").strip()
            if not api_key:
                raise RuntimeError("Gemini API key is missing in config.")
            raw_json = _call_gemini_vision_json(prompt, image_parts, api_key)

        normalized = normalize_identification_result(raw_json)
        normalized["image_meta"] = img_meta
        return normalized

    def identify_held_item(
        self,
        question: str = "What am I holding?",
        source: int | str = 0,
        use_previous_context: bool = False,
        frame_provider: Callable[[int], Any] | None = None,
        analyzer_fn: Callable[[str, list[tuple[str, bytes]]], dict[str, Any]] | None = None,
        auto_reanalyze_callback: Callable[[dict[str, Any]], Any] | None = None,
        status_cb: Callable[[str], None] | None = None,
        min_sharpness: float = MIN_SHARPNESS_LAPLACIAN,
    ) -> Result:
        """
        Captures a sharp still, identifies exact brand+model, updates UI preview ONCE,
        and enforces confidence < 70% follow-up / re-analysis flow.
        """
        ok, err, cap_meta = self.capture_sharp_still(
            source=source,
            min_sharpness=min_sharpness,
            frame_provider=frame_provider,
            status_cb=status_cb,
        )
        if not ok:
            return Result.failure(err, data=cap_meta)

        frame = cap_meta["frame"]
        prev_ctx = None
        if use_previous_context:
            with self._lock:
                prev_ctx = self._pending_recheck

        try:
            analysis = self.analyze_item_frames(
                frame_bgr=frame,
                question=question,
                previous_context=prev_ctx,
                analyzer_fn=analyzer_fn,
            )
        except Exception as exc:
            LOGGER.exception("Camera identification failed")
            preview_path = str(cap_meta.get("preview_path") or "")
            self._emit_preview(
                preview_path,
                {
                    "status": "error",
                    "title": "Analysis Error",
                    "confidence": 0,
                    "evidence": [str(exc)[:80]],
                    "suggestion": "Check API key or local vision model.",
                    "captured_at": dt.datetime.now().strftime("%H:%M:%S"),
                },
            )
            return Result.failure(f"Captured sharp still ({cap_meta['width']}x{cap_meta['height']}), but analysis failed: {exc}")

        analysis["sharpness"] = cap_meta["sharpness"]
        analysis["resolution"] = f"{cap_meta['width']}x{cap_meta['height']}"
        analysis["retried_blur"] = bool(cap_meta.get("retried", False))
        analysis["preview_path"] = str(cap_meta.get("preview_path") or "")
        analysis["reanalyzed"] = bool(use_previous_context and prev_ctx is not None)

        # Save or clear pending recheck state based on confidence < 70%
        preview_bytes, _, _ = _bgr_to_jpeg_bytes(frame, max_dim=960, quality=88)
        with self._lock:
            self._last_analysis = dict(analysis)
            if analysis["confidence"] < CONFIDENCE_THRESHOLD or analysis["needs_better_view"]:
                self._pending_recheck = {
                    "analysis": dict(analysis),
                    "preview_bytes": preview_bytes,
                    "captured_at": time.time(),
                }
            else:
                self._pending_recheck = None

        spoken = format_spoken_identification(analysis)
        analysis["spoken"] = spoken
        self._emit_preview(
            analysis["preview_path"],
            {
                "status": "low_conf" if analysis["confidence"] < CONFIDENCE_THRESHOLD else "identified",
                "title": _display_product_name(analysis),
                "matched_memory_name": analysis.get("matched_memory_name", ""),
                "object_type": analysis.get("object_type", ""),
                "confidence": analysis["confidence"],
                "evidence": analysis["evidence"],
                "alternatives": analysis["alternatives"],
                "needs_better_view": analysis["needs_better_view"],
                "suggestion": analysis["suggestion"],
                "resolution": analysis["resolution"],
                "sharpness": analysis["sharpness"],
                "captured_at": dt.datetime.now().strftime("%H:%M:%S"),
            },
        )

        # Optional automated second-view re-analysis loop (when caller supplies a better-view frame)
        if analysis["confidence"] < CONFIDENCE_THRESHOLD and auto_reanalyze_callback is not None:
            next_frame = auto_reanalyze_callback(analysis)
            if next_frame is not None:
                return self.identify_held_item(
                    question="Re-analyze using the newly shown back/label/barcode view.",
                    source=source,
                    use_previous_context=True,
                    frame_provider=lambda _a: next_frame,
                    analyzer_fn=analyzer_fn,
                    auto_reanalyze_callback=None,
                    status_cb=status_cb,
                    min_sharpness=min_sharpness,
                )

        return Result.success(
            data=analysis,
            verified=True,
            message=spoken,
        )

    def remember_item(
        self,
        name: str,
        source: int | str = 0,
        frame_provider: Callable[[int], Any] | None = None,
        analyzer_fn: Callable[[str, list[tuple[str, bytes]]], dict[str, Any]] | None = None,
    ) -> Result:
        """
        Saves the last captured (or newly captured) item photo + structured identification
        into `camera_memory/items.json` as `<name>`.
        """
        clean_name = " ".join(str(name or "").split()).strip()
        if not clean_name:
            return Result.failure("Tell me what name to remember this item as (e.g. 'remember this as my Anker powerbank').")

        if not HAS_CV2:
            return Result.failure("OpenCV (cv2) is not installed.")

        with self._lock:
            recent_valid = (
                self._last_capture_frame is not None
                and (time.time() - self._last_capture_ts) <= 120.0
                and frame_provider is None
            )
            frame = self._last_capture_frame.copy() if recent_valid else None
            cached_analysis = dict(self._last_analysis) if (recent_valid and self._last_analysis) else None

        if frame is None:
            ok, err, cap_meta = self.capture_sharp_still(source=source, frame_provider=frame_provider)
            if not ok:
                return Result.failure(err, data=cap_meta)
            frame = cap_meta["frame"]

        if cached_analysis is None:
            try:
                cached_analysis = self.analyze_item_frames(
                    frame_bgr=frame,
                    question=f"Describe visual identifying features of '{clean_name}' for future recognition.",
                    analyzer_fn=analyzer_fn,
                )
            except Exception:
                # Even without cloud vision, still save the crisp photo reference cleanly
                cached_analysis = {
                    "object_type": "item",
                    "brand": "Unknown",
                    "model": clean_name,
                    "confidence": 100,
                    "evidence": ["Saved visual reference photo"],
                    "alternatives": [],
                    "needs_better_view": False,
                    "suggestion": "",
                    "matched_memory_name": clean_name,
                }

        self.images_dir.mkdir(parents=True, exist_ok=True)
        ts_slug = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        img_filename = f"{_slugify(clean_name)}_{ts_slug}.jpg"
        img_path = self.images_dir / img_filename

        jpg_bytes, _, _ = _bgr_to_jpeg_bytes(frame, max_dim=1280, quality=92)
        img_path.write_bytes(jpg_bytes)
        preview_path = save_preview_thumbnail(frame, dest=self.last_preview_path)

        entry = {
            "id": f"{_slugify(clean_name)}_{ts_slug}",
            "name": clean_name,
            "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
            "image_path": str(img_path),
            "result": {
                "object_type": cached_analysis.get("object_type", "item"),
                "brand": cached_analysis.get("brand", "Unknown"),
                "model": cached_analysis.get("model", clean_name),
                "confidence": cached_analysis.get("confidence", 0),
                "evidence": cached_analysis.get("evidence", []),
            },
        }

        items = load_camera_memory(self.memory_file)
        items = [it for it in items if str(it.get("name", "")).strip().lower() != clean_name.lower()]
        items.append(entry)
        save_camera_memory(items, self.memory_file)

        # Verify persisted on disk cleanly (Rule A2)
        reloaded = load_camera_memory(self.memory_file)
        verified = (
            img_path.exists()
            and img_path.stat().st_size > 0
            and any(it.get("id") == entry["id"] for it in reloaded)
        )
        if not verified:
            return Result.failure("Failed to verify saved camera item on disk.")

        label = _display_product_name(cached_analysis)
        self._emit_preview(
            preview_path,
            {
                "status": "remembered",
                "title": f"Saved: {clean_name}",
                "matched_memory_name": clean_name,
                "object_type": entry["result"]["object_type"],
                "confidence": cached_analysis.get("confidence", 100),
                "evidence": cached_analysis.get("evidence", [])[:3],
                "needs_better_view": False,
                "suggestion": "",
                "captured_at": dt.datetime.now().strftime("%H:%M:%S"),
            },
        )
        return Result.success(
            data={"item": entry, "total_saved": len(reloaded), "preview_path": preview_path},
            verified=True,
            message=f"Remembered this item as '{clean_name}' ({label}). Saved photo & clues for next time.",
        )

    def list_saved_items(self) -> Result:
        items = load_camera_memory(self.memory_file)
        if not items:
            return Result.success(
                data={"items": []},
                verified=True,
                message="No camera items saved yet. Hold something up and say 'remember this as <name>'.",
            )
        summary = ", ".join(f"{it.get('name')} ({_display_product_name(it.get('result') or {})})" for it in items)
        return Result.success(
            data={"items": items},
            verified=True,
            message=f"Saved camera items ({len(items)}): {summary}.",
        )


# ── Optional On-Demand Live View / Security Guard HUD (Legacy Tkinter) ────────

class RoseCameraWindow:
    """
    Lazy on-demand Live Camera & Guard HUD window.
    NEVER runs a background 30fps loop unless explicitly opened via `open` or `guard_on`.
    """

    def __init__(self, root: Any = None):
        self.root = root
        self.win: Any = None
        self.running = False
        self.cap = None
        self.camera_source: int | str = 0
        self.last_frame_bgr = None
        self.prev_gray = None
        self.segment_mode = True
        self.guard_mode = False
        self.motion_boxes: list[tuple[int, int, int, int]] = []
        self._last_motion_snap = 0.0
        self.text_filter = ""
        self.detected_boxes: list[dict] = []
        self.orb_phase = 0.0
        self._detect_busy = False
        self._last_auto_detect = 0.0
        self._lock = threading.Lock()
        self._cap_lock = threading.Lock()

    def set_root(self, root: Any) -> None:
        self.root = root

    def capture_still_from_active(self, flush_grabs: int = 3, warmup_frames: int = 2) -> tuple[bool, Any, dict[str, Any]]:
        """Safely grabs a high-detail frame when the Live HUD already holds `VideoCapture`."""
        with self._cap_lock:
            cap = self.cap
            if cap is None or not cap.isOpened():
                return False, None, {"error": "Live capture handle is closed."}
            try:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, TARGET_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, TARGET_HEIGHT)
            except Exception:
                pass
            for _ in range(max(1, flush_grabs)):
                cap.grab()
            frame = None
            for _ in range(max(1, warmup_frames)):
                ok, f = cap.read()
                if ok and f is not None:
                    frame = f
            if frame is None and self.last_frame_bgr is not None:
                frame = self.last_frame_bgr.copy()
            if frame is None:
                return False, None, {"error": "Could not grab a still from active camera."}
            fh, fw = frame.shape[:2]
            return True, frame, {"width": int(fw), "height": int(fh)}

    def open(self, text_prompt: str = "", source: int | str | None = None, guard: bool | None = None) -> Result:
        if not HAS_CV2:
            return Result.failure("OpenCV (cv2) is not installed.")
        if tk is None or self.root is None:
            return Result.failure("Live camera window requires the desktop GUI window.")
        if text_prompt:
            self.text_filter = text_prompt.strip()
        if source is not None:
            self.camera_source = source
        if guard is not None:
            self.guard_mode = guard
        from ui.tk_bridge import ensure_tk_root, schedule_tk_call

        tk_root = ensure_tk_root(self.root)
        if tk_root is None:
            return Result.failure("Live camera window requires the desktop GUI window.")
        self.root = tk_root
        schedule_tk_call(self.root, self._open_on_main_thread)
        mode_note = " in Security Guard Mode (motion detection + snapshot recording)" if self.guard_mode else ""
        msg = (
            f"Opened the ROSE Vision live camera view{mode_note} "
            + (f"tracking '{self.text_filter}'." if self.text_filter else "with object highlighting.")
        )
        return Result.success(data={"guard": self.guard_mode, "filter": self.text_filter}, verified=True, message=msg)

    def close(self) -> Result:
        if tk is not None and self.root is not None:
            from ui.tk_bridge import schedule_tk_call

            schedule_tk_call(self.root, self._close_on_main_thread)
        else:
            self._close_on_main_thread()
        return Result.success(data={"closed": True}, verified=True, message="Closed the ROSE Vision live camera view.")

    def _open_on_main_thread(self):
        if self.win and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            if hasattr(self, "btn_guard"):
                self.btn_guard.configure(bg="#ff7700" if self.guard_mode else "#18181f")
            self._trigger_gemini_detection()
            return

        self.win = tk.Toplevel(self.root)
        self.win.title("R.O.S.E — VISION & SURVEILLANCE VIEW")
        self.win.geometry("920x620+120+80")
        self.win.configure(bg="#08080a")
        self.win.protocol("WM_DELETE_WINDOW", self._close_on_main_thread)

        top_bar = tk.Frame(self.win, bg="#08080a", height=46)
        top_bar.pack(fill="x", side="top", padx=14, pady=(8, 4))
        top_bar.pack_propagate(False)

        self.title_lbl = tk.Label(
            top_bar,
            text="◈ R.O.S.E VISION · LIVE VIEW",
            fg="#22d3ee",
            bg="#08080a",
            font=("Segoe UI", 11, "bold"),
        )
        self.title_lbl.pack(side="left", padx=(4, 12))

        self.prompt_var = tk.StringVar(value=self.text_filter)
        self.prompt_entry = tk.Entry(
            top_bar,
            textvariable=self.prompt_var,
            fg="#ffffff",
            bg="#141418",
            insertbackground="#22d3ee",
            relief="flat",
            font=("Consolas", 10),
            width=20,
            highlightthickness=1,
            highlightbackground="#22d3ee",
        )
        self.prompt_entry.pack(side="left", padx=6, ipady=4)
        self.prompt_entry.bind("<Return>", lambda e: self._on_set_text())

        def _hud_btn(text, cmd, bg="#18181f", fg="#e0e0e0", active_red=False):
            b = tk.Button(
                top_bar,
                text=text,
                command=cmd,
                bg="#cc1f33" if active_red else bg,
                fg="#ffffff" if active_red else fg,
                activebackground="#22d3ee",
                activeforeground="#06070b",
                relief="flat",
                borderwidth=0,
                padx=10,
                pady=5,
                cursor="hand2",
                font=("Consolas", 9, "bold"),
            )
            b.pack(side="right", padx=3)
            return b

        self.btn_clear_all = _hud_btn("CLEAR ALL", self._on_clear_all, active_red=True)
        self.btn_clear_text = _hud_btn("CLEAR TEXT", self._on_clear_text)
        self.btn_text = _hud_btn("TEXT", self._on_set_text)
        self.btn_segment = _hud_btn("SEGMENT", self._on_toggle_segment, bg="#22d3ee", fg="#06070b")
        self.btn_guard = _hud_btn(
            "GUARD",
            self._on_toggle_guard,
            bg="#ff7700" if self.guard_mode else "#18181f",
            fg="#ffffff",
        )

        self.canvas = tk.Label(self.win, bg="#050507", borderwidth=0)
        self.canvas.pack(fill="both", expand=True, padx=14, pady=(2, 12))

        self.running = True
        threading.Thread(target=self._capture_loop, daemon=True, name="ROSE-CameraLive").start()
        self._render_loop()

    def _on_toggle_guard(self):
        self.guard_mode = not self.guard_mode
        self.prev_gray = None
        self.motion_boxes = []
        self.btn_guard.configure(bg="#ff7700" if self.guard_mode else "#18181f")

    def _on_toggle_segment(self):
        self.segment_mode = not self.segment_mode
        self.btn_segment.configure(bg="#22d3ee" if self.segment_mode else "#18181f", fg="#06070b" if self.segment_mode else "#e0e0e0")
        if self.segment_mode:
            self._trigger_gemini_detection()

    def _on_set_text(self):
        self.text_filter = self.prompt_var.get().strip()
        self._trigger_gemini_detection()

    def _on_clear_text(self):
        self.prompt_var.set("")
        self.text_filter = ""
        self._trigger_gemini_detection()

    def _on_clear_all(self):
        self.prompt_var.set("")
        self.text_filter = ""
        with self._lock:
            self.detected_boxes = []
        self.motion_boxes = []
        self.segment_mode = False
        self.guard_mode = False
        self.btn_segment.configure(bg="#18181f", fg="#e0e0e0")
        self.btn_guard.configure(bg="#18181f")

    def _close_on_main_thread(self):
        self.running = False
        with self._cap_lock:
            if self.cap is not None:
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None
        if self.win and tk is not None:
            try:
                if self.win.winfo_exists():
                    self.win.destroy()
            except Exception:
                pass
        self.win = None

    def _capture_loop(self):
        src = self.camera_source
        if isinstance(src, int):
            cap = cv2.VideoCapture(src, cv2.CAP_DSHOW)
            if not cap or not cap.isOpened():
                cap = cv2.VideoCapture(src)
        else:
            cap = cv2.VideoCapture(str(src))
        try:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, TARGET_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, TARGET_HEIGHT)
        except Exception:
            pass
        with self._cap_lock:
            self.cap = cap

        while self.running and cap and cap.isOpened():
            with self._cap_lock:
                ok, frame = cap.read()
            if ok and frame is not None:
                self.last_frame_bgr = frame
                now = time.time()
                if self.guard_mode:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    gray = cv2.GaussianBlur(gray, (21, 21), 0)
                    if self.prev_gray is None or self.prev_gray.shape != gray.shape:
                        self.prev_gray = gray
                    else:
                        delta = cv2.absdiff(self.prev_gray, gray)
                        thresh = cv2.threshold(delta, 28, 255, cv2.THRESH_BINARY)[1]
                        thresh = cv2.dilate(thresh, None, iterations=2)
                        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                        m_boxes = []
                        for c in contours:
                            if cv2.contourArea(c) < 1200:
                                continue
                            x, y, bw, bh = cv2.boundingRect(c)
                            m_boxes.append((x, y, x + bw, y + bh))
                        self.motion_boxes = m_boxes
                        self.prev_gray = cv2.addWeighted(self.prev_gray, 0.85, gray, 0.15, 0)
                        if m_boxes and (now - self._last_motion_snap > 8.0):
                            self._last_motion_snap = now
                            try:
                                SECURITY_SNAP_DIR.mkdir(parents=True, exist_ok=True)
                                ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                                cv2.imwrite(str(SECURITY_SNAP_DIR / f"motion_{ts}.jpg"), frame)
                            except Exception:
                                pass
                if self.segment_mode and not self._detect_busy and (now - self._last_auto_detect > 3.5):
                    self._last_auto_detect = now
                    self._trigger_gemini_detection()
            time.sleep(0.03)

    def _trigger_gemini_detection(self):
        if self._detect_busy or self.last_frame_bgr is None:
            return
        self._detect_busy = True
        threading.Thread(target=self._run_gemini_boxes, daemon=True, name="ROSE-CamSeg").start()

    def _run_gemini_boxes(self):
        try:
            frame = self.last_frame_bgr
            if frame is None:
                return
            api_key = str(get_app_config_value("gemini_api_key", "") or "").strip()
            if not api_key:
                return
            from google import genai
            from google.genai import types

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb)
            pil_img.thumbnail((640, 640))
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=80)
            target_desc = (
                f"matching '{self.text_filter}'"
                if self.text_filter
                else "prominent objects (person, hands, phone, cup, mug, bottle, keyboard, book, laptop, etc.)"
            )
            prompt = (
                f"Detect {target_desc} in this image. "
                "Return ONLY a JSON array of objects, each with 'box_2d' ([ymin, xmin, ymax, xmax] scaled 0 to 1000) "
                "and 'label' (short English name). Maximum 6 objects."
            )
            client = genai.Client(api_key=api_key)
            resp = None
            for m_name in VISION_MODELS:
                try:
                    resp = client.models.generate_content(
                        model=m_name,
                        contents=[
                            types.Part.from_text(text=prompt),
                            types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"),
                        ],
                        config=types.GenerateContentConfig(temperature=0.1, response_mime_type="application/json"),
                    )
                    break
                except Exception:
                    continue
            if resp is None:
                return
            data = json.loads((resp.text or "[]").strip())
            if isinstance(data, list):
                with self._lock:
                    self.detected_boxes = [
                        item for item in data
                        if isinstance(item, dict) and isinstance(item.get("box_2d"), list) and len(item["box_2d"]) == 4
                    ]
        except Exception:
            pass
        finally:
            self._detect_busy = False

    def _render_loop(self):
        if not self.running or not self.win or not self.win.winfo_exists() or ImageTk is None:
            return
        try:
            frame = self.last_frame_bgr
            if frame is not None:
                vis = frame.copy()
                h, w = vis.shape[:2]
                if self.guard_mode:
                    cv2.putText(vis, "SURVEILLANCE GUARD ACTIVE", (16, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 140, 255), 2, cv2.LINE_AA)
                    for (mx1, my1, mx2, my2) in self.motion_boxes:
                        cv2.rectangle(vis, (mx1, my1), (mx2, my2), (0, 140, 255), 2)
                if self.segment_mode:
                    with self._lock:
                        boxes = list(self.detected_boxes)
                    overlay = vis.copy()
                    for item in boxes:
                        ymin, xmin, ymax, xmax = item["box_2d"]
                        x1 = max(0, min(w - 1, int(xmin * w / 1000)))
                        y1 = max(0, min(h - 1, int(ymin * h / 1000)))
                        x2 = max(0, min(w - 1, int(xmax * w / 1000)))
                        y2 = max(0, min(h - 1, int(ymax * h / 1000)))
                        if x2 <= x1 or y2 <= y1:
                            continue
                        label = str(item.get("label", "OBJECT")).upper()
                        cv2.rectangle(overlay, (x1, y1), (x2, y2), (238, 211, 34), -1)
                        cv2.rectangle(vis, (x1, y1), (x2, y2), (238, 211, 34), 2)
                        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
                        ly = max(th + 8, y1)
                        cv2.rectangle(vis, (x1, ly - th - 8), (x1 + tw + 12, ly), (180, 140, 20), -1)
                        cv2.putText(vis, label, (x1 + 6, ly - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
                    cv2.addWeighted(overlay, 0.16, vis, 0.84, 0, vis)

                rgb = cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)
                cw = max(640, self.canvas.winfo_width())
                ch = max(420, self.canvas.winfo_height())
                img = Image.fromarray(rgb)
                img.thumbnail((cw, ch))
                tk_img = ImageTk.PhotoImage(img)
                self.canvas.configure(image=tk_img)
                self.canvas.image = tk_img
        except Exception:
            pass
        if self.win and self.running:
            self.win.after(40, self._render_loop)


# ── Singleton Service & Public Tool Entrypoints ───────────────────────────────

_CAMERA_SERVICE = CameraService()
_CAMERA_HUD = RoseCameraWindow()
_CAMERA_SERVICE._hud = _CAMERA_HUD
_UI_REF: Any = None


def get_camera_service() -> CameraService:
    return _CAMERA_SERVICE


def register_camera_preview_listener(cb: Callable[[str, dict[str, Any]], None]) -> None:
    _CAMERA_SERVICE.register_preview_listener(cb)


def init_camera_controller(root: Any = None, ui_ref: Any = None) -> RoseCameraWindow:
    """Attaches optional GUI root & preview listener without starting a background video feed."""
    global _UI_REF
    if root is not None:
        _CAMERA_HUD.set_root(root)
    if ui_ref is not None:
        _UI_REF = ui_ref
        if hasattr(ui_ref, "update_camera_preview"):
            _CAMERA_SERVICE.register_preview_listener(ui_ref.update_camera_preview)
    return _CAMERA_HUD


def _ui_status_notify(message: str) -> None:
    if _UI_REF is not None and hasattr(_UI_REF, "write_log"):
        try:
            _UI_REF.write_log(f"SYS: {message}")
        except Exception:
            pass


def camera_vision_result(action: str = "identify", query: str = "") -> Result:
    """Structured Rule-A2 entrypoint for `camera_vision` across registry and Python callers."""
    act = (action or "identify").strip().lower()
    q = (query or "").strip()

    try:
        if act in ("identify", "what_is_this", "analyze", "look", "what_do_you_see", "describe"):
            q_low = q.lower()
            explicit_prev = any(w in q_low for w in ("back", "label", "barcode", "again", "retry", "re-check", "recheck", "reanalyze", "re-analyze"))
            recent_pending = False
            with _CAMERA_SERVICE._lock:
                if _CAMERA_SERVICE._pending_recheck is not None:
                    age = time.time() - float(_CAMERA_SERVICE._pending_recheck.get("captured_at", 0) or 0)
                    recent_pending = 0.0 <= age <= 90.0 and "new " not in q_low
            return _CAMERA_SERVICE.identify_held_item(
                question=q or "What am I holding?",
                use_previous_context=(explicit_prev or recent_pending),
                status_cb=_ui_status_notify,
            )

        if act in ("reanalyze", "re-analyze", "recheck", "re-check"):
            return _CAMERA_SERVICE.identify_held_item(
                question=q or "Re-analyze the held item from this clearer/back/label view.",
                use_previous_context=True,
                status_cb=_ui_status_notify,
            )

        if act in ("remember", "save_item", "remember_this"):
            return _CAMERA_SERVICE.remember_item(name=q)

        if act in ("list_saved", "memory", "items"):
            return _CAMERA_SERVICE.list_saved_items()

        if act == "close":
            return _CAMERA_HUD.close()

        if act == "guard_on":
            return _CAMERA_HUD.open(guard=True)

        if act == "guard_off":
            _CAMERA_HUD.guard_mode = False
            return Result.success(data={"guard": False}, verified=True, message="Surveillance Guard Mode disabled.")

        if act == "connect_cctv":
            src: int | str = 0 if not q or q == "0" else q
            _CAMERA_HUD.close()
            time.sleep(0.25)
            return _CAMERA_HUD.open(source=src, guard=True)

        if act == "filter":
            return _CAMERA_HUD.open(text_prompt=q)

        if act == "open":
            return _CAMERA_HUD.open(text_prompt=q)

        return Result.failure(f"Unknown camera_vision action '{action}'. Use identify, reanalyze, remember, list_saved, open, filter, guard_on, guard_off, connect_cctv, or close.")
    except Exception as exc:
        LOGGER.exception("camera_vision failed (action=%s)", act)
        return Result.failure(f"Camera error: {exc}")


def camera_vision(action: str = "identify", query: str = "") -> str:
    """Legacy string wrapper kept compatible with older callers."""
    return camera_vision_result(action=action, query=query).to_user_message()
