# src/settings.py
"""
Unified application settings management.
Handles both app-wide preferences and mode-specific configurations.
"""

from __future__ import annotations
from dataclasses import replace
from typing import Optional
from .interfaces import IterationMode, TransitionSettings
from . import prefs
from . import config as app_config


class Settings:
    """Unified settings manager for the application."""

    def __init__(self):
        """Initialize the settings manager."""
        pass

    # App-wide settings
    @property
    def keep_history(self) -> bool:
        """Whether to maintain cumulative message history across iterations."""
        return prefs.get('keep_history', 'false').lower() == 'true'

    @keep_history.setter
    def keep_history(self, value: bool) -> None:
        """Set the keep history preference."""
        prefs.set('keep_history', str(value).lower())

    @property
    def current_mode(self) -> IterationMode:
        """Get the current iteration mode."""
        cfg = app_config.get_config()
        fallback = cfg.iteration_mode
        stored = prefs.get('iteration.mode', fallback.value)
        try:
            return IterationMode(stored or fallback.value)
        except Exception:
            return fallback

    @current_mode.setter
    def current_mode(self, mode: IterationMode) -> None:
        """Set the current iteration mode."""
        prefs.set('iteration.mode', mode.value)

    # Mode-specific settings
    def _pref_key(self, base: str, mode: IterationMode) -> str:
        """Generate mode-specific preference key."""
        return f"{base}.{mode.value}"

    def _get_mode_value(self, base: str, mode: IterationMode, fallback: str) -> str:
        """Get mode-specific setting value."""
        value = prefs.get(self._pref_key(base, mode), '')
        return value if value.strip() else fallback

    def _set_mode_value(self, base: str, mode: IterationMode, value: str) -> None:
        """Set mode-specific setting value."""
        prefs.set(self._pref_key(base, mode), value)
        prefs.set(base, value)  # Also set as global default

    def get_code_model(self, mode: Optional[IterationMode] = None) -> str:
        """Get the preferred code model for the given mode."""
        if mode is None:
            mode = self.current_mode
        cfg = app_config.get_config()
        return self._get_mode_value('model.code', mode, prefs.get('model.code', cfg.code_model))

    def get_vision_model(self, mode: Optional[IterationMode] = None) -> str:
        """Get the preferred vision model for the given mode."""
        if mode is None:
            mode = self.current_mode
        cfg = app_config.get_config()
        return self._get_mode_value('model.vision', mode, prefs.get('model.vision', cfg.vision_model))

    def get_code_template(self, mode: Optional[IterationMode] = None) -> str:
        """Get the code template for the given mode."""
        if mode is None:
            mode = self.current_mode
        cfg = app_config.get_config()
        return self._get_mode_value('template.code', mode, prefs.get('template.code', cfg.code_template))

    def get_vision_template(self, mode: Optional[IterationMode] = None) -> str:
        """Get the vision template for the given mode."""
        if mode is None:
            mode = self.current_mode
        cfg = app_config.get_config()
        return self._get_mode_value('template.vision', mode, prefs.get('template.vision', cfg.vision_template))

    def set_code_model(self, value: str, mode: Optional[IterationMode] = None) -> None:
        """Set the code model for the given mode."""
        if mode is None:
            mode = self.current_mode
        self._set_mode_value('model.code', mode, value)

    def set_vision_model(self, value: str, mode: Optional[IterationMode] = None) -> None:
        """Set the vision model for the given mode."""
        if mode is None:
            mode = self.current_mode
        self._set_mode_value('model.vision', mode, value)

    def set_code_template(self, value: str, mode: Optional[IterationMode] = None) -> None:
        """Set the code template for the given mode."""
        if mode is None:
            mode = self.current_mode
        self._set_mode_value('template.code', mode, value)

    def set_vision_template(self, value: str, mode: Optional[IterationMode] = None) -> None:
        """Set the vision template for the given mode."""
        if mode is None:
            mode = self.current_mode
        self._set_mode_value('template.vision', mode, value)

    # TransitionSettings compatibility
    def load_settings(self, overall_goal: str = '', user_steering: str = '') -> TransitionSettings:
        """Load complete settings for current mode."""
        return self.load_settings_for_mode(self.current_mode, overall_goal=overall_goal, user_steering=user_steering)

    def load_settings_for_mode(
        self,
        mode: IterationMode,
        *,
        overall_goal: str = '',
        user_steering: str = '',
    ) -> TransitionSettings:
        """Load complete settings for the given mode."""
        return TransitionSettings(
            code_model=self.get_code_model(mode),
            vision_model=self.get_vision_model(mode),
            overall_goal=overall_goal,
            user_steering=user_steering,
            code_template=self.get_code_template(mode),
            vision_template=self.get_vision_template(mode),
            mode=mode,
            keep_history=self.keep_history,
        )

    def save_settings(self, settings: TransitionSettings) -> None:
        """Save complete settings."""
        self.current_mode = settings.mode
        self.set_code_model(settings.code_model, settings.mode)
        self.set_vision_model(settings.vision_model, settings.mode)
        self.set_code_template(settings.code_template, settings.mode)
        self.set_vision_template(settings.vision_template, settings.mode)
        self.keep_history = settings.keep_history

    def with_mode(self, settings: TransitionSettings, mode: IterationMode) -> TransitionSettings:
        """Create settings with a different mode."""
        replacement = self.load_settings_for_mode(
            mode,
            overall_goal=settings.overall_goal,
            user_steering=settings.user_steering,
        )
        return replace(replacement, mode=mode)


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


# Legacy compatibility functions (delegate to unified settings)
def get_mode(default: IterationMode | None = None) -> IterationMode:
    """Legacy compatibility: get current mode."""
    settings = get_settings()
    return settings.current_mode


def set_mode(mode: IterationMode) -> None:
    """Legacy compatibility: set current mode."""
    settings = get_settings()
    settings.current_mode = mode


def load_settings(overall_goal: str = '', user_steering: str = '') -> TransitionSettings:
    """Legacy compatibility: load settings."""
    settings = get_settings()
    return settings.load_settings(overall_goal, user_steering)


def load_settings_for_mode(
    mode: IterationMode,
    *,
    overall_goal: str = '',
    user_steering: str = '',
) -> TransitionSettings:
    """Legacy compatibility: load settings for mode."""
    settings = get_settings()
    return settings.load_settings_for_mode(mode, overall_goal=overall_goal, user_steering=user_steering)


def save_settings(settings: TransitionSettings) -> None:
    """Legacy compatibility: save settings."""
    unified_settings = get_settings()
    unified_settings.save_settings(settings)


def with_mode(settings: TransitionSettings, mode: IterationMode) -> TransitionSettings:
    """Legacy compatibility: create settings with different mode."""
    unified_settings = get_settings()
    return unified_settings.with_mode(settings, mode)