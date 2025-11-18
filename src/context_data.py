from __future__ import annotations

"""Per-task context storage for agent session data."""

from contextvars import ContextVar, Token
from typing import Any, Dict, Optional

_CTX: ContextVar[Optional[Dict[str, Any]]] = ContextVar("agent_context_data", default=None)
_SNAPSHOTS: Dict[str, Dict[str, Any]] = {}


def _current_data() -> Optional[Dict[str, Any]]:
    return _CTX.get()


def _ensure_data() -> Dict[str, Any]:
    data = _CTX.get()
    if data is None:
        data = {}
        _CTX.set(data)
    return data


def _worker_key(data: Optional[Dict[str, Any]]) -> str:
    if not data:
        return ""
    try:
        worker = str(data.get("worker_id", "") or "").strip()
    except Exception:
        worker = ""
    return worker


def _update_snapshot(data: Optional[Dict[str, Any]]) -> None:
    worker = _worker_key(data)
    if worker:
        _SNAPSHOTS[worker] = dict(data or {})


def _remove_snapshot_for(data: Optional[Dict[str, Any]]) -> None:
    worker = _worker_key(data)
    if worker:
        _SNAPSHOTS.pop(worker, None)


def has_context() -> bool:
    return _CTX.get() is not None


def reset_context(defaults: Optional[Dict[str, Any]] = None) -> Token:
    data = dict(defaults or {})
    token = _CTX.set(data)
    _update_snapshot(data)
    return token


def restore_context(token: Token) -> None:
    current = _CTX.get()
    _CTX.reset(token)
    _remove_snapshot_for(current)
    restored = _CTX.get()
    if restored:
        _update_snapshot(restored)


def get(key: str, default: Any = None) -> Any:
    data = _current_data()
    if data is None:
        return default
    return data.get(key, default)


def set(key: str, value: Any) -> None:
    data = _ensure_data()
    data[key] = value
    _update_snapshot(data)


def increment(key: str, delta: int = 1) -> int:
    data = _ensure_data()
    current = data.get(key, 0)
    if not isinstance(current, (int, float)):
        current = 0
    new_value = current + delta
    data[key] = new_value
    _update_snapshot(data)
    return new_value


def reset(key: str, value: Any | None = None) -> None:
    data = _ensure_data()
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    _update_snapshot(data)


def get_all() -> Dict[str, Any]:
    data = _current_data()
    return dict(data) if data is not None else {}


def get_worker_snapshot(worker: str) -> Dict[str, Any]:
    w = (worker or "").strip()
    return dict(_SNAPSHOTS.get(w, {}))


def get_all_snapshots() -> Dict[str, Dict[str, Any]]:
    return {worker: dict(payload) for worker, payload in _SNAPSHOTS.items()}
