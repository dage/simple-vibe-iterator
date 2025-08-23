# src/op_status.py
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_phase: str = ""
_phase_started_at: float = 0.0


def set_phase(phase: str) -> None:
    global _phase, _phase_started_at
    with _lock:
        new_phase = str(phase or "")
        if new_phase != _phase:
            _phase = new_phase
            _phase_started_at = time.monotonic() if new_phase else 0.0


def clear_phase() -> None:
    set_phase("")


def get_phase_and_elapsed() -> tuple[str, float]:
    with _lock:
        if not _phase:
            return "", 0.0
        started = _phase_started_at or time.monotonic()
        return _phase, max(0.0, time.monotonic() - started)


