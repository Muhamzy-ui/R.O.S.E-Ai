"""
R.O.S.E (Project Mirror) - Lightweight Non-Blocking Sound Effects Manager.
Pure Windows MCI (`winmm`) audio playback with strict same-thread MCI affinity.
"""

from __future__ import annotations

import ctypes
import itertools
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    _winmm = ctypes.windll.winmm
    _mciSendString = _winmm.mciSendStringW
    _mciSendString.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_void_p,
    ]
    _HAS_WINMM = True
except Exception:
    _mciSendString = None
    _HAS_WINMM = False

_alias_counter = itertools.count(1)

_SFX_DIR = BASE_DIR / "SFX"
_HUD_FILE = _SFX_DIR / "HUD.mp3"
_START_FILE = _SFX_DIR / "Start.mp3"
_THINK_FILE = _SFX_DIR / "Think.mp3"
_DONE_FILE = _SFX_DIR / "Done.mp3"
_ERROR_FILE = _SFX_DIR / "Error.mp3"


class _MciTrack:
    """Single Windows MCI MP3 playback instance (must be used on its owning thread)."""

    def __init__(self, path: Path, volume: float):
        self.alias = f"rose_sfx_{next(_alias_counter)}"
        self._opened = False
        self._closed = False
        self.path = str(path)
        self._volume = max(0.0, min(1.0, float(volume)))

    def open(self) -> bool:
        if not _HAS_WINMM or _mciSendString is None:
            return False
        cmd = f'open "{self.path}" type mpegvideo alias {self.alias}'
        err = _mciSendString(cmd, None, 0, None)
        if err != 0:
            return False
        self._opened = True
        try:
            vol = int(self._volume * 1000)
            _mciSendString(f"setaudio {self.alias} volume to {vol}", None, 0, None)
        except Exception:
            pass
        return True

    def play(self) -> bool:
        if not self._opened or _mciSendString is None:
            return False
        return _mciSendString(f"play {self.alias}", None, 0, None) == 0

    def is_playing(self) -> bool:
        if not self._opened or self._closed or _mciSendString is None:
            return False
        buf = ctypes.create_unicode_buffer(64)
        err = _mciSendString(f"status {self.alias} mode", buf, 64, None)
        if err != 0:
            return False
        return buf.value.strip().lower() == "playing"

    def stop(self) -> None:
        if self._opened and not self._closed and _mciSendString is not None:
            try:
                _mciSendString(f"stop {self.alias}", None, 0, None)
            except Exception:
                pass

    def close(self) -> None:
        if self._opened and not self._closed and _mciSendString is not None:
            try:
                _mciSendString(f"close {self.alias}", None, 0, None)
            except Exception:
                pass
            self._closed = True


