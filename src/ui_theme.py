from __future__ import annotations

from nicegui import ui

_THEME_STYLES = """
<style>
.q-notification.bg-negative .q-btn--flat,
.q-notification.text-negative .q-btn--flat { color: black !important; }
.q-expansion-item.nicegui-expansion { border: 1px solid #555 !important; border-radius: 6px !important; }
</style>
"""

_applied_style = False


def apply_theme() -> None:
    """Apply the shared dark NiceGUI styling across the app."""
    global _applied_style

    ui.dark_mode().enable()

    if not _applied_style:
        try:
            ui.add_head_html(_THEME_STYLES)  # type: ignore[attr-defined]
        except Exception:
            ui.html(_THEME_STYLES)
        _applied_style = True
