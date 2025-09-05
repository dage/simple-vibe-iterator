from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Any


# Store parameters in project root so they can be versioned/shared.
_DEFAULT_PATH = (Path(__file__).resolve().parents[1] / "model_params.json").resolve()

def _path() -> Path:
    custom = os.getenv("MODEL_PARAMS_PATH", "").strip() if 'os' in globals() else ""
    if custom:
        return Path(custom).expanduser().resolve()
    return _DEFAULT_PATH

def _effective_path() -> Path:
    env_path = os.getenv("MODEL_PARAMS_PATH", "").strip()
    if env_path:
        try:
            return Path(env_path).expanduser().resolve()
        except Exception:
            return _DEFAULT_PATH
    return _DEFAULT_PATH


def _read_all() -> Dict[str, Dict[str, str]]:
    try:
        raw = _effective_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            # Ensure { model_slug: {param: str} }
            out: Dict[str, Dict[str, str]] = {}
            for k, v in data.items():
                if isinstance(v, dict):
                    out[str(k)] = {str(pk): str(pv) for pk, pv in v.items() if isinstance(pk, str)}
            return out
        return {}
    except Exception:
        return {}


def _write_all(data: Dict[str, Dict[str, str]]) -> None:
    try:
        p = _effective_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # Best-effort persistence only
        pass


def get_params(slug: str) -> Dict[str, str]:
    """Return stored parameter key->value (strings) for the given model slug."""
    return dict(_read_all().get(slug, {}))


def set_params(slug: str, params: Dict[str, str]) -> None:
    """Persist parameter key->value (strings) for the given model slug.
    Blank values are filtered out before writing.
    """
    cleaned = {k: v for k, v in (params or {}).items() if str(k).strip() and str(v).strip()}
    data = _read_all()
    if cleaned:
        data[str(slug)] = {str(k): str(v) for k, v in cleaned.items()}
    else:
        # If nothing to store, remove entry to keep file tidy
        data.pop(str(slug), None)
    _write_all(data)


def get_sanitized_params_for_api(slug: str, supported: list[str] | None) -> Dict[str, Any]:
    """Return params filtered to supported keys and converted from strings.

    Conversion strategy:
    - Try JSON parse for numbers/bools/arrays/objects
    - Fallback to original string
    """
    raw = get_params(slug)
    out: Dict[str, Any] = {}
    # If supported is falsy/unknown, do NOT filter; pass through all keys.
    supported_set = None if not supported else {s.lower() for s in supported}
    for k, v in raw.items():
        if supported_set is not None and k.lower() not in supported_set:
            continue
        try:
            parsed = json.loads(v)
            out[k] = parsed
        except Exception:
            # Accept string as-is
            out[k] = v
    return out
