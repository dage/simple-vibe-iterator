# src/settings.py
"""
Unified application settings management for app-wide preferences.
"""

from __future__ import annotations
from typing import Optional
from .interfaces import TransitionSettings
from . import prefs
from . import config as app_config
from . import feedback_presets


class Settings:
    """Unified settings manager for the application."""

    def __init__(self):
        """Initialize the settings manager."""
        pass

    def get_code_model(self) -> str:
        cfg = app_config.get_config()
        stored = prefs.get('model.code', cfg.code_model)
        value = (stored or '').strip() or cfg.code_model
        return value

    def set_code_model(self, value: str) -> None:
        prefs.set('model.code', value.strip())

    def get_vision_model(self) -> str:
        cfg = app_config.get_config()
        stored = prefs.get('model.vision', cfg.vision_model)
        value = (stored or '').strip() or cfg.vision_model
        return value

    def set_vision_model(self, value: str) -> None:
        prefs.set('model.vision', value.strip())

    def get_code_template(self) -> str:
        cfg = app_config.get_config()
        return cfg.code_template

    def get_code_system_prompt_template(self) -> str:
        cfg = app_config.get_config()
        return cfg.code_system_prompt_template

    def get_code_first_prompt_template(self) -> str:
        cfg = app_config.get_config()
        return cfg.code_first_prompt_template

    def get_vision_template(self) -> str:
        cfg = app_config.get_config()
        return cfg.vision_template

    def get_input_screenshot_count(self) -> int:
        cfg = app_config.get_config()
        fallback = str(cfg.input_screenshot_default)
        raw = prefs.get('input.screenshot.count', fallback)
        try:
            value = int(raw)
        except Exception:
            value = cfg.input_screenshot_default
        if value < 1:
            value = 1
        return value

    def set_input_screenshot_count(self, value: int) -> None:
        try:
            count = int(value)
        except Exception:
            count = 1
        if count < 1:
            count = 1
        prefs.set('input.screenshot.count', str(count))

    def get_feedback_preset_id(self) -> str:
        fallback = feedback_presets.get_initial_preset_id()
        raw = prefs.get('feedback.preset.id', fallback)
        return (raw or fallback or '').strip() or fallback

    def set_feedback_preset_id(self, preset_id: str | None) -> None:
        prefs.set('feedback.preset.id', (preset_id or '').strip())

    def load_settings(self, overall_goal: str = '', user_feedback: str = '') -> TransitionSettings:
        """Load complete settings."""
        return TransitionSettings(
            code_model=self.get_code_model(),
            vision_model=self.get_vision_model(),
            overall_goal=overall_goal,
            user_feedback=user_feedback,
            code_template=self.get_code_template(),
            code_system_prompt_template=self.get_code_system_prompt_template(),
            code_first_prompt_template=self.get_code_first_prompt_template(),
            vision_template=self.get_vision_template(),
            input_screenshot_count=self.get_input_screenshot_count(),
            feedback_preset_id=self.get_feedback_preset_id(),
        )

    def save_settings(self, settings: TransitionSettings) -> None:
        """Persist settings."""
        self.set_code_model(settings.code_model)
        self.set_vision_model(settings.vision_model)
        self.set_input_screenshot_count(settings.input_screenshot_count)
        self.set_feedback_preset_id(settings.feedback_preset_id)


# Global settings instance
_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """Get the global settings instance."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


def reset_settings() -> None:
    """Reset the global settings instance (for testing)."""
    global _settings_instance
    _settings_instance = None


def load_settings(overall_goal: str = '', user_feedback: str = '') -> TransitionSettings:
    """Legacy compatibility: load settings."""
    settings = get_settings()
    return settings.load_settings(overall_goal, user_feedback)


def save_settings(settings: TransitionSettings) -> None:
    """Legacy compatibility: save settings."""
    unified_settings = get_settings()
    unified_settings.save_settings(settings)
