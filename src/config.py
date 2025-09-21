# src/config.py
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os

import yaml

try:  # pragma: no cover - supports both package and sys.path setups
    from .interfaces import IterationMode
except Exception:  # pragma: no cover
    from interfaces import IterationMode  # type: ignore


"""
App configuration loaded from YAML (single source of truth).
Location: project_root/config.yaml (required)
Required keys: models.code, models.vision, templates.code, templates.vision
"""


@dataclass(frozen=True)
class AppConfig:
    code_model: str
    vision_model: str
    code_template: str
    vision_template: str
    iteration_mode: IterationMode


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _find_config_file() -> Path | None:
    # Single source of truth: project root config.yaml
    p = _project_root() / 'config.yaml'
    return p if p.exists() else None


@lru_cache
def get_config() -> AppConfig:
    data: dict = {}
    cfg_path = _find_config_file()
    if cfg_path is None:
        raise RuntimeError('config.yaml not found; create it at project root')
    try:
        text = cfg_path.read_text(encoding='utf-8')
        loaded = yaml.safe_load(text)
        if isinstance(loaded, dict):
            data = loaded
        else:
            raise RuntimeError('config.yaml must contain a YAML mapping (dict)')
    except Exception as exc:
        raise RuntimeError(f'Failed to read YAML config: {exc}')

    models = data.get('models', {}) if isinstance(data, dict) else {}
    templates = data.get('templates', {}) if isinstance(data, dict) else {}

    missing: list[str] = []
    if not isinstance(models, dict) or not models.get('code'):
        missing.append('models.code')
    if not isinstance(models, dict) or not models.get('vision'):
        missing.append('models.vision')
    if not isinstance(templates, dict) or not templates.get('code'):
        missing.append('templates.code')
    if not isinstance(templates, dict) or not templates.get('vision'):
        missing.append('templates.vision')
    if missing:
        raise RuntimeError('Missing required config keys: ' + ', '.join(missing))

    code_model = str(models.get('code'))
    vision_model = str(models.get('vision'))
    code_template = str(templates.get('code'))
    vision_template = str(templates.get('vision'))

    iteration_cfg = data.get('iteration') if isinstance(data, dict) else {}
    mode_value = (iteration_cfg or {}).get('mode', IterationMode.VISION_SUMMARY.value)
    try:
        iteration_mode = IterationMode(str(mode_value))
    except Exception:
        iteration_mode = IterationMode.VISION_SUMMARY

    return AppConfig(
        code_model=code_model,
        vision_model=vision_model,
        code_template=code_template,
        vision_template=vision_template,
        iteration_mode=iteration_mode,
    )

