"""
Acoustic Voice Biometric Authentication (Owner Voiceprint) for R.O.S.E.
- Pure NumPy DSP (MFCC vocal-tract formants + F0 pitch + spectral profile) — zero external ML model downloads.
- Continuously buffers the user's live 16kHz microphone turns in RAM.
- Enrolls/stores the owner's ("Boss") voiceprint persistently in memory/voice_profile.json.
- Verifies active speakers before allowing access to personal information (memory, notes, calendar,
  reminders, files, WhatsApp messages, etc.) and rejects unrecognized voices with:
  "You are not the boss here."
"""

from __future__ import annotations

import datetime as dt
import json
import math
import threading
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_FILE = BASE_DIR / "memory" / "voice_profile.json"

SAMPLE_RATE = 16000
FRAME_LEN = 400    # 25 ms @ 16 kHz
FRAME_HOP = 160    # 10 ms @ 16 kHz
N_FFT = 512
N_MELS = 26
N_MFCC = 16

DEFAULT_THRESHOLD = 0.74
MIN_VOICED_FRAMES = 32  # ~0.35s minimum voiced speech


def _hz_to_mel(hz: float | np.ndarray) -> float | np.ndarray:
    return 2595.0 * np.log10(1.0 + np.asarray(hz) / 700.0)


def _mel_to_hz(mel: float | np.ndarray) -> float | np.ndarray:
    return 700.0 * (10.0 ** (np.asarray(mel) / 2595.0) - 1.0)


