# src/prefs.py
from __future__ import annotations

import json
from pathlib import Path


_PREFS_PATH = Path.home() / ".simple-vibe-iterator" / "prefs.json"


def get(key: str, default: str = "") -> str:
    try:
        data = json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
        return str(data.get(str(key), default))
    except Exception:
        return default


def set(key: str, value: str) -> None:
    try:
        try:
            data = json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        data[str(key)] = str(value)
        _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PREFS_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