class SoundManager:
    """Thread-safe ambient & foreground SFX playback controller."""

    def __init__(self):
        self._enabled = True
        self._ambient_enabled = True
        self._ambient_proc = None
        self._ambient_playing = False
        self._volume = 0.20
        self._ambient_stop = None
        self._ambient_thread = None
        self._foreground_proc = None
        self._foreground_playing = False
        self._foreground_stop = None
        self._foreground_thread = None
        self._foreground_tag = ""
        self._all_sound_procs = set()
        self._lock = threading.RLock()

    @staticmethod
    def _close_track_on_owner_thread(proc: _MciTrack | None) -> None:
        if not proc:
            return
        try:
            proc.stop()
        except Exception:
            pass
        try:
            proc.close()
        except Exception:
            pass

    def _start_track(self, path: Path, volume: float) -> _MciTrack:
        if not _HAS_WINMM:
            raise RuntimeError("winmm unavailable")
        track = _MciTrack(path, volume)
        if not track.open():
            raise RuntimeError(f"MCI failed to open: {path}")
        if not track.play():
            track.close()
            raise RuntimeError(f"MCI failed to play: {path}")
        with self._lock:
            self._all_sound_procs.add(track)
        return track

    def is_ambient_playing(self) -> bool:
        with self._lock:
            return bool(self._ambient_playing and self._ambient_thread and self._ambient_thread.is_alive())

    def start_ambient(self):
        if not self._ambient_enabled or not _HUD_FILE.exists():
            return
        with self._lock:
            if not self._enabled or self._foreground_playing:
                return
            if self._ambient_thread and self._ambient_thread.is_alive():
                return
            stop_event = threading.Event()
            worker = threading.Thread(
                target=self._loop_ambient,
                args=(stop_event,),
                daemon=True,
                name="ROSE-SFX-Ambient",
            )
            self._ambient_stop = stop_event
            self._ambient_thread = worker
        worker.start()

    def _loop_ambient(self, stop_event: threading.Event):
        while not stop_event.is_set():
            with self._lock:
                if not self._enabled or not self._ambient_enabled or self._ambient_stop is not stop_event:
                    break
                volume = max(0.03, min(0.18, self._volume * 0.55)) if self._volume > 0 else 0.0

            proc = None
            try:
                proc = self._start_track(_HUD_FILE, volume)
                with self._lock:
                    if self._ambient_stop is not stop_event or not self._enabled:
                        self._all_sound_procs.discard(proc)
                        break
                    self._ambient_proc = proc
                    self._ambient_playing = True

                while proc.is_playing() and not stop_event.wait(0.06):
                    pass
            except Exception:
                break
            finally:
                with self._lock:
                    if proc is not None:
                        self._all_sound_procs.discard(proc)
                    if self._ambient_proc is proc:
                        self._ambient_proc = None
                    self._ambient_playing = False
                self._close_track_on_owner_thread(proc)

            if stop_event.is_set():
                break
            time.sleep(0.08)

        with self._lock:
            if self._ambient_stop is stop_event:
                self._ambient_stop = None
            if self._ambient_thread and self._ambient_thread.ident == threading.get_ident():
                self._ambient_thread = None

    def _stop_ambient(self):
        with self._lock:
            stop_event = self._ambient_stop
            worker = self._ambient_thread
            self._ambient_stop = None
            self._ambient_thread = None
        if stop_event:
            stop_event.set()
        if worker and worker.is_alive() and worker.ident != threading.get_ident():
            worker.join(timeout=0.35)

    def _stop_foreground(self, wait: bool = True):
        with self._lock:
            stop_event = self._foreground_stop
            worker = self._foreground_thread
            self._foreground_stop = None
            self._foreground_thread = None
            self._foreground_tag = ""
        if stop_event:
            stop_event.set()
        if wait and worker and worker.is_alive() and worker.ident != threading.get_ident():
            worker.join(timeout=0.35)

    def _play_foreground(
        self,
        path: Path,
        tag: str,
        loop: bool = False,
        volume_factor: float = 1.0,
        pause_ambient: bool = True,
    ):
        if not path.exists():
            return
        with self._lock:
            if not self._enabled:
                return
            if loop and self._foreground_tag == tag and self._foreground_thread and self._foreground_thread.is_alive():
                return
            base_volume = self._volume
        if pause_ambient:
            self._stop_ambient()
        self._stop_foreground()

        stop_event = threading.Event()
        worker = threading.Thread(
            target=self._foreground_worker,
            args=(path, tag, stop_event, loop, max(0.0, min(1.0, base_volume * volume_factor)), pause_ambient),
            daemon=True,
            name=f"ROSE-SFX-{tag}",
        )
        with self._lock:
            self._foreground_stop = stop_event
            self._foreground_thread = worker
            self._foreground_tag = tag
        worker.start()

    def _foreground_worker(
        self,
        path: Path,
        tag: str,
        stop_event: threading.Event,
        loop: bool,
        volume: float,
        resume_ambient: bool,
    ):
        while not stop_event.is_set():
            proc = None
            try:
                proc = self._start_track(path, volume)
                with self._lock:
                    if self._foreground_stop is not stop_event or not self._enabled:
                        self._all_sound_procs.discard(proc)
                        break
                    self._foreground_proc = proc
                    self._foreground_playing = True

                while proc.is_playing() and not stop_event.wait(0.06):
                    pass
            except Exception:
                break
            finally:
                with self._lock:
                    if proc is not None:
                        self._all_sound_procs.discard(proc)
                    if self._foreground_proc is proc:
                        self._foreground_proc = None
                    self._foreground_playing = False
                self._close_track_on_owner_thread(proc)

            if not loop or stop_event.is_set():
                break
            time.sleep(0.06)

        with self._lock:
            if self._foreground_stop is stop_event:
                self._foreground_stop = None
                self._foreground_thread = None
                self._foreground_tag = ""
            should_restart = resume_ambient and self._enabled and self._foreground_stop is None
        if should_restart:
            self.start_ambient()

    def play_startup(self):
        self._play_foreground(_START_FILE, tag="start", loop=False, volume_factor=0.95)

    def play_success(self):
        self._play_foreground(_DONE_FILE, tag="done", loop=False, volume_factor=0.68, pause_ambient=False)

    def play_error(self):
        self._play_foreground(_ERROR_FILE, tag="error", loop=False, volume_factor=0.95)

    def start_thinking(self):
        self._play_foreground(_THINK_FILE, tag="think", loop=True, volume_factor=0.82, pause_ambient=False)

    def stop_thinking(self):
        with self._lock:
            is_thinking = self._foreground_tag == "think"
        if is_thinking:
            self._stop_foreground(wait=False)

    def toggle(self) -> bool:
        self.set_enabled(not self._enabled)
        return self._enabled

    def set_enabled(self, enabled: bool):
        enabled = bool(enabled)
        with self._lock:
            self._enabled = enabled
        if enabled:
            self.start_ambient()
        else:
            self._stop_ambient()
            self._stop_foreground()

    def set_volume(self, volume: float):
        with self._lock:
            self._volume = max(0.0, min(1.0, float(volume)))
            fg_tag = self._foreground_tag
            can_restart_ambient = self._enabled and not fg_tag
        if fg_tag == "think":
            self._stop_foreground()
            self.start_thinking()
        elif can_restart_ambient:
            self._stop_ambient()
            self.start_ambient()

    def stop_all(self):
        with self._lock:
            self._enabled = False
            ambient_stop = self._ambient_stop
            ambient_worker = self._ambient_thread
            foreground_stop = self._foreground_stop
            foreground_worker = self._foreground_thread
            self._ambient_stop = None
            self._ambient_thread = None
            self._foreground_stop = None
            self._foreground_thread = None
            self._foreground_tag = ""
        if ambient_stop:
            ambient_stop.set()
        if foreground_stop:
            foreground_stop.set()
        if ambient_worker and ambient_worker.is_alive() and ambient_worker.ident != threading.get_ident():
            ambient_worker.join(timeout=0.35)
        if foreground_worker and foreground_worker.is_alive() and foreground_worker.ident != threading.get_ident():
            foreground_worker.join(timeout=0.35)

    def get_volume(self) -> float:
        return self._volume
