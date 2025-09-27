from __future__ import annotations

from typing import Dict, Optional

from .config import get_config


def get_default_input_screenshot_count() -> int:
    cfg = get_config()
    try:
        value = int(getattr(cfg, "input_screenshot_default", 1) or 1)
    except Exception:
        value = 1
    return max(1, value)


def get_input_screenshot_interval() -> float:
    cfg = get_config()
    try:
        value = float(getattr(cfg, "input_screenshot_interval", 1.0) or 1.0)
    except Exception:
        value = 1.0
    if value <= 0:
        return 1.0
    return value


def get_image_limit(model_slug: str) -> Optional[int]:
    limits: Dict[str, int] = getattr(get_config(), "model_image_limits", {}) or {}
    if not isinstance(limits, dict):
        return None
    try:
        value = limits.get(model_slug)
    except Exception:
        return None
    if value is None:
        return None
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None