def _build_mel_filterbank() -> np.ndarray:
    low_mel = float(_hz_to_mel(110.0))
    high_mel = float(_hz_to_mel(7600.0))
    mel_pts = np.linspace(low_mel, high_mel, N_MELS + 2)
    hz_pts = _mel_to_hz(mel_pts)
    bins = np.floor((N_FFT + 1) * hz_pts / SAMPLE_RATE).astype(int)
    fbank = np.zeros((N_MELS, N_FFT // 2 + 1), dtype=np.float32)
    for m in range(1, N_MELS + 1):
        f_left, f_center, f_right = bins[m - 1], bins[m], bins[m + 1]
        if f_center <= f_left:
            f_center = f_left + 1
        if f_right <= f_center:
            f_right = f_center + 1
        for k in range(max(0, f_left), min(f_center, N_FFT // 2 + 1)):
            fbank[m - 1, k] = (k - f_left) / float(f_center - f_left)
        for k in range(max(0, f_center), min(f_right, N_FFT // 2 + 1)):
            fbank[m - 1, k] = (f_right - k) / float(f_right - f_center)
    return fbank


def _build_dct_matrix() -> np.ndarray:
    n = np.arange(N_MELS, dtype=np.float32)
    k = np.arange(N_MFCC, dtype=np.float32)[:, None]
    dct = np.cos(math.pi / N_MELS * (n + 0.5) * k)
    dct[0, :] *= math.sqrt(1.0 / N_MELS)
    dct[1:, :] *= math.sqrt(2.0 / N_MELS)
    return dct.astype(np.float32)


def _build_lifter_weights() -> np.ndarray:
    idx = np.arange(1, N_MFCC, dtype=np.float32)
    # Emphasize formant coefficients (c2..c15) and keep c1 modest to reduce room/mic tilt drift
    w = 1.0 + 10.0 * np.sin(math.pi * idx / 24.0)
    w[0] *= 0.35
    w /= np.mean(w)
    return w.astype(np.float32)


_MEL_FBANK = _build_mel_filterbank()
_DCT_MAT = _build_dct_matrix()
_HAMMING = np.hamming(FRAME_LEN).astype(np.float32)
_LIFTER = _build_lifter_weights()
_FREQS = np.fft.rfftfreq(N_FFT, d=1.0 / SAMPLE_RATE).astype(np.float32)


def _estimate_frame_f0(frame: np.ndarray) -> float | None:
    """Fast autocorrelation pitch (70 Hz – 380 Hz) on a voiced 25ms frame."""
    centered = frame - np.mean(frame)
    energy = float(np.dot(centered, centered))
    if energy <= 1e-6:
        return None
    ac = np.correlate(centered, centered, mode="full")[len(centered) - 1:]
    min_lag = int(SAMPLE_RATE / 380.0)  # ~42 samples
    max_lag = min(len(ac) - 1, int(SAMPLE_RATE / 70.0))  # ~228 samples
    if max_lag <= min_lag:
        return None
    seg = ac[min_lag:max_lag + 1]
    peak_idx = int(np.argmax(seg))
    peak_val = float(seg[peak_idx])
    if peak_val / ac[0] < 0.36:
        return None
    lag = min_lag + peak_idx
    return SAMPLE_RATE / float(lag)


def extract_voice_embedding(pcm16_bytes: bytes) -> tuple[dict | None, str]:
    """
    Extracts an acoustic speaker embedding from 16kHz signed 16-bit mono PCM bytes.
    Returns (embedding_dict, error_reason).
    """
    if not pcm16_bytes or len(pcm16_bytes) < SAMPLE_RATE // 2 * 2:
        return None, "Audio sample is too short. Please speak a full sentence naturally."

    samples = np.frombuffer(pcm16_bytes[: (len(pcm16_bytes) // 2) * 2], dtype=np.int16).astype(np.float32) / 32768.0
    if len(samples) < FRAME_LEN + FRAME_HOP:
        return None, "Audio buffer too short for analysis."

    # Raw frame RMS (before pre-emphasis) for VAD
    n_frames = 1 + (len(samples) - FRAME_LEN) // FRAME_HOP
    strides = (samples.strides[0] * FRAME_HOP, samples.strides[0])
    raw_frames = np.lib.stride_tricks.as_strided(samples, shape=(n_frames, FRAME_LEN), strides=strides)

    frame_rms = np.sqrt(np.mean(raw_frames ** 2, axis=1) + 1e-12)
    abs_floor = 0.008  # ~262 int16 RMS
    rel_floor = float(np.percentile(frame_rms, 90)) * 0.22
    thresh = max(abs_floor, rel_floor)

    # Zero-crossing rate check rejects high-frequency broadband noise/clicks
    zcr = np.mean(np.abs(np.diff(np.signbit(raw_frames), axis=1)), axis=1)
    voiced_idx = np.where((frame_rms >= thresh) & (zcr < 0.42))[0]

    if len(voiced_idx) < MIN_VOICED_FRAMES:
        return None, f"Not enough clear speech detected ({len(voiced_idx)} frames). Please speak a bit louder or longer."

    # Pre-emphasis on whole signal for spectral clarity
    pre = np.empty_like(samples)
    pre[0] = samples[0]
    pre[1:] = samples[1:] - 0.97 * samples[:-1]
    pre_frames = np.lib.stride_tricks.as_strided(pre, shape=(n_frames, FRAME_LEN), strides=strides)

    win_frames = pre_frames[voiced_idx] * _HAMMING
    spec = np.fft.rfft(win_frames, n=N_FFT, axis=1)
    mag = np.abs(spec)
    power = (mag ** 2) / N_FFT

    mel_e = np.dot(power, _MEL_FBANK.T) + 1e-8
    log_mel = np.log(mel_e)
    mfcc = np.dot(log_mel, _DCT_MAT.T)[:, 1:N_MFCC]  # drop c0 loudness -> c1..c15
    mfcc = mfcc * _LIFTER[None, :]

    mfcc_mean = np.mean(mfcc, axis=0)
    norm_m = float(np.linalg.norm(mfcc_mean))
    if norm_m < 1e-6:
        return None, "Could not extract speech resonances."
    mfcc_unit = (mfcc_mean / norm_m).astype(np.float32)

    # Pitch F0 across voiced frames
    f0_vals = []
    # Sample up to ~120 evenly spaced voiced frames for speed
    step = max(1, len(voiced_idx) // 120)
    for idx in voiced_idx[::step]:
        f0 = _estimate_frame_f0(raw_frames[idx])
        if f0 is not None:
            f0_vals.append(math.log2(f0 / 100.0))

    pitch_med = float(np.median(f0_vals)) if len(f0_vals) >= 4 else None

    # Spectral centroid (kHz)
    mag_sum = np.sum(mag, axis=1) + 1e-8
    centroids_hz = np.sum(mag * _FREQS[None, :], axis=1) / mag_sum
    centroid_khz = float(np.median(centroids_hz) / 1000.0)

    return {
        "mfcc": [round(float(v), 5) for v in mfcc_unit.tolist()],
        "pitch_log2": round(pitch_med, 5) if pitch_med is not None else None,
        "centroid_khz": round(centroid_khz, 4),
        "frames": int(len(voiced_idx)),
    }, ""


def score_voice_embeddings(template: dict, candidate: dict) -> float:
    """Computes a calibrated [0.0, 1.0] speaker match score between template and candidate."""
    v1 = np.asarray(template.get("mfcc") or [], dtype=np.float32)
    v2 = np.asarray(candidate.get("mfcc") or [], dtype=np.float32)
    if v1.size == 0 or v1.size != v2.size:
        return 0.0

    n1 = float(np.linalg.norm(v1))
    n2 = float(np.linalg.norm(v2))
    if n1 < 1e-6 or n2 < 1e-6:
        return 0.0

    cos_sim = float(np.dot(v1, v2) / (n1 * n2))
    # Clamp negative correlations cleanly
    timbre_score = max(0.0, min(1.0, cos_sim))

    # Pitch comparison (semitone difference with Gaussian kernel; tolerant of natural expression)
    p1 = template.get("pitch_log2")
    p2 = candidate.get("pitch_log2")
    if p1 is not None and p2 is not None:
        semitones = abs(float(p2) - float(p1)) * 12.0
        pitch_score = math.exp(-((semitones / 6.0) ** 2))
    else:
        pitch_score = timbre_score

    # Spectral centroid agreement
    c1 = template.get("centroid_khz")
    c2 = candidate.get("centroid_khz")
    if c1 is not None and c2 is not None:
        cdiff = abs(float(c2) - float(c1))
        centroid_score = math.exp(-((cdiff / 1.1) ** 2))
    else:
        centroid_score = timbre_score

    return round(0.62 * timbre_score + 0.26 * pitch_score + 0.12 * centroid_score, 4)


def load_voice_profile() -> dict:
    default = {
        "enrolled": False,
        "owner_name": "Boss",
        "threshold": DEFAULT_THRESHOLD,
        "templates": [],
        "created_at": "",
        "updated_at": "",
    }
    if not PROFILE_FILE.exists():
        return default
    try:
        data = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        default.update(data)
        return default
    except Exception:
        return default


def save_voice_profile(profile: dict) -> None:
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_FILE.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")


class VoicePrintMonitor:
    """Thread-safe rolling speech monitor + owner biometric gate for R.O.S.E."""

    def __init__(self):
        self._lock = threading.Lock()
        # Store up to ~6.5 seconds of 16kHz PCM chunks (each 1024 samples = 64ms -> ~102 chunks)
        self._ring: deque[bytes] = deque(maxlen=110)
        self._voiced_ring: deque[bytes] = deque(maxlen=90)
        self._last_voiced_ts = 0.0
        self._last_verdict: dict[str, Any] = {
            "verified": True,
            "enrolled": False,
            "score": 1.0,
            "status": "open",
            "frames": 0,
        }
        self._profile = load_voice_profile()

    def reload_profile(self) -> dict:
        prof = load_voice_profile()
        with self._lock:
            self._profile = prof
        return prof

    def is_enrolled(self) -> bool:
        with self._lock:
            return bool(self._profile.get("enrolled") and self._profile.get("templates"))

    def reset_turn_buffer(self) -> None:
        with self._lock:
            self._voiced_ring.clear()

    def feed_pcm(self, pcm16: bytes, rms: int = 0) -> None:
        """Called from _mic_thread_loop with 16kHz mono PCM16 chunks."""
        if not pcm16:
            return
        now_ts = dt.datetime.now().timestamp()
        with self._lock:
            self._ring.append(pcm16)
            if rms > 220:
                if self._last_voiced_ts and (now_ts - self._last_voiced_ts) > 2.0:
                    # New utterance after >2s silence -> prevent mixing different speakers
                    self._voiced_ring.clear()
                self._voiced_ring.append(pcm16)
                self._last_voiced_ts = now_ts

    def get_recent_speech_pcm(self) -> bytes:
        with self._lock:
            if len(self._voiced_ring) >= 12:
                return b"".join(self._voiced_ring)
            return b"".join(self._ring)

    def enroll_from_pcm(self, pcm16: bytes, owner_name: str = "Boss", replace: bool = False) -> tuple[bool, str]:
        emb, err = extract_voice_embedding(pcm16)
        if not emb:
            return False, err

        with self._lock:
            prof = load_voice_profile() if replace is False else {
                "enrolled": False,
                "owner_name": owner_name or "Boss",
                "threshold": DEFAULT_THRESHOLD,
                "templates": [],
                "created_at": "",
                "updated_at": "",
            }
            templates = list(prof.get("templates") or [])
            templates.append(emb)
            # Keep most recent 6 voiceprint samples for multi-phrase robustness
            templates = templates[-6:]
            now_iso = dt.datetime.now().isoformat(timespec="seconds")
            if not prof.get("created_at"):
                prof["created_at"] = now_iso
            prof["enrolled"] = True
            prof["owner_name"] = (owner_name or prof.get("owner_name") or "Boss").strip() or "Boss"
            prof["updated_at"] = now_iso
            prof["templates"] = templates
            prof.setdefault("threshold", DEFAULT_THRESHOLD)
            save_voice_profile(prof)
            self._profile = prof

        count = len(prof["templates"])
        return True, (
            f"Your voice has been stored and locked ({count} sample{'s' if count != 1 else ''} saved for {prof['owner_name']}). "
            "If an unrecognized person asks for your personal information, I will reject them and say: 'You are not the boss here.'"
        )

    def verify_pcm(self, pcm16: bytes) -> dict:
        with self._lock:
            prof = dict(self._profile)

        if not prof.get("enrolled") or not prof.get("templates"):
            return {
                "enrolled": False,
                "verified": True,
                "score": 1.0,
                "threshold": DEFAULT_THRESHOLD,
                "status": "not_enrolled",
                "reason": "No owner voice profile is enrolled yet.",
            }

        cand, err = extract_voice_embedding(pcm16)
        if not cand:
            return {
                "enrolled": True,
                "verified": False,
                "score": 0.0,
                "threshold": float(prof.get("threshold", DEFAULT_THRESHOLD)),
                "status": "insufficient_audio",
                "reason": err,
            }

        templates = prof.get("templates") or []
        scores = sorted((score_voice_embeddings(t, cand) for t in templates), reverse=True)
        # Use best match blended slightly with second-best if multiple templates exist
        if len(scores) >= 2:
            best = 0.70 * scores[0] + 0.30 * scores[1]
        else:
            best = scores[0]

        threshold = float(prof.get("threshold", DEFAULT_THRESHOLD))
        passed = best >= threshold
        result = {
            "enrolled": True,
            "verified": passed,
            "score": round(best, 3),
            "threshold": threshold,
            "frames": cand["frames"],
            "status": "verified" if passed else "rejected",
            "reason": (
                f"Voice matched {prof.get('owner_name', 'Boss')} (score={best:.2f})."
                if passed
                else f"Voice did NOT match the registered boss (score={best:.2f} < {threshold:.2f})."
            ),
        }
        with self._lock:
            self._last_verdict = result
        return result

    def verify_recent_speaker(self) -> dict:
        pcm = self.get_recent_speech_pcm()
        res = self.verify_pcm(pcm)
        # If very short phrase wasn't enough and user was ALREADY verified cleanly in this session turn, check raw ring
        if res.get("status") == "insufficient_audio":
            with self._lock:
                raw = b"".join(self._ring)
            if raw and len(raw) > len(pcm):
                res2 = self.verify_pcm(raw)
                if res2.get("status") != "insufficient_audio":
                    return res2
        return res

    def clear_profile(self) -> str:
        prof = {
            "enrolled": False,
            "owner_name": "Boss",
            "threshold": DEFAULT_THRESHOLD,
            "templates": [],
            "created_at": "",
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        save_voice_profile(prof)
        with self._lock:
            self._profile = prof
            self._last_verdict = {
                "verified": True,
                "enrolled": False,
                "score": 1.0,
                "status": "cleared",
            }
        return "Owner voice lock profile cleared. Voice security is now disabled."


# Singleton monitor shared between microphone thread and tool handlers
voice_monitor = VoicePrintMonitor()


def voice_security_control(
    action: str = "status",
    owner_name: str = "Boss",
) -> str:
    """
    Tool entrypoint for storing, verifying, checking, or removing the owner's voiceprint.
    action: enroll | add_sample | verify | status | clear
    """
    act = (action or "status").strip().lower()

    if act in ("enroll", "save", "register", "store", "learn", "lock"):
        pcm = voice_monitor.get_recent_speech_pcm()
        ok, msg = voice_monitor.enroll_from_pcm(pcm, owner_name=owner_name, replace=(act == "enroll" and not voice_monitor.is_enrolled()))
        if not ok:
            return (
                f"Could not capture a long enough sample yet ({msg}) "
                "Please ask the user to speak a clear sentence (for example, 'Rose, remember my voice as the boss of this computer') and then call voice_security(action='enroll')."
            )
        return msg

    if act in ("add_sample", "strengthen", "update"):
        pcm = voice_monitor.get_recent_speech_pcm()
        ok, msg = voice_monitor.enroll_from_pcm(pcm, owner_name=owner_name, replace=False)
        return msg if ok else f"Could not add voice sample: {msg}"

    if act in ("verify", "check", "authenticate"):
        res = voice_monitor.verify_recent_speaker()
        if not res["enrolled"]:
            return "No owner voice is enrolled yet, so voice security is currently open."
        if res["verified"]:
            return f"VOICE VERIFIED: {res['reason']}"
        if res.get("status") == "insufficient_audio":
            return (
                "ACCESS DENIED (VOICE NOT CLEARLY VERIFIED): Ask the speaker to say a clear sentence first, or verify their identity. "
                "Do NOT reveal personal information until their voice matches."
            )
        return (
            f"ACCESS DENIED (UNRECOGNIZED VOICE, score={res['score']:.2f}): "
            "Reject the request immediately and say: 'You are not the boss here.'"
        )

    if act in ("clear", "reset", "delete", "disable", "remove"):
        # Require verified boss voice before disabling voice lock!
        if voice_monitor.is_enrolled():
            res = voice_monitor.verify_recent_speaker()
            if not res["verified"]:
                return (
                    f"ACCESS DENIED: Only the registered boss's voice can disable voice security (score={res['score']:.2f}). "
                    "Say: 'You are not the boss here.'"
                )
        return voice_monitor.clear_profile()

    # Status
    prof = voice_monitor.reload_profile()
    if prof.get("enrolled") and prof.get("templates"):
        return (
            f"Voice Security: ACTIVE for '{prof.get('owner_name', 'Boss')}' "
            f"({len(prof['templates'])} voiceprint sample(s) saved, last updated {prof.get('updated_at', 'recently')})."
        )
    return "Voice Security: NOT ENROLLED yet. Say 'Rose, store my voice' while speaking naturally to lock your personal information."

