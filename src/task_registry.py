from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict


_tasks: Dict[str, asyncio.Task[Any]] = {}
_lock = threading.Lock()


def register_task(worker: str, task: asyncio.Task[Any]) -> None:
    name = worker or "default"
    with _lock:
        _tasks[name] = task


def cancel_task(worker: str) -> bool:
    name = worker or "default"
    with _lock:
        task = _tasks.get(name)
    if task is None:
        return False
    if task.done():
        with _lock:
            _tasks.pop(name, None)
        return False
    task.cancel()
    with _lock:
        _tasks.pop(name, None)
    return True


def remove_task(worker: str) -> None:
    name = worker or "default"
    with _lock:
        _tasks.pop(name, None)


def clear_all_tasks() -> None:
    with _lock:
        tasks = list(_tasks.items())
        _tasks.clear()
    for _, task in tasks:
        try:
            if not task.done():
                task.cancel()
        except Exception:
            pass
