# src/tool_logging.py
"""Append-only logging for AI tool calls."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict
import json
import os
import threading

_LOCK = threading.Lock()
_LOG_PATH = Path(os.getenv("TOOL_CALL_LOG", Path("artifacts") / "tool_calls.log"))


def log_tool_call(*, model: str, tool: str, code: str, output: str) -> None:
    entry: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "model": model,
        "tool": tool,
        "code_preview": code.strip()[:200],
        "output": _safe_json(output),
    }
    path = _LOG_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(entry, ensure_ascii=False)
    except Exception:
        return
    with _LOCK:
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(payload + "\n")
        except Exception:
            pass


def _safe_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return raw
