# src/config.py
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
import os
from typing import Dict

import yaml

"""
App configuration loaded from YAML (single source of truth).
Location: project_root/config.yaml (required)
Required keys: models.code, models.vision, templates.code, templates.code_system_prompt, templates.vision
"""


@dataclass(frozen=True)
class AppConfig:
    code_model: str
    vision_model: str
    code_template: str
    code_system_prompt_template: str
    vision_template: str
    input_screenshot_default: int = 1
    input_screenshot_interval: float = 1.0
    model_image_limits: Dict[str, int] = field(default_factory=dict)
    screenshot_scale: float = 1.0


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
    if not isinstance(templates, dict) or not templates.get('code_system_prompt'):
        missing.append('templates.code_system_prompt')
    if not isinstance(templates, dict) or not templates.get('vision'):
        missing.append('templates.vision')
    if missing:
        raise RuntimeError('Missing required config keys: ' + ', '.join(missing))

    code_model = str(models.get('code'))
    vision_model = str(models.get('vision'))
    code_template = str(templates.get('code'))
    code_system_prompt_template = str(templates.get('code_system_prompt'))
    vision_template = str(templates.get('vision'))

    iteration_cfg = data.get('iteration') if isinstance(data, dict) else {}

    screenshot_cfg = iteration_cfg.get('input_screenshots') if isinstance(iteration_cfg, dict) else {}
    if not isinstance(screenshot_cfg, dict):
        screenshot_cfg = {}
    try:
        default_shots = int(screenshot_cfg.get('default_count', 1))
    except Exception:
        default_shots = 1
    if default_shots < 1:
        default_shots = 1
    try:
        interval_seconds = float(screenshot_cfg.get('interval_seconds', 1.0))
    except Exception:
        interval_seconds = 1.0
    if interval_seconds <= 0:
        interval_seconds = 1.0

    limits: Dict[str, int] = {}

    def _register_limit(slug: str, raw_value: object) -> None:
        try:
            value = int(raw_value)  # type: ignore[arg-type]
        except Exception:
            return
        if value <= 0:
            return
        slug_key = str(slug)
        if slug_key in limits:
            limits[slug_key] = min(limits[slug_key], value)
        else:
            limits[slug_key] = value

    raw_limits = screenshot_cfg.get('model_limits', {})
    if isinstance(raw_limits, dict):
        for key, entry in raw_limits.items():
            if isinstance(entry, dict):
                for slug, raw_value in entry.items():
                    _register_limit(slug, raw_value)
            else:
                _register_limit(key, entry)

    raw_scale = data.get('screenshot_scale', 1.0)
    screenshot_scale = float(raw_scale)
    return AppConfig(
        code_model=code_model,
        vision_model=vision_model,
        code_template=code_template,
        code_system_prompt_template=code_system_prompt_template,
        vision_template=vision_template,
        input_screenshot_default=default_shots,
        input_screenshot_interval=interval_seconds,
        model_image_limits=limits,
        screenshot_scale=screenshot_scale,
    )
