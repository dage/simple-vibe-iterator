# src/op_status.py
from __future__ import annotations

import threading
import time
from typing import Dict, Tuple

_lock = threading.Lock()
# Mapping of worker -> (phase, start_time)
_status: Dict[str, Tuple[str, float]] = {}


def set_phase(worker: str, phase: str) -> None:
    """Set the current phase for a given worker.

    A blank phase clears the worker entry entirely."""
    w = worker or "main"
    new_phase = str(phase or "")
    with _lock:
        if new_phase:
            _status[w] = (new_phase, time.monotonic())
        else:
            _status.pop(w, None)


def clear_phase(worker: str) -> None:
    set_phase(worker, "")


def clear_all() -> None:
    """Clear all worker phases."""
    with _lock:
        _status.clear()


def get_phase_and_elapsed(worker: str) -> tuple[str, float]:
    w = worker or "main"
    with _lock:
        phase, started = _status.get(w, ("", 0.0))
        if not phase:
            return "", 0.0
        return phase, max(0.0, time.monotonic() - started)


def get_all_phases() -> Dict[str, tuple[str, float]]:
    """Return a snapshot of all worker phases and elapsed times."""
    with _lock:
        now = time.monotonic()
        return {
            w: (phase, max(0.0, now - started))
            for w, (phase, started) in _status.items()
        }


