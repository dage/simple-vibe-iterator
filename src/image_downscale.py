from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Union

from PIL import Image

from .config import get_config


def _get_screenshot_scale() -> float:
    """Return the configured fraction of the original screenshot size."""
    cfg = get_config()
    raw_value = getattr(cfg, "screenshot_scale", 1.0)
    try:
        return float(raw_value) if raw_value is not None else 1.0
    except Exception:
        return 1.0


def load_scaled_image_bytes(path: Union[str, Path]) -> bytes | None:
    """
    Return downsampled image bytes if configured scale is between 0 and 1.
    Original files stay untouched and returned bytes are always PNG encoded.
    """
    scale = _get_screenshot_scale()
    if not (0 < scale < 1):
        return None

    candidate = Path(str(path))
    if not candidate.exists():
        return None

    try:
        with Image.open(candidate) as image:
            original_width, original_height = image.size
            target_width = max(1, int(round(original_width * scale)))
            target_height = max(1, int(round(original_height * scale)))
            if target_width == original_width and target_height == original_height:
                return None
            resized = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            resized.save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:
        return None
