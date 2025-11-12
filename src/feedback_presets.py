from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
import os

import yaml


@dataclass(frozen=True)
class FeedbackAction:
    kind: str
    seconds: float = 0.0
    label: str = ""
    full_page: bool = False
    key: str = ""
    duration_ms: int = 0


@dataclass(frozen=True)
class FeedbackPreset:
    id: str
    label: str
    description: str = ""
    actions: Tuple[FeedbackAction, ...] = field(default_factory=tuple)
    enabled: bool = True
    model_overrides: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FeedbackPresetConfig:
    presets: Tuple[FeedbackPreset, ...] = field(default_factory=tuple)
    default_models: Dict[str, str] = field(default_factory=dict)
    initial_preset_id: str = ""

    def get_preset(self, preset_id: str) -> Optional[FeedbackPreset]:
        lookup = {p.id: p for p in self.presets}
        preset = lookup.get(preset_id)
        if preset is None or not preset.enabled:
            return None
        return preset


_CACHE_KEY: Tuple[str | None, float | None] | None = None
_CACHE_VALUE: FeedbackPresetConfig | None = None


def reset_feedback_presets_cache() -> None:
    global _CACHE_KEY, _CACHE_VALUE
    _CACHE_KEY = None
    _CACHE_VALUE = None


def get_feedback_preset_config() -> FeedbackPresetConfig:
    global _CACHE_KEY, _CACHE_VALUE
    path = _resolve_presets_path()
    stat_key: Tuple[str | None, float | None]
    if path is None:
        stat_key = (None, None)
    else:
        try:
            stat = path.stat()
            stat_key = (str(path), float(stat.st_mtime))
        except FileNotFoundError:
            stat_key = (str(path), None)
    if _CACHE_KEY == stat_key and _CACHE_VALUE is not None:
        return _CACHE_VALUE
    config = _load_presets_from_path(path)
    _CACHE_KEY = stat_key
    _CACHE_VALUE = config
    return config


def list_enabled_presets() -> Tuple[FeedbackPreset, ...]:
    cfg = get_feedback_preset_config()
    return tuple(p for p in cfg.presets if p.enabled and p.actions)


def get_feedback_preset(preset_id: str) -> Optional[FeedbackPreset]:
    if not preset_id:
        return None
    cfg = get_feedback_preset_config()
    return cfg.get_preset(preset_id)


def get_initial_preset_id() -> str:
    cfg = get_feedback_preset_config()
    if cfg.initial_preset_id and cfg.get_preset(cfg.initial_preset_id):
        return cfg.initial_preset_id
    enabled = list_enabled_presets()
    return enabled[0].id if enabled else ""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_presets_path() -> Optional[Path]:
    override = os.getenv("FEEDBACK_PRESETS_PATH")
    if override:
        path = Path(override).expanduser()
        if path.exists():
            return path
    candidate = _project_root() / "feedback_presets.yaml"
    return candidate if candidate.exists() else None


def _load_presets_from_path(path: Optional[Path]) -> FeedbackPresetConfig:
    if path is None:
        return FeedbackPresetConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return FeedbackPresetConfig()
    try:
        raw = yaml.safe_load(text)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse feedback presets: {exc}")
    if not isinstance(raw, dict):
        return FeedbackPresetConfig()
    defaults = raw.get("defaults", {})
    model_defaults: Dict[str, str] = {}
    initial_id = ""
    if isinstance(defaults, dict):
        models = defaults.get("models", {})
        if isinstance(models, dict):
            for key, value in models.items():
                if value:
                    model_defaults[str(key)] = str(value)
        ui_defaults = defaults.get("ui", {})
        if isinstance(ui_defaults, dict):
            initial = ui_defaults.get("initial_preset")
            if isinstance(initial, str):
                initial_id = initial.strip()
    presets_raw = raw.get("presets", [])
    presets: List[FeedbackPreset] = []
    if isinstance(presets_raw, list):
        for entry in presets_raw:
            preset = _parse_preset(entry)
            if preset is not None and preset.actions:
                presets.append(preset)
    return FeedbackPresetConfig(
        presets=tuple(presets),
        default_models=model_defaults,
        initial_preset_id=initial_id,
    )


def _parse_preset(entry: object) -> Optional[FeedbackPreset]:
    if not isinstance(entry, dict):
        return None
    preset_id = str(entry.get("id") or "").strip()
    label = str(entry.get("label") or preset_id).strip()
    if not preset_id or not label:
        return None
    description = str(entry.get("description") or "").strip()
    enabled = bool(entry.get("enabled", True))
    model_overrides: Dict[str, str] = {}
    raw_models = entry.get("models", {})
    if isinstance(raw_models, dict):
        for key, value in raw_models.items():
            if value is None:
                continue
            model_overrides[str(key)] = str(value)
    actions_raw = entry.get("actions", [])
    actions: List[FeedbackAction] = []
    if isinstance(actions_raw, Sequence):
        for raw_action in actions_raw:
            action = _parse_action(raw_action)
            if action is None:
                continue
            actions.append(action)
    return FeedbackPreset(
        id=preset_id,
        label=label,
        description=description,
        actions=tuple(actions),
        enabled=enabled,
        model_overrides=model_overrides,
    )


def _parse_action(raw: object) -> Optional[FeedbackAction]:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("action") or raw.get("type") or raw.get("kind") or "").strip().lower()
    if not kind:
        return None
    if kind == "wait":
        seconds = _coerce_float(raw.get("seconds") or raw.get("duration_seconds") or raw.get("delay"))
        return FeedbackAction(kind="wait", seconds=max(0.0, seconds))
    if kind == "keypress":
        key = str(raw.get("key") or "").strip()
        if not key:
            return None
        duration_ms = _coerce_int(raw.get("duration_ms") or raw.get("duration") or raw.get("hold_ms"), default=150)
        return FeedbackAction(kind="keypress", key=key, duration_ms=max(0, duration_ms))
    if kind == "screenshot":
        label = str(raw.get("label") or raw.get("name") or "").strip() or "shot"
        full = bool(raw.get("full_page", False))
        return FeedbackAction(kind="screenshot", label=label, full_page=full)
    return None


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default
