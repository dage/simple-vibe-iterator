# src/op_status.py
from __future__ import annotations

import threading
import time
from typing import Dict, Tuple

# Map of worker -> (phase, started_at)
_phases: Dict[str, Tuple[str, float]] = {}
_lock = threading.Lock()


def set_phase(worker: str, phase: str) -> None:
    """Set current phase text for a given worker."""
    w = worker or "default"
    with _lock:
        if phase:
            _phases[w] = (phase, time.monotonic())
        else:
            _phases.pop(w, None)


def clear_phase(worker: str) -> None:
    set_phase(worker, "")


def clear_all() -> None:
    """Remove all worker phases."""
    with _lock:
        _phases.clear()


def get_all_phases() -> Dict[str, Tuple[str, float]]:
    """Return mapping of worker -> (phase, elapsed_seconds)."""
    with _lock:
        now = time.monotonic()
        return {w: (p, max(0.0, now - ts)) for w, (p, ts) in _phases.items()}
