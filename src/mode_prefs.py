from __future__ import annotations

from dataclasses import replace

from .interfaces import IterationMode, TransitionSettings
from . import prefs
from . import config as app_config


def _pref_key(base: str, mode: IterationMode) -> str:
    return f"{base}.{mode.value}"


def get_mode(default: IterationMode | None = None) -> IterationMode:
    cfg = app_config.get_config()
    fallback = default or cfg.iteration_mode
    stored = prefs.get('iteration.mode', fallback.value)
    try:
        return IterationMode(stored or fallback.value)
    except Exception:
        return fallback


def set_mode(mode: IterationMode) -> None:
    prefs.set('iteration.mode', mode.value)


def _get_value(base: str, mode: IterationMode, fallback: str) -> str:
    value = prefs.get(_pref_key(base, mode), '')
    return value if value.strip() else fallback


def _set_value(base: str, mode: IterationMode, value: str) -> None:
    prefs.set(_pref_key(base, mode), value)
    prefs.set(base, value)


def load_settings(overall_goal: str = '', user_steering: str = '') -> TransitionSettings:
    cfg = app_config.get_config()
    mode = get_mode(cfg.iteration_mode)
    return load_settings_for_mode(mode, overall_goal=overall_goal, user_steering=user_steering)


def load_settings_for_mode(
    mode: IterationMode,
    *,
    overall_goal: str = '',
    user_steering: str = '',
) -> TransitionSettings:
    cfg = app_config.get_config()
    code_model = _get_value('model.code', mode, prefs.get('model.code', cfg.code_model))
    vision_model = _get_value('model.vision', mode, prefs.get('model.vision', cfg.vision_model))
    code_template = _get_value('template.code', mode, prefs.get('template.code', cfg.code_template))
    vision_template = _get_value('template.vision', mode, prefs.get('template.vision', cfg.vision_template))
    return TransitionSettings(
        code_model=code_model,
        vision_model=vision_model,
        overall_goal=overall_goal,
        user_steering=user_steering,
        code_template=code_template,
        vision_template=vision_template,
        mode=mode,
    )


def save_settings(settings: TransitionSettings) -> None:
    set_mode(settings.mode)
    _set_value('model.code', settings.mode, settings.code_model)
    _set_value('model.vision', settings.mode, settings.vision_model)
    _set_value('template.code', settings.mode, settings.code_template)
    _set_value('template.vision', settings.mode, settings.vision_template)


def with_mode(settings: TransitionSettings, mode: IterationMode) -> TransitionSettings:
    replacement = load_settings_for_mode(
        mode,
        overall_goal=settings.overall_goal,
        user_steering=settings.user_steering,
    )
    return replace(replacement, mode=mode)
