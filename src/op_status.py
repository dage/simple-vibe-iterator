# src/op_status.py
from __future__ import annotations

import threading
import time
from typing import Dict, Tuple, List, Dict as _Dict

# Map of worker -> (phase, started_at)
_phases: Dict[str, Tuple[str, float]] = {}
_lock = threading.Lock()
_notifications: List[_Dict[str, object]] = []


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


# --- UI notification queue ---
def enqueue_notification(
    text: str,
    *,
    color: str = "negative",
    timeout: float | int = 0,
    close_button: bool = True,
) -> None:
    """Queue a UI notification to be shown from the UI context.

    Background tasks must not call ui.notify directly; instead, push here and
    let the UI layer drain and display.
    """
    item: _Dict[str, object] = {
        "text": str(text),
        "color": str(color),
        "timeout": timeout,
        "close_button": bool(close_button),
    }
    with _lock:
        _notifications.append(item)


def drain_notifications() -> List[_Dict[str, object]]:
    """Return and clear all queued notifications."""
    with _lock:
        items = list(_notifications)
        _notifications.clear()
        return items
