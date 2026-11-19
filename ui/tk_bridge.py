"""Lazy on-demand Tkinter host for legacy Toplevel popups under PySide6.

Keeps memory at 0 MB and CPU at 0% unless a legacy Tk helper window
(Interview Co-Pilot, Spatial Research Board, Busy Bar, or Live Camera Tk view)
is actually opened while R.O.S.E runs in the default PySide6 UI.
When R.O.S.E runs with `--legacy`, `ensure_tk_root` simply returns the active tk.Tk.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from typing import Any, Callable, Optional

_HOST_LOCK = threading.Lock()
_TK_ROOT: Optional[tk.Tk] = None
_TK_THREAD_ID: Optional[int] = None
_CALL_QUEUE: queue.Queue[tuple[int, Callable[[], Any]]] = queue.Queue()
_SHUTTING_DOWN = False


def is_real_tk(obj: Any) -> bool:
    return obj is not None and hasattr(obj, "_w") and hasattr(obj, "tk")


def is_tk_thread(root_obj: Any = None) -> bool:
    """True only if currently executing on the thread owning the Tk root."""
    if is_real_tk(root_obj) and root_obj is not _TK_ROOT:
        return threading.current_thread() is threading.main_thread()
    if _TK_THREAD_ID is not None:
        return threading.get_ident() == _TK_THREAD_ID
    return threading.current_thread() is threading.main_thread()


def _drain_queue() -> None:
    global _TK_ROOT
    root = _TK_ROOT
    if root is None:
        return
    try:
        while True:
            delay_ms, fn = _CALL_QUEUE.get_nowait()
            if delay_ms <= 0:
                try:
                    fn()
                except Exception:
                    pass
            else:
                root.after(int(delay_ms), fn)
    except queue.Empty:
        pass
    if not _SHUTTING_DOWN:
        try:
            root.after(40, _drain_queue)
        except Exception:
            pass


def ensure_tk_root(root_obj: Any = None) -> Optional[tk.Tk]:
    """Return an active tk.Tk instance, launching a hidden daemon host only on demand."""
    global _TK_ROOT, _TK_THREAD_ID, _SHUTTING_DOWN
    if is_real_tk(root_obj):
        return root_obj

    with _HOST_LOCK:
        if _TK_ROOT is not None:
            return _TK_ROOT
        _SHUTTING_DOWN = False
        ready = threading.Event()
        err_holder: list[BaseException] = []

        def _tk_main() -> None:
            global _TK_ROOT, _TK_THREAD_ID
            try:
                root = tk.Tk()
                root.withdraw()
                _TK_ROOT = root
                _TK_THREAD_ID = threading.get_ident()
                root.after(40, _drain_queue)
                ready.set()
                root.mainloop()
            except BaseException as exc:
                err_holder.append(exc)
                ready.set()
            finally:
                _TK_ROOT = None
                _TK_THREAD_ID = None

        t = threading.Thread(target=_tk_main, daemon=True, name="rose-tk-popup-host")
        t.start()
        ready.wait(timeout=4.0)
        if err_holder:
            return None
        return _TK_ROOT


def schedule_tk_call(root_obj: Any, fn: Callable[[], Any], delay_ms: int = 0) -> Optional[tk.Tk]:
    """Ensure a Tk root exists and execute `fn` safely on its owning thread."""
    if is_real_tk(root_obj) and root_obj is not _TK_ROOT:
        root_obj.after(max(0, int(delay_ms)), fn)
        return root_obj

    tk_root = ensure_tk_root(root_obj)
    if tk_root is None:
        return None
    if is_tk_thread(tk_root) and delay_ms <= 0:
        fn()
    else:
        _CALL_QUEUE.put((max(0, int(delay_ms)), fn))
    return tk_root


def shutdown_tk_host() -> None:
    """Cleanly stop the lazy Tk host if it was started."""
    global _SHUTTING_DOWN, _TK_ROOT
    _SHUTTING_DOWN = True
    root = _TK_ROOT
    if root is not None:
        def _quit() -> None:
            try:
                root.quit()
                root.destroy()
            except Exception:
                pass

        _CALL_QUEUE.put((0, _quit))


__all__ = [
    "is_real_tk",
    "is_tk_thread",
    "ensure_tk_root",
    "schedule_tk_call",
    "shutdown_tk_host",
]

