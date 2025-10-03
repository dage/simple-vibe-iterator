from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from nicegui import ui


@dataclass
class _StatusRow:
    row: ui.element
    headline_label: ui.label
    detail_label: ui.label
    headline_text: str = ''
    detail_text: str = ''


class StatusPanel:
    """Small helper that manages the worker status boxes in the top-right corner."""

    _BOX_CLASSES = (
        'items-start gap-2 bg-white/90 border border-gray-300 rounded px-3 py-2 shadow '
        'dark:bg-indigo-600/20 dark:border-indigo-400/30 dark:text-indigo-100 backdrop-blur-sm'
    )

    def __init__(self) -> None:
        self._container: ui.element | None = None
        self._rows: Dict[str, _StatusRow] = {}
        self._idle_row: ui.element | None = None
        self._idle_state: str | None = None

    def build(self) -> ui.element:
        if self._container is not None:
            return self._container
        with ui.column().classes('fixed top-2 right-2 z-50 gap-2 items-end') as container:
            self._container = container
        return container

    def update(self, phases: Dict[str, Tuple[str, float]], *, busy: bool) -> None:
        if self._container is None:
            return

        if not phases:
            self._remove_rows()
            self._render_idle(busy=busy)
            return

        self._clear_idle()
        self._remove_stale_rows(active_workers=set(phases.keys()))

        for worker, (phase_text, elapsed) in phases.items():
            row = self._rows.get(worker)
            if row is None:
                row = self._create_row()
                self._rows[worker] = row

            headline, detail = self._parse_phase(phase_text)
            detail_text = f"{detail} · {elapsed:.1f}s"

            if row.headline_text != headline:
                row.headline_label.set_text(headline)
                row.headline_text = headline

            if row.detail_text != detail_text:
                row.detail_label.set_text(detail_text)
                row.detail_text = detail_text

    def clear(self) -> None:
        self._remove_rows()
        self._clear_idle()

    # --- row helpers -----------------------------------------------------
    def _create_row(self) -> _StatusRow:
        assert self._container is not None
        with self._container:
            with ui.row().classes(self._BOX_CLASSES) as row:
                ui.spinner('dots', color='indigo').classes('w-5 h-5')
                with ui.column().classes('leading-none gap-0'):
                    headline_label = ui.label('Working').classes('font-mono text-sm')
                    detail_label = ui.label('...').classes('font-mono text-xs text-gray-600 dark:text-indigo-200')
        return _StatusRow(row=row, headline_label=headline_label, detail_label=detail_label)

    def _remove_rows(self) -> None:
        if not self._rows:
            return
        for row in self._rows.values():
            try:
                row.row.delete()
            except Exception:
                try:
                    row.row.clear()
                except Exception:
                    pass
        self._rows.clear()

    def _clear_idle(self) -> None:
        if self._idle_row is None:
            return
        try:
            self._idle_row.delete()
        except Exception:
            try:
                self._idle_row.clear()
            except Exception:
                pass
        self._idle_row = None
        self._idle_state = None

    def _render_idle(self, *, busy: bool) -> None:
        if self._container is None:
            return
        state = 'busy' if busy else 'idle'
        if self._idle_state == state and self._idle_row is not None:
            return
        self._clear_idle()
        with self._container:
            with ui.row().classes(self._BOX_CLASSES) as row:
                if busy:
                    ui.spinner('dots', color='indigo').classes('w-5 h-5')
                    ui.label('Starting...').classes('font-mono text-sm')
                else:
                    ui.icon('check_circle', color='green').classes('w-5 h-5')
                    ui.label('No operation running').classes('font-mono text-sm')
        self._idle_row = row
        self._idle_state = state

    def _remove_stale_rows(self, *, active_workers: set[str]) -> None:
        stale = [worker for worker in self._rows.keys() if worker not in active_workers]
        if not stale:
            return
        for worker in stale:
            row = self._rows.pop(worker, None)
            if row is None:
                continue
            try:
                row.row.delete()
            except Exception:
                try:
                    row.row.clear()
                except Exception:
                    pass

    # --- parsing ---------------------------------------------------------
    def _parse_phase(self, phase: str | None) -> tuple[str, str]:
        try:
            raw = str(phase or '')
        except Exception:
            raw = ''
        headline = ''
        detail = raw
        if '|' in raw:
            parts = raw.split('|', 1)
            headline = (parts[0] or '').strip()
            detail = (parts[1] or '').strip()
        else:
            lower = raw.lower()
            if lower.startswith('code:'):
                headline = 'Coding'
                detail = raw.split(':', 1)[1].strip() if ':' in raw else ''
            elif lower.startswith('vision:'):
                headline = 'Vision'
                detail = raw.split(':', 1)[1].strip() if ':' in raw else ''
            elif 'playwright' in lower:
                headline = 'Screenshot'
                detail = raw
            else:
                headline = 'Working'
                detail = raw
        return headline or 'Working', detail
