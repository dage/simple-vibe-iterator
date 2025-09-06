# src/model_selector.py
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Set
import asyncio
import datetime

from nicegui import ui
from . import op_status
from .params_dialog import open_params_dialog

try:
    from . import or_client as orc
except Exception:  # pragma: no cover - fallback for tests that import without package context
    import or_client as orc  # type: ignore


class ModelSelector:
    """
    Interactive, dropdown-style multi-select for OpenRouter models.

    - Read-only input toggles a dropdown below it
    - Multi-select by default; comma-separated slugs (model.id) displayed in input
    - Shows error message when no models are selected
    - Scrollable grid (~10 rows) with:
      * first column checkbox (or radio button if single_selection=True)
      * columns: name (with secondary id line), has_text_input, has_image_input, prompt_price, completion_price
    - Live filtering via or_client.list_models(query=...) on each keystroke
    - Keyboard navigation: ArrowUp/ArrowDown to change focus, Space to toggle,
      Enter to apply & close, Escape to cancel & close
    - Exposes get_value()/set_value(); on_change called when selection is applied
    - vision_only=True filters to models with image input capability
    - single_selection=True uses radio buttons and replaces selection instead of adding
    """

    def __init__(
        self,
        *,
        initial_value: str,
        vision_only: bool,
        label: str = 'model',
        on_change: Optional[Callable[[str], None]] = None,
        single_selection: bool = False,
    ) -> None:
        self.label = label
        self.vision_only = vision_only
        self.on_change = on_change
        self.single_selection = single_selection

        self._selected_ids: Set[str] = self._parse_value(initial_value)
        self._applied_value: str = self._format_value(sorted(self._selected_ids))
        self._models: List[orc.ModelInfo] = []
        self._focused_index: int = -1
        self._row_entries: List[Dict[str, object]] = []

        with ui.column().classes('w-full gap-1') as root:
            self.root = root
            # Hidden value holder input for binding compatibility
            self.input = ui.input(
                label=self.label,
                value=self._applied_value,
            ).classes('hidden')

            # Nested expander that always exists in DOM; header mirrors selected slugs (with empty fallback)
            _initial_header = self._applied_value if (self._applied_value or '').strip() else '(no models selected)'
            with ui.expansion(_initial_header).classes('w-full') as exp:
                self._expander = exp
                # Keep header text in sync with the selected value
                self._expander.bind_text_from(
                    self.input,
                    'value',
                    lambda v: (v if (v or '').strip() else '(no models selected)')
                )
                self._filter = ui.input(
                    label='Filter models',
                    placeholder='type to filter (by name or id)...',
                    value='',
                ).classes('w-full').props('dense clearable')
                self._filter.on('input', self._on_filter_input)
                self._filter.on('keyup', self._on_filter_keyup)
                self._filter.on('update:model-value', self._on_filter_input)
                self._filter.on('keydown', self._on_filter_key)

                # Selected chips summary
                with ui.column().classes('w-full gap-1'):
                    ui.label('Selected').classes('text-xs text-gray-500 dark:text-gray-300')
                    self._chips_row = ui.row().classes('w-full items-center gap-2 flex-wrap')
                    self._error_message = ui.label('No models selected').classes('text-xs text-red-500 dark:text-red-400')

                self._header = ui.row().classes(
                    'w-full text-xs text-gray-500 dark:text-gray-300 px-2 select-none'
                ).style('display: grid; grid-template-columns: auto auto 2fr auto 1fr auto; gap: 0.5rem;')
                with self._header:
                    ui.label('')
                    ui.label('')
                    ui.label('')
                    ui.label('T/V').classes('text-center')
                    ui.label('Pricing ($/M)').classes('text-center')
                    ui.label('Created').classes('text-center')

                with ui.element('div').classes('w-full max-h-[360px] overflow-auto') as scroll:
                    self._scroll = scroll
                    self._rows_container = ui.column().classes('w-full gap-0')

        ui.timer(0.05, lambda: asyncio.create_task(self._load_and_render('')), once=True)
        # Initial chips render
        self._render_chips()

    # --- Public API ---

    def get_value(self) -> str:
        return self._format_value(sorted(self._selected_ids))

    def set_value(self, value: str) -> None:
        self._selected_ids = self._parse_value(value)
        self._applied_value = self._format_value(sorted(self._selected_ids))
        self._set_input_value(self._applied_value)
        try:
            self._render_rows()
        except Exception:
            pass

    # --- Events ---

    async def _on_filter_input(self, e) -> None:
        query = (self._filter.value or '').strip()
        await self._load_and_render(query)

    async def _on_filter_keyup(self, e) -> None:
        # Mirror input handler to ensure realtime filtering regardless of browser/event quirk
        await self._on_filter_input(e)

    async def _on_filter_key(self, e) -> None:
        key = ''
        try:
            if isinstance(e.args, dict):
                key = str(e.args.get('key', ''))
            else:
                key = str(e.args or '')
        except Exception:
            key = ''
        if key in ('ArrowDown', 'Down'):
            self._move_focus(1)
        elif key in ('ArrowUp', 'Up'):
            self._move_focus(-1)
        elif key in (' ', 'Spacebar', 'Space'):
            self._toggle_focused_selection()
        elif key in ('Enter', 'NumpadEnter'):
            await self._apply_immediately()
        elif key in ('Escape', 'Esc'):
            # Clear filter and reload full list
            try:
                self._filter.value = ''
            except Exception:
                pass
            await self._load_and_render('')

    # --- Apply / Cancel ---

    async def _apply_immediately(self) -> None:
        new_value = self._format_value(sorted(self._selected_ids))
        self._applied_value = new_value
        self._set_input_value(new_value)
        self._render_chips()
        if self.on_change:
            try:
                self.on_change(new_value)
            except Exception:
                pass

    def _apply_new_selection(self, new_ids: Set[str]) -> None:
        self._selected_ids = set(new_ids)
        asyncio.create_task(self._apply_immediately())

    # --- Load and render ---

    async def _load_and_render(self, query: str) -> None:
        try:
            models = await orc.list_models(query=query, vision_only=self.vision_only, limit=200)
            self._models = list(models)
        except Exception as exc:
            # Use background-safe notification path
            op_status.enqueue_notification(f'Failed to load models: {exc}', color='negative', timeout=0, close_button=True)
            self._models = []
        self._focused_index = 0 if self._models else -1
        self._render_rows()

    def _render_rows(self) -> None:
        self._rows_container.clear()
        self._row_entries.clear()

        def format_price(prompt: float, completion: float) -> str:
            try:
                return f'${prompt:,.2f} / ${completion:,.2f}'
            except Exception:
                return '$0.00 / $0.00'
        
        def format_date(timestamp: int) -> str:
            try:
                if timestamp == 0:
                    return 'N/A'
                dt = datetime.datetime.fromtimestamp(timestamp)
                return dt.strftime('%m/%d/%y')
            except Exception:
                return 'N/A'

        with self._rows_container:
            for idx, m in enumerate(self._models):
                is_checked = m.id in self._selected_ids
                row = ui.element('div').classes(
                    'w-full px-2 py-1 rounded cursor-default hover:bg-gray-100 dark:hover:bg-gray-800'
                ).style('display: grid; grid-template-columns: auto auto 2fr auto 1fr auto; gap: 0.5rem; align-items: center;')
                if idx == self._focused_index:
                    row.classes('bg-indigo-600/10')

                with row:
                    if self.single_selection:
                        cb = ui.radio(value=m.id if is_checked else None, options={m.id: ''}).classes('justify-self-start self-start').props('dense')
                    else:
                        cb = ui.checkbox(value=is_checked).classes('justify-self-start self-start').props('dense')
                    # Params button
                    async def _open_params_handler(mid=m.id, title=m.id):
                        await open_params_dialog(mid, title_name=title)
                    ui.button('P', on_click=_open_params_handler).props('flat dense').classes('justify-self-start self-start').style('padding: 0; min-height: 20px; align-items: flex-start; margin-top: -2px;')
                    with ui.column().classes('truncate gap-0'):
                        ui.label(m.name).classes('text-sm truncate')
                        ui.label(m.id).classes('text-[10px] text-gray-500 dark:text-gray-400 truncate')
                    with ui.row().classes('gap-1 justify-center'):
                        ui.icon('check_circle' if m.has_text_input else 'cancel',
                                color='green' if m.has_text_input else 'grey').classes('text-sm')
                        ui.icon('check_circle' if m.has_image_input else 'cancel',
                                color='green' if m.has_image_input else 'grey').classes('text-sm')
                    ui.label(format_price(m.prompt_price, m.completion_price)).classes('text-sm text-center')
                    ui.label(format_date(m.created)).classes('text-sm text-center')

                # Row click: focus only (no selection change)
                row.on('click', lambda _, i=idx: self._set_focus(i))

                # Checkbox/radio change: selection = checkbox.value or radio.value
                async def _handle_cb_change(_=None, mid=m.id, cb_ref=cb):
                    try:
                        if self.single_selection:
                            # Radio button: value is the selected option or None
                            selected_value = getattr(cb_ref, 'value', None)
                            if selected_value == mid:
                                # Clear all selections and select only this one
                                self._selected_ids.clear()
                                self._selected_ids.add(mid)
                                # Update all other radio buttons to be unselected
                                for entry in self._row_entries:
                                    other_cb = entry.get('checkbox')
                                    other_id = entry.get('id')
                                    if other_cb and other_id != mid:
                                        try:
                                            other_cb.value = None
                                        except Exception:
                                            pass
                            else:
                                # Deselect this one
                                self._selected_ids.discard(mid)
                        else:
                            # Checkbox: value is boolean
                            is_checked = bool(getattr(cb_ref, 'value', False))
                            if is_checked:
                                self._selected_ids.add(mid)
                            else:
                                self._selected_ids.discard(mid)
                    except Exception:
                        pass
                    self._preview_selection_update()
                    # Apply immediately so parent expansion header updates too
                    await self._apply_immediately()

                # Prefer dedicated value-change; fall back to generic events
                wired = False
                try:
                    cb.on_value_change(lambda v, mid=m.id, cb_ref=cb: asyncio.create_task(_handle_cb_change(mid=mid, cb_ref=cb_ref)))
                    wired = True
                except Exception:
                    pass
                if not wired:
                    cb.on('change', lambda e, mid=m.id, cb_ref=cb: asyncio.create_task(_handle_cb_change(mid=mid, cb_ref=cb_ref)))
                    cb.on('update:model-value', lambda v, mid=m.id, cb_ref=cb: asyncio.create_task(_handle_cb_change(mid=mid, cb_ref=cb_ref)))

                self._row_entries.append({'row': row, 'checkbox': cb, 'id': m.id})

    def _set_focus(self, new_index: int) -> None:
        if not self._models:
            self._focused_index = -1
            return
        new_index = max(0, min(len(self._models) - 1, new_index))
        if new_index == self._focused_index:
            return
        if 0 <= self._focused_index < len(self._row_entries):
            row_old = self._row_entries[self._focused_index]['row']
            assert isinstance(row_old, ui.element)
            row_old.classes(remove='bg-indigo-600/10')
        self._focused_index = new_index
        row_new = self._row_entries[self._focused_index]['row']
        assert isinstance(row_new, ui.element)
        row_new.classes('bg-indigo-600/10')
        try:
            row_new.run_method('scrollIntoView', {'block': 'nearest', 'inline': 'nearest'})
        except Exception:
            pass

    def _move_focus(self, delta: int) -> None:
        if not self._models:
            return
        self._set_focus((self._focused_index if self._focused_index >= 0 else 0) + delta)

    def _toggle_focused_selection(self) -> None:
        if not (0 <= self._focused_index < len(self._row_entries)):
            return
        mid = str(self._row_entries[self._focused_index]['id'])
        cb = self._row_entries[self._focused_index]['checkbox']
        assert isinstance(cb, ui.element)
        
        if self.single_selection:
            # Radio button behavior: always select this one (clearing others)
            self._selected_ids.clear()
            self._selected_ids.add(mid)
            cb.value = mid
            # Update all other radio buttons to be unselected
            for entry in self._row_entries:
                other_cb = entry.get('checkbox')
                other_id = entry.get('id')
                if other_cb and other_id != mid:
                    try:
                        other_cb.value = None
                    except Exception:
                        pass
        else:
            # Checkbox behavior: toggle
            if mid in self._selected_ids:
                self._selected_ids.remove(mid)
                cb.value = False
            else:
                self._selected_ids.add(mid)
                cb.value = True
        
        self._preview_selection_update()
        try:
            asyncio.create_task(self._apply_immediately())
        except Exception:
            pass

    @staticmethod
    def _parse_value(value: str) -> Set[str]:
        if not value:
            return set()
        return {s.strip() for s in value.split(',') if s.strip()}

    @staticmethod
    def _format_value(ids: List[str]) -> str:
        return ', '.join(ids)

    # --- Preview helper ---

    def _preview_selection_update(self) -> None:
        # Update the read-only input text to reflect current (not-yet-applied) selection
        try:
            self._set_input_value(self._format_value(sorted(self._selected_ids)))
        except Exception:
            pass

    def _render_chips(self) -> None:
        try:
            self._chips_row.clear()
            selected_sorted = sorted(self._selected_ids)
            has_selection = bool(selected_sorted)
            self._chips_row.visible = has_selection
            self._error_message.visible = not has_selection
            for mid in selected_sorted:
                with self._chips_row:
                    with ui.row().classes('items-center gap-1 px-2 py-1 rounded bg-gray-100 dark:bg-gray-800 text-sm'):
                        ui.label(mid).classes('truncate max-w-[240px]')
                        # close icon to remove selection
                        def _mk_remove(mid_str: str):
                            async def _remove(_=None):
                                try:
                                    if mid_str in self._selected_ids:
                                        self._selected_ids.remove(mid_str)
                                    # if row is visible, also uncheck its checkbox
                                    try:
                                        for entry in self._row_entries:
                                            if entry.get('id') == mid_str:
                                                cb = entry.get('checkbox')
                                                if cb is not None:
                                                    cb.value = False
                                                break
                                    except Exception:
                                        pass
                                    await self._apply_immediately()
                                except Exception:
                                    pass
                            return _remove
                        ui.icon('close').classes('cursor-pointer text-gray-500 hover:text-gray-700').on('click', _mk_remove(mid))
        except Exception:
            pass

    def _set_input_value(self, value: str) -> None:
        try:
            # Preferred: use NiceGUI's reactive setter
            self.input.set_value(value)
        except Exception:
            try:
                # Fallback to direct assignment and force update
                self.input.value = value
                try:
                    self.input.update()
                except Exception:
                    pass
            except Exception:
                pass
