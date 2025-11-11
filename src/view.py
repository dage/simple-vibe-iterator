# src/view.py
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List
from pathlib import Path
from types import SimpleNamespace
import time

from nicegui import ui
from diff_match_patch import diff_match_patch
import html as _html

from .controller import IterationController
from .interfaces import IterationEventListener, IterationNode, IterationMode, TransitionSettings
from . import op_status
from . import prefs
from . import task_registry
from .model_selector import ModelSelector
from .settings import get_settings
from .ui_theme import apply_theme
from .view_utils import format_html_size
from .node_summary_dialog import create_node_summary_dialog
from .status_panel import StatusPanel


class NiceGUIView(IterationEventListener):
    def __init__(self, controller: IterationController):
        self.controller = controller
        self.controller.add_listener(self)
        self.node_panels: Dict[str, ui.element] = {}
        self.chat_container: ui.element | None = None
        self.start_panel: ui.element | None = None
        self.scroll_area: ui.scroll_area | None = None
        self.initial_goal_input: ui.textarea | None = None
        # --- Operation status & lock ---
        self._op_busy: bool = False
        self._status_timer: ui.timer | None = None
        self._notif_timer: ui.timer | None = None
        self._status_panel: StatusPanel | None = None
        self._persistent_selectors: List[ModelSelector] = []
        self._ephemeral_selectors: List[ModelSelector] = []
        self._shutdown_called: bool = False
        self._status_refresh_interval: float = 1.0
        self._last_status_refresh: float = 0.0

        # Set some default styling
        apply_theme()

    def render(self) -> None:
        self._stop_timers()
        # Scoped CSS: Make the default CLOSE button text black on error notifications
        with ui.column().classes('w-full h-screen p-4 gap-3'):
            ui.label('Simple Vibe Iterator').classes('text-2xl font-bold')

            # Container for worker status boxes
            self._status_panel = StatusPanel(on_cancel=self._cancel_worker)
            self._status_panel.build()
            self._status_timer = ui.timer(0.25, lambda: self._refresh_phase())
            # Drain background notifications in UI context
            self._notif_timer = ui.timer(0.25, self._flush_notifications)
            self._refresh_phase(force=True)

            with ui.scroll_area().classes('flex-grow w-full') as scroll:
                self.scroll_area = scroll
                with ui.column().classes('w-full gap-4'):
                    # Start area rendered as collapsible panel like iterations
                    self.start_panel = self._create_start_panel()

                    # Iteration chain container
                    self.chat_container = ui.column().classes('w-full gap-4')

    def _create_start_panel(self) -> ui.element:
        init_settings = self._default_settings(overall_goal='')
        with ui.expansion('Start', value=True).classes(
            'w-full shadow-sm rounded-lg border border-gray-200/70 dark:border-gray-700/50'
        ) as panel:
            with ui.column().classes('w-full gap-4 p-4'):
                inputs = self._render_settings_editor(init_settings, allow_mode_switch=True)

                async def _start() -> None:
                    og = (inputs['overall_goal'].value or '').strip()
                    if not og:
                        ui.notify('Please enter an overall goal', color='negative', timeout=0, close_button=True)
                        return
                    if not self._begin_operation('Start'):
                        return
                    try:
                        # Update app-wide keep_history setting from UI if changed
                        from .settings import get_settings
                        settings_manager = get_settings()

                        if 'keep_history' in inputs and inputs['keep_history'] is not None:
                            try:
                                new_keep_history = bool(inputs['keep_history'].value)
                                settings_manager.keep_history = new_keep_history
                            except Exception:
                                pass

                        try:
                            raw_shots = getattr(inputs['input_screenshot_count'], 'value', init_settings.input_screenshot_count)
                            shot_value = int(raw_shots)
                        except Exception:
                            shot_value = init_settings.input_screenshot_count
                        if shot_value < 1:
                            shot_value = 1

                        settings = TransitionSettings(
                            code_model=inputs['code_model'].value or '',
                            vision_model=inputs['vision_model'].value or '',
                            overall_goal=og,
                            user_steering=inputs['user_steering'].value or '',
                            code_template=inputs['code_template'].value or '',
                            vision_template=inputs['vision_template'].value or '',
                            input_screenshot_count=shot_value,
                            mode=self._extract_mode(inputs['mode']),
                            keep_history=get_settings().keep_history
                        )
                        get_settings().save_settings(settings)
                        if self.controller.has_nodes():
                            root = self.controller.get_root()
                            if root and root.settings.mode != settings.mode:
                                self.controller.reset()
                                if self.chat_container is not None:
                                    self.chat_container.clear()
                                self.node_panels.clear()
                                if self.start_panel is not None:
                                    try:
                                        self.start_panel.value = True
                                    except Exception:
                                        pass
                        await self.controller.apply_transition(None, settings)
                    except asyncio.CancelledError:
                        ui.notify('Operation cancelled', color='warning', timeout=2000)
                    except Exception as exc:
                        ui.notify(f'Start failed: {exc}', color='negative', timeout=0, close_button=True)
                    finally:
                        self._end_operation()

                ui.button('Start', on_click=_start).classes('w-full')

        return panel

    def _default_settings(self, overall_goal: str) -> TransitionSettings:
        return get_settings().load_settings(overall_goal=overall_goal)

    # IterationEventListener
    async def on_node_created(self, node: IterationNode) -> None:
        await self._rebuild_chain(node.id)
        await asyncio.sleep(0.05)
        if self.scroll_area:
            self.scroll_area.scroll_to(percent=1.0)

    async def _rebuild_chain(self, leaf_id: str) -> None:
        self._dispose_ephemeral_selectors()
        if self.chat_container is None:
            return
        # Build linear chain from root -> leaf by following parents
        chain: List[IterationNode] = []
        cur = self.controller.get_node(leaf_id)
        while cur is not None:
            chain.append(cur)
            cur = self.controller.get_node(cur.parent_id) if cur.parent_id else None
        chain.reverse()

        self.chat_container.clear()
        self.node_panels.clear()
        total = len(chain)
        with self.chat_container:
            for idx, node in enumerate(chain, start=1):
                panel = self._create_node_panel(idx, node, expanded=(idx == total))
                self.node_panels[node.id] = panel

        if self.start_panel is not None:
            try:
                self.start_panel.value = (total == 0)
            except Exception:
                pass

    def _render_settings_editor(self, initial: TransitionSettings, *, allow_mode_switch: bool = False) -> Dict[str, ui.element]:
        # Left-side settings editor used in both Start area and iteration cards

        # Settings section (only visible for start node with allow_mode_switch=True)
        keep_history_checkbox = None
        mode_select = None
        if allow_mode_switch:
            ui.label('Settings').classes('text-sm font-semibold text-gray-600 dark:text-gray-400 mt-2 mb-2')

            # Keep History toggle
            keep_history_checkbox = ui.checkbox(
                'Keep history (cumulative message thread)',
                value=initial.keep_history
            ).classes('w-full')

            # Iteration Mode select
            mode_value = initial.mode.value if isinstance(initial.mode, IterationMode) else str(initial.mode)
            if not mode_value:
                mode_value = IterationMode.VISION_SUMMARY.value

            mode_options = {
                'Vision analysis (separate model)': IterationMode.VISION_SUMMARY.value,
                'Direct screenshot to coder': IterationMode.DIRECT_TO_CODER.value,
            }
            label_by_value = {v: k for k, v in mode_options.items()}
            value_by_label = {k: v for k, v in mode_options.items()}
            initial_label = label_by_value.get(mode_value, 'Vision analysis (separate model)')

            mode_select = ui.select(
                options=list(mode_options.keys()),
                value=initial_label,
                label='iteration mode',
            ).props('dense outlined').classes('w-full')
            mode_select._mode_value_map = value_by_label  # type: ignore[attr-defined]
        else:
            # For iteration cards, just create a stub mode_select
            mode_value = initial.mode.value if isinstance(initial.mode, IterationMode) else str(initial.mode)
            mode_options = {
                'Vision analysis (separate model)': IterationMode.VISION_SUMMARY.value,
                'Direct screenshot to coder': IterationMode.DIRECT_TO_CODER.value,
            }
            label_by_value = {v: k for k, v in mode_options.items()}
            value_by_label = {k: v for k, v in mode_options.items()}
            initial_label = label_by_value.get(mode_value, 'Vision analysis (separate model)')
            mode_select = SimpleNamespace(value=initial_label, _mode_value_map=value_by_label)

        try:
            initial_shots = int(getattr(initial, 'input_screenshot_count', 1) or 1)
        except Exception:
            initial_shots = 1
        if initial_shots < 1:
            initial_shots = 1
        screenshot_count = ui.number(
            label='Input screenshots',
            value=initial_shots,
            min=1,
            max=16,
            step=1,
        ).props('dense outlined').classes('w-full')

        overall_goal = ui.textarea(label='Overall goal', value=initial.overall_goal).classes('w-full')
        user_steering = ui.textarea(label='Optional user steering', value=initial.user_steering).classes('w-full')

        with ui.expansion('Coding').classes('w-full') as code_exp:
            code_selector = self._register_selector(ModelSelector(
                initial_value=initial.code_model,
                vision_only=False,
                label='model',
                on_change=lambda v: None,
            ), persistent=allow_mode_switch)
            code_model = code_selector.input
            code_tmpl = ui.textarea(label='coding template', value=initial.code_template).classes('w-full')

        with ui.expansion('Vision').classes('w-full') as vision_exp:
            vision_selector = self._register_selector(ModelSelector(
                initial_value=initial.vision_model,
                vision_only=True,
                label='model',
                on_change=lambda v: None,
                single_selection=True,
            ), persistent=allow_mode_switch)
            vision_model = vision_selector.input
            vision_tmpl = ui.textarea(label='vision template', value=initial.vision_template).classes('w-full')

        def _apply_mode_state(label: str, *, reset_on_mode_change: bool = False) -> None:
            mapped_value = value_by_label.get(label, IterationMode.VISION_SUMMARY.value)
            require_image = mapped_value == IterationMode.DIRECT_TO_CODER.value
            try:
                code_selector.set_require_image_input(require_image)
            except Exception:
                pass
            # Keep vision settings visible for all modes (direct mode now also uses vision)
            try:
                vision_exp.visible = True
            except Exception:
                pass
            if require_image and reset_on_mode_change:
                if allow_mode_switch:
                    try:
                        code_selector.set_value('')
                    except Exception:
                        pass

        _apply_mode_state(initial_label)

        def _persist_current() -> None:
            try:
                mode = self._extract_mode(mode_select)
            except Exception:
                mode = initial.mode if isinstance(initial.mode, IterationMode) else IterationMode.VISION_SUMMARY

            keep_history_value = initial.keep_history
            if keep_history_checkbox is not None:
                try:
                    keep_history_value = bool(keep_history_checkbox.value)
                except Exception:
                    keep_history_value = initial.keep_history

            try:
                shot_value = int(getattr(screenshot_count, 'value', initial_shots) or initial_shots)
            except Exception:
                shot_value = initial_shots
            if shot_value < 1:
                shot_value = 1

            current = TransitionSettings(
                code_model=code_selector.get_value(),
                vision_model=vision_selector.get_value(),
                overall_goal=overall_goal.value or '',
                user_steering=user_steering.value or '',
                code_template=code_tmpl.value or '',
                vision_template=vision_tmpl.value or '',
                input_screenshot_count=shot_value,
                mode=mode,
                keep_history=keep_history_value,
            )
            get_settings().save_settings(current)

        if allow_mode_switch:
            def _handle_mode_change(_=None) -> None:
                try:
                    current = str(getattr(mode_select, 'value', initial_label) or '')
                except Exception:
                    current = initial_label
                _apply_mode_state(current, reset_on_mode_change=True)

                try:
                    new_mode = self._extract_mode(mode_select)
                except Exception:
                    new_mode = initial.mode if isinstance(initial.mode, IterationMode) else IterationMode.VISION_SUMMARY

                get_settings().current_mode = new_mode
                stored = get_settings().load_settings_for_mode(new_mode)

                code_selector.set_value(stored.code_model)
                try:
                    code_model.set_value(stored.code_model)
                except Exception:
                    code_model.value = stored.code_model

                vision_selector.set_value(stored.vision_model)
                try:
                    vision_model.set_value(stored.vision_model)
                except Exception:
                    vision_model.value = stored.vision_model

                try:
                    code_tmpl.set_value(stored.code_template)
                except Exception:
                    code_tmpl.value = stored.code_template

                try:
                    vision_tmpl.set_value(stored.vision_template)
                except Exception:
                    vision_tmpl.value = stored.vision_template
                try:
                    screenshot_count.set_value(stored.input_screenshot_count)
                except Exception:
                    screenshot_count.value = stored.input_screenshot_count
                # Update keep_history checkbox to match loaded settings
                if keep_history_checkbox is not None:
                    try:
                        keep_history_checkbox.set_value(stored.keep_history)
                    except Exception:
                        keep_history_checkbox.value = stored.keep_history

                _persist_current()

            mode_select.on_value_change(lambda _: _handle_mode_change())
            mode_select.on('update:model-value', lambda _: _handle_mode_change())

            # Persist changes as users tweak values in the start editor
            def _wrap_change(orig):
                def handler(value: str) -> None:
                    _persist_current()
                    if callable(orig):
                        try:
                            orig(value)
                        except Exception:
                            pass
                return handler

            code_selector.on_change = _wrap_change(code_selector.on_change)
            vision_selector.on_change = _wrap_change(vision_selector.on_change)

            code_tmpl.on('blur', lambda _: _persist_current())
            vision_tmpl.on('blur', lambda _: _persist_current())
            if keep_history_checkbox is not None:
                keep_history_checkbox.on_value_change(lambda _: _persist_current())
            screenshot_count.on_value_change(lambda _: _persist_current())
            screenshot_count.on('update:model-value', lambda _: _persist_current())
        else:
            prefs.set('iteration.mode', self._extract_mode(mode_select).value)

        result = {
            'user_steering': user_steering,
            'overall_goal': overall_goal,
            'code_model': code_model,
            'vision_model': vision_model,
            'code_template': code_tmpl,
            'vision_template': vision_tmpl,
            'input_screenshot_count': screenshot_count,
            'mode': mode_select,
        }
        if keep_history_checkbox is not None:
            result['keep_history'] = keep_history_checkbox
        return result

    def _create_node_panel(self, index: int, node: IterationNode, *, expanded: bool) -> ui.element:
        with ui.expansion(f'Iteration {index}', value=expanded).classes(
            'w-full shadow-sm rounded-lg border border-gray-200/70 dark:border-gray-700/50'
        ) as panel:
            self._build_node_card(index, node, show_heading=False)

        try:
            panel.value = expanded
        except Exception:
            pass

        return panel

    def _build_node_card(self, index: int, node: IterationNode, *, show_heading: bool = True) -> ui.card:
        with ui.card().classes('w-full p-4') as card:
            if show_heading:
                with ui.row().classes('items-center justify-between w-full'):
                    ui.label(f'Iteration {index}').classes('text-lg font-semibold')

            with ui.row().classes('w-full items-start gap-6 flex-nowrap'):
                with ui.column().classes('basis-5/12 min-w-0 gap-3'):
                    inputs = self._render_settings_editor(node.settings)

                with ui.column().classes('basis-7/12 min-w-0 gap-4'):
                    first_output = next(iter(node.outputs.values())) if node.outputs else None

                    # Messages dialog (showing full JSON as sent to LLM)
                    messages_dialog = None
                    open_messages_handler = None
                    if first_output and hasattr(first_output, 'messages') and first_output.messages:
                        messages_dialog = ui.dialog()
                        messages_dialog.props('persistent')
                        msgs_snapshot = list(first_output.messages)

                        def _open_messages_dialog(msgs=msgs_snapshot) -> None:
                            self._render_messages_dialog(messages_dialog, msgs)
                            messages_dialog.open()

                        messages_dialog.on('hide', lambda _: messages_dialog.clear())
                        open_messages_handler = _open_messages_dialog
                    summary_dialog, summary_button_label, summary_disabled = create_node_summary_dialog(node)

                    with ui.row().classes('w-full items-start gap-2 mb-2'):
                        if open_messages_handler:
                            ui.button('📋 Messages', on_click=open_messages_handler).props('flat dense').classes('text-sm p-0 min-h-0 self-start')
                        summary_handler = summary_dialog.open if not summary_disabled else (lambda: None)
                        summary_btn = ui.button(summary_button_label, on_click=summary_handler).props('flat dense').classes('text-sm p-0 min-h-0 self-start')
                        if summary_disabled:
                            summary_btn.props('disable')

                    with ui.row().classes('w-full items-start gap-6 flex-nowrap'):
                        with ui.column().classes('basis-1/2 min-w-0 gap-2'):
                            ui.label('INPUT SCREENSHOTS').classes('text-sm font-semibold')
                            input_entries: List[tuple[int, str, str, str]] = []
                            primary_html_url = ''
                            limit_note = ''
                            try:
                                from pathlib import Path as _P
                                raw_paths = list(getattr(first_output.artifacts, 'input_screenshot_filenames', []) if first_output else [])
                                for idx, raw_path in enumerate(raw_paths):
                                    if not (raw_path or '').strip():
                                        continue
                                    p = _P(raw_path)
                                    artifact_url = f"/artifacts/{p.name}" if p.exists() else ''
                                    html_candidate = p.with_suffix('.html')
                                    html_url = f"/artifacts/{html_candidate.name}" if html_candidate.exists() else ''
                                    input_entries.append((idx, raw_path, artifact_url, html_url))
                                    if not primary_html_url and html_url:
                                        primary_html_url = html_url
                                limit_note = (getattr(first_output.artifacts, 'analysis', {}) or {}).get('input_screenshot_limit', '')
                            except Exception:
                                input_entries = []
                                limit_note = ''
                                primary_html_url = ''

                            if input_entries:
                                if limit_note:
                                    ui.label(limit_note).classes('text-xs text-amber-300')
                                with ui.row().classes('w-full gap-2 flex-wrap'):
                                    for idx, raw_path, artifact_url, html_url in input_entries:
                                        with ui.column().classes('gap-1 w-[120px]'):
                                            target_link = artifact_url or raw_path
                                            with ui.link('', target_link, new_tab=True).classes('block no-underline'):
                                                ui.image(raw_path).classes('w-[120px] h-[80px] object-cover border border-gray-600 rounded hover:border-blue-400 transition-colors duration-150')
                                            with ui.row().classes('items-center justify-between w-full'):
                                                ui.label(f'#{idx + 1}').classes('text-xs text-gray-400')
                                size = format_html_size(node.html_input)
                                with ui.row().classes('items-center gap-2 mt-1'):
                                    ui.icon('content_copy').classes('text-sm cursor-pointer').on('click', lambda html=node.html_input: self._copy_to_clipboard(html))
                                    ui.label('HTML source').classes('text-sm')
                                    if primary_html_url:
                                        ui.link('Open', primary_html_url, new_tab=True).classes('text-sm')
                                    ui.label(f'({size})').classes('text-sm text-gray-600 dark:text-gray-400')
                            else:
                                ui.label('(no input screenshots)').classes('text-sm text-gray-500')
                            in_logs = list(getattr(first_output.artifacts, 'input_console_logs', []) if first_output else [])
                            in_title = f"Console logs ({'empty' if len(in_logs) == 0 else len(in_logs)})"
                            with ui.expansion(in_title):
                                if in_logs:
                                    in_logs_text = '\n\n'.join(in_logs)
                                    ui.markdown(in_logs_text)
                                else:
                                    ui.label('(no console logs)')
                            # Show vision analysis section for any mode; direct mode now includes vision
                            _va_raw = first_output.artifacts.vision_output if first_output else ''
                            _va_lines = [l for l in _va_raw.splitlines() if l.strip()]
                            va_title = f"Vision Analysis ({'empty' if len(_va_lines) == 0 else len(_va_lines)})"
                            with ui.expansion(va_title):
                                va_text = first_output.artifacts.vision_output if first_output else ''
                                has_inputs = bool(getattr(first_output.artifacts, 'input_screenshot_filenames', []) if first_output else [])
                                if not has_inputs:
                                    va_text = '(no input screenshot)'
                                elif not (va_text or '').strip():
                                    va_text = '(pending)'
                                else:
                                    va_text = va_text.replace('\n', '\n\n')
                                ui.markdown(va_text)

                        with ui.column().classes('basis-1/2 min-w-0 gap-6'):
                            for model_slug, out in node.outputs.items():
                                with ui.column().classes('w-full min-w-0 gap-2 border rounded p-2'):
                                    ui.label(f'{model_slug}').classes('text-sm font-semibold')
                                    # Always render a subtle metadata line under the model slug
                                    try:
                                        cost = getattr(out, 'total_cost', None)
                                        time_s = getattr(out, 'generation_time', None)
                                        cost_str = (f"${cost:.6f}" if isinstance(cost, (int, float)) else "$—")
                                        time_str = (f"{float(time_s):.1f}s" if isinstance(time_s, (int, float)) else "—")
                                        ui.label(f"{cost_str} · {time_str}").classes('text-xs text-gray-500 dark:text-gray-400 leading-tight')
                                    except Exception:
                                        ui.label("$— · —").classes('text-xs text-gray-500 dark:text-gray-400 leading-tight')
                                    out_png = out.artifacts.screenshot_filename
                                    if out_png:
                                        ui.image(out_png).classes('w-full h-auto max-w-full border rounded')
                                    else:
                                        ui.label('(no output screenshot)')
                                    out_html_url = ''
                                    try:
                                        from pathlib import Path as _P
                                        if out_png:
                                            p = _P(out_png)
                                            html_candidate = p.with_suffix('.html')
                                            if html_candidate.exists():
                                                out_html_url = '/artifacts/' + html_candidate.name
                                    except Exception:
                                        pass
                                    diff_html = self._create_visual_diff(node.html_input or '', out.html_output or '')
                                    with ui.dialog() as diff_dialog:
                                        diff_dialog.props('persistent')
                                        with ui.card().classes('w-[90vw] max-w-[1200px]'):
                                            with ui.row().classes('items-center justify-between w-full'):
                                                ui.label('HTML Diff').classes('text-lg font-semibold')
                                                ui.button(icon='close', on_click=diff_dialog.close).props('flat round dense')
                                            ui.html('''<style>
                                            .diff-container { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; background: #0b0f17; color: #e5e7eb; border: 1px solid #334155; border-radius: 6px; padding: 16px; max-height: 70vh; overflow: auto; }
        .diff-content { white-space: pre-wrap; word-break: break-word; }
        .diff-insert { background-color: rgba(34,197,94,0.25); border-radius: 2px; }
        .diff-delete { background-color: rgba(239,68,68,0.25); text-decoration: line-through; border-radius: 2px; }
        .diff-legend { gap: 8px; align-items: center; }
        .legend-chip { display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 12px; }
        .legend-insert { background-color: rgba(34,197,94,0.25); color: #86efac; }
        .legend-delete { background-color: rgba(239,68,68,0.25); color: #fca5a5; }
        </style>''')
                                            with ui.row().classes('diff-legend'):
                                                ui.html('<span class="legend-chip legend-insert">Insert</span>')
                                                ui.html('<span class="legend-chip legend-delete">Delete</span>')
                                            ui.html(f"<div class='diff-container'><pre class='diff-content'>{diff_html or _html.escape('(no differences)')}</pre></div>")
                                    if out_html_url:
                                        size = format_html_size(out.html_output)
                                        with ui.row().classes('items-center gap-2'):
                                            ui.icon('content_copy').classes('text-sm cursor-pointer').on('click', lambda html=out.html_output: self._copy_to_clipboard(html))
                                            ui.label('HTML').classes('text-sm')
                                            ui.label(f'({size})').classes('text-sm text-gray-600 dark:text-gray-400')
                                            ui.label(':').classes('text-sm')
                                            ui.link('Open', out_html_url, new_tab=True).classes('text-sm')
                                            ui.button('Diff', on_click=diff_dialog.open).props('flat dense').classes('text-sm p-0 min-h-0')
                                            # Reasoning indicator (grey brain icon) if reasoning is present
                                            if (out.reasoning_text or '').strip():
                                                with ui.dialog() as reasoning_dialog:
                                                    reasoning_dialog.props('persistent')
                                                    with ui.card().classes('w-[90vw] max-w-[900px]'):
                                                        with ui.row().classes('items-center justify-between w-full'):
                                                            ui.label('Model Reasoning').classes('text-lg font-semibold')
                                                            ui.button(icon='close', on_click=reasoning_dialog.close).props('flat round dense')
                                                        # Render reasoning as markdown but prevent raw HTML from rendering
                                                        # by escaping angle brackets. This preserves markdown formatting
                                                        # (e.g., **bold**, lists, code fences) while neutralizing tags.
                                                        raw_reasoning = (out.reasoning_text or '')
                                                        safe_reasoning = _html.escape(raw_reasoning, quote=False)
                                                        ui.markdown(safe_reasoning)
                                                ui.icon('psychology').classes('text-gray-500 cursor-pointer').on('click', reasoning_dialog.open)
                                    out_logs = list(out.artifacts.console_logs or [])
                                    out_title = f"Console logs ({'empty' if len(out_logs) == 0 else len(out_logs)})"
                                    with ui.expansion(out_title):
                                        if out_logs:
                                            out_logs_text = '\n\n'.join(out_logs)
                                            ui.markdown(out_logs_text)
                                        else:
                                            ui.label('(no console logs)')
                                    async def _iterate_with_slug(slug: str = model_slug) -> None:
                                        if not self._begin_operation('Iterate'):
                                            return
                                        try:
                                            selected_model = inputs['code_model'].value or slug
                                            # Update app-wide keep_history setting from UI if changed
                                            settings_manager = get_settings()

                                            if 'keep_history' in inputs and inputs['keep_history'] is not None:
                                                try:
                                                    new_keep_history = bool(inputs['keep_history'].value)
                                                    settings_manager.keep_history = new_keep_history
                                                except Exception:
                                                    pass

                                            try:
                                                raw_iter_shots = getattr(inputs['input_screenshot_count'], 'value', node.settings.input_screenshot_count)
                                                iter_shots = int(raw_iter_shots)
                                            except Exception:
                                                iter_shots = node.settings.input_screenshot_count
                                            if iter_shots < 1:
                                                iter_shots = 1

                                            updated = TransitionSettings(
                                                code_model=selected_model,
                                                vision_model=inputs['vision_model'].value or '',
                                                overall_goal=inputs['overall_goal'].value or '',
                                                user_steering=inputs['user_steering'].value or '',
                                                code_template=inputs['code_template'].value or '',
                                                vision_template=inputs['vision_template'].value or '',
                                                input_screenshot_count=iter_shots,
                                                mode=self._extract_mode(inputs['mode']),
                                                keep_history=get_settings().keep_history
                                            )
                                            get_settings().save_settings(updated)
                                            await self.controller.apply_transition(node.id, updated, slug)
                                        except asyncio.CancelledError:
                                            ui.notify(f'Cancelled {slug}', color='warning', timeout=2000)
                                        except Exception as exc:
                                            # Route error to UI via notification queue (safe from background tasks)
                                            op_status.enqueue_notification(f'Iterate failed: {exc}', color='negative', timeout=0, close_button=True)
                                        finally:
                                            self._end_operation()

                                    ui.button('Iterate', on_click=(lambda slug=model_slug: lambda: asyncio.create_task(_iterate_with_slug(slug)))(model_slug)).classes('w-full')
        return card

    # --- Operation status helpers ---
    def _begin_operation(self, title: str) -> bool:
        if self._op_busy:
            op_status.enqueue_notification('Another operation is running. Please wait until it finishes.', color='warning')
            return False
        self._op_busy = True
        op_status.clear_all()
        task_registry.clear_all_tasks()
        self._refresh_phase(force=True)
        return True

    def _end_operation(self) -> None:
        self._op_busy = False
        # Ensure UI resets cleanly on success or error
        try:
            op_status.clear_all()
            task_registry.clear_all_tasks()
        except Exception:
            pass
        self._refresh_phase(force=True)

    def _refresh_phase(self, *, force: bool = False) -> None:
        if self._status_panel is None:
            return
        now = time.monotonic()
        if not force and (now - self._last_status_refresh) < self._status_refresh_interval:
            return
        self._last_status_refresh = now

        phases = op_status.get_all_phases()
        self._status_panel.update(phases, busy=self._op_busy)

    def _cancel_worker(self, worker: str) -> None:
        phases = op_status.get_all_phases()
        phase_info = phases.get(worker)
        is_coding = False
        if phase_info is not None:
            raw_phase = phase_info[0] if isinstance(phase_info, (tuple, list)) and phase_info else phase_info
            try:
                text = str(raw_phase or '')
            except Exception:
                text = ''
            if '|' in text:
                head = text.split('|', 1)[0].strip().lower()
                is_coding = head == 'coding'
            else:
                is_coding = text.lower().startswith('coding')
        if not is_coding:
            ui.notify('Cancellation available only during coding phase', color='info', timeout=2000)
            return
        success = False
        try:
            success = task_registry.cancel_task(worker)
        except Exception:
            success = False
        if success:
            op_status.clear_phase(worker)
            ui.notify(f'Cancelled {worker}', color='warning', timeout=2000)
        else:
            ui.notify(f'Worker {worker} already completed or not found', color='info', timeout=2000)


    # --- Utilities ---
    def _extract_mode(self, element: ui.element) -> IterationMode:
        try:
            raw = getattr(element, 'value', '')
            value_map = getattr(element, '_mode_value_map', {}) or {}
            mapped = value_map.get(raw, raw)
        except Exception:
            mapped = ''
        try:
            return IterationMode(str(mapped or IterationMode.VISION_SUMMARY.value))
        except Exception:
            return IterationMode.VISION_SUMMARY

    def _copy_to_clipboard(self, text: str) -> None:
        try:
            js_text = json.dumps(text)
            ui.run_javascript(f'navigator.clipboard.writeText({js_text});')
            ui.notify('HTML copied to clipboard')
        except Exception as exc:
            ui.notify(f'Copy failed: {exc}', color='negative', timeout=0, close_button=True)

    def _flush_notifications(self) -> None:
        """Display any queued notifications from background tasks."""
        try:
            items = op_status.drain_notifications()
        except Exception:
            items = []
        for it in items:
            try:
                text = str(it.get('text', ''))
                color = str(it.get('color', 'negative'))
                timeout = it.get('timeout', 0)
                close_button = bool(it.get('close_button', True))
                ui.notify(text, color=color, timeout=timeout, close_button=close_button)
            except Exception:
                # Best-effort; drop malformed items
                pass

    def _render_messages_dialog(self, dialog: ui.dialog, messages: List[Dict[str, Any]]) -> None:
        """Lazy-render the heavy message history dialog only when the user opens it."""
        dialog.clear()
        with dialog:
            with ui.card().classes('w-[90vw] max-w-[1200px]'):
                with ui.row().classes('items-center justify-between w-full'):
                    ui.label('Message History').classes('text-lg font-semibold')
                    ui.button(icon='close', on_click=dialog.close).props('flat round dense')
                ui.html('''<style>
                .messages-container { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; background: #0b0f17; color: #e5e7eb; border: 1px solid #334155; border-radius: 6px; padding: 16px; max-height: 70vh; overflow: auto; }
                .msg-pre { white-space: pre-wrap; word-break: break-word; background: #0b0f17; color: #e5e7eb; border: 1px solid #334155; border-radius: 6px; padding: 10px; }
                .msg-thumb { width: 260px; height: auto; border: 1px solid #334155; border-radius: 6px; }
                .msg-expansion .q-item__label { border-radius: 9999px; padding: 2px 8px; font-size: 12px; font-weight: 600; display: inline-block; }
                .msg-expansion.chip-system .q-item__label { background: #1f2937; color: #93c5fd; }
                .msg-expansion.chip-user .q-item__label { background: #0f766e; color: #a7f3d0; }
                .msg-expansion.chip-assistant .q-item__label { background: #4c1d95; color: #c4b5fd; }
                .msg-expansion.chip-tool .q-item__label { background: #374151; color: #f59e0b; }
                </style>''')
                msgs = list(messages)
                with ui.column().classes('w-full').style('gap: 10px;'):
                    with ui.row().classes('items-center justify-between w-full'):
                        raw_toggle = ui.checkbox('Raw JSON', value=False).props('dense')
                        ui.button(icon='close', on_click=dialog.close).props('flat round dense')

                    structured_container = ui.column().classes('w-full').style('gap: 10px;')
                    raw_container = ui.column().classes('w-full').style('gap: 10px; display: none;')

                    with raw_container:
                        messages_json = json.dumps(msgs, indent=2, ensure_ascii=False)
                        escaped_json = _html.escape(messages_json)
                        ui.html(f"<div class='messages-container'><pre class='messages-content'>{escaped_json}</pre></div>")

                    with structured_container:
                        for m in msgs:
                            role = str(m.get('role', '')) if isinstance(m, dict) else ''
                            content = m.get('content') if isinstance(m, dict) else m
                            parts: List[Dict[str, Any]] = []
                            try:
                                if isinstance(content, list):
                                    for p in content:
                                        if isinstance(p, dict) and p.get('type') == 'image_url':
                                            url = p.get('image_url', {})
                                            if isinstance(url, dict):
                                                url = url.get('url', '')
                                            parts.append({'type': 'image_url', 'url': str(url)})
                                        elif isinstance(p, dict) and p.get('type') == 'text':
                                            parts.append({'type': 'text', 'text': str(p.get('text', ''))})
                                        else:
                                            parts.append({'type': 'text', 'text': json.dumps(p, ensure_ascii=False)})
                                elif isinstance(content, dict):
                                    parts.append({'type': 'text', 'text': json.dumps(content, ensure_ascii=False)})
                                else:
                                    parts.append({'type': 'text', 'text': str(content)})
                            except Exception:
                                parts.append({'type': 'text', 'text': str(content)})

                            exp = ui.expansion('').classes('msg-expansion ' + (
                                'chip-user' if role == 'user' else ('chip-assistant' if role == 'assistant' else ('chip-system' if role == 'system' else 'chip-tool'))
                            ))
                            try:
                                pretty = json.dumps(m, ensure_ascii=False)
                                kb = len(pretty.encode('utf-8')) / 1024.0
                                size_label = f"{kb:.2f} KB"
                            except Exception:
                                size_label = ''
                            with exp.add_slot('header'):
                                with ui.row().classes('items-center justify-between w-full'):
                                    role_class = 'chip-user' if role == 'user' else ('chip-assistant' if role == 'assistant' else ('chip-system' if role == 'system' else 'chip-tool'))
                                    ui.html(f"<span class='msg-chip {role_class}'>{_html.escape(role or 'unknown')}</span>")
                                    ui.label(size_label).classes('text-xs text-gray-400')
                            try:
                                exp.set_value(False)
                            except Exception:
                                try:
                                    exp.value = False
                                except Exception:
                                    pass
                            with exp:
                                with ui.row().classes('items-center justify-end w-full'):
                                    if any(p.get('type') == 'text' and p.get('text') for p in parts):
                                        _copy_text = '\n\n'.join([p.get('text','') for p in parts if p.get('type')=='text'])
                                        ui.button('Copy', on_click=(lambda t=_copy_text: (lambda: self._copy_to_clipboard(t)))()).props('flat dense')
                                image_parts = [p for p in parts if p.get('type') == 'image_url']
                                text_parts = [p for p in parts if p.get('type') == 'text']
                                ordered = (image_parts + text_parts) if role == 'user' else (text_parts + image_parts)
                                for p in ordered:
                                    if p.get('type') == 'text':
                                        safe = _html.escape(str(p.get('text') or ''), quote=False)
                                        ui.html(f"<pre class='msg-pre'>{safe}</pre>")
                                    elif p.get('type') == 'image_url':
                                        url = str(p.get('url') or '')
                                        with ui.row().classes('items-center gap-2'):
                                            if url:
                                                ui.image(url).classes('msg-thumb')
                                            else:
                                                ui.label('(invalid image url)')

                    def _toggle_raw() -> None:
                        try:
                            is_raw = bool(getattr(raw_toggle, 'value', False))
                        except Exception:
                            is_raw = False
                        try:
                            raw_container.style('display: block;' if is_raw else 'display: none;')
                        except Exception:
                            pass
                        try:
                            structured_container.style('display: none;' if is_raw else 'display: block;')
                        except Exception:
                            pass

                    raw_toggle.on_value_change(lambda _: _toggle_raw())
                    _toggle_raw()

    def _register_selector(self, selector: ModelSelector, *, persistent: bool) -> ModelSelector:
        target = self._persistent_selectors if persistent else self._ephemeral_selectors
        target.append(selector)
        return selector

    def _dispose_selector_list(self, selectors: List[ModelSelector]) -> None:
        while selectors:
            selector = selectors.pop()
            try:
                selector.dispose()
            except Exception:
                pass

    def _dispose_ephemeral_selectors(self) -> None:
        self._dispose_selector_list(self._ephemeral_selectors)

    def _dispose_all_selectors(self) -> None:
        self._dispose_selector_list(self._ephemeral_selectors)
        self._dispose_selector_list(self._persistent_selectors)

    def _stop_timers(self) -> None:
        for attr in ('_status_timer', '_notif_timer'):
            timer = getattr(self, attr, None)
            if timer is None:
                continue
            try:
                timer.cancel()
            except Exception:
                pass
            setattr(self, attr, None)

    async def shutdown(self) -> None:
        if self._shutdown_called:
            return
        self._shutdown_called = True
        self._stop_timers()
        self._dispose_all_selectors()
        try:
            task_registry.clear_all_tasks()
        except Exception:
            pass
        try:
            op_status.clear_all()
            op_status.drain_notifications()
        except Exception:
            pass
        try:
            self.controller.remove_listener(self)
        except Exception:
            pass
        if self._status_panel is not None:
            try:
                self._status_panel.clear()
            except Exception:
                pass
            self._status_panel = None
        self.node_panels.clear()
        for attr in ('chat_container', 'start_panel', 'scroll_area'):
            elem = getattr(self, attr, None)
            if elem is None:
                continue
            try:
                elem.clear()
            except Exception:
                pass
            setattr(self, attr, None)
        self._op_busy = False




    def _create_visual_diff(self, text1: str, text2: str) -> str:
        """Return HTML for a modern-looking inline diff between two texts.
        The HTML tags within inputs are escaped so they render as text.
        """
        try:
            dmp = diff_match_patch()
            diffs = dmp.diff_main(text1 or '', text2 or '')
            dmp.diff_cleanupSemantic(diffs)
        except Exception:
            # Fallback: plain escaped output if diffing fails
            safe1 = _html.escape(text1 or '')
            safe2 = _html.escape(text2 or '')
            if safe1 == safe2:
                return safe2
            return safe1 + ' -> ' + safe2

        html_parts: List[str] = []
        for op, segment in diffs:
            escaped = _html.escape(segment)
            if op == 1:  # Insert
                html_parts.append(f'<span class="diff-insert">{escaped}</span>')
            elif op == -1:  # Delete
                html_parts.append(f'<span class="diff-delete">{escaped}</span>')
            else:  # Equal
                html_parts.append(escaped)
        return ''.join(html_parts)
