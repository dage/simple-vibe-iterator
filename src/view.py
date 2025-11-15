# src/view.py
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Callable
from types import SimpleNamespace
import time

from nicegui import ui
from diff_match_patch import diff_match_patch
import html as _html

from .controller import IterationController
from .interfaces import IterationEventListener, IterationNode, TransitionSettings
from . import op_status
from . import prefs
from . import task_registry
from . import feedback_presets
from .model_selector import ModelSelector
from .settings import get_settings
from .ui_theme import apply_theme
from .view_utils import extract_vision_summary, format_html_size
from .node_summary_dialog import create_node_summary_dialog
from .status_panel import StatusPanel


class NiceGUIView(IterationEventListener):
    def __init__(self, controller: IterationController):
        self.controller = controller
        self.controller.add_listener(self)
        self.node_panels: Dict[str, ui.element] = {}
        self.chat_container: ui.element | None = None
        self.goal_panel: ui.element | None = None
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
        self._goal_heading_label: ui.element | None = None
        self._current_overall_goal: str = ""

        # Set some default styling
        apply_theme()

    def render(self) -> None:
        self._stop_timers()
        # Scoped CSS: Make the default CLOSE button text black on error notifications
        with ui.column().classes('w-full h-screen p-4 gap-3'):
            ui.label('Simple Vibe Iterator').classes('text-2xl font-bold')
            self._goal_heading_label = ui.label('').classes('text-lg font-semibold text-primary-600')
            self._set_overall_goal_heading(self._current_overall_goal)

            # Container for worker status boxes
            self._status_panel = StatusPanel(on_cancel=self._cancel_worker)
            self._status_panel.build()
            self._status_timer = ui.timer(0.25, lambda: self._refresh_phase())
            # Drain background notifications in UI context
            self._notif_timer = ui.timer(0.25, self._flush_notifications)
            self._refresh_phase(force=True)

            self.goal_panel = self._create_goal_panel()

            with ui.scroll_area().classes('flex-grow w-full') as scroll:
                self.scroll_area = scroll
                with ui.column().classes('w-full gap-4'):
                    # Iteration chain container
                    self.chat_container = ui.column().classes('w-full gap-4')

    def _create_goal_panel(self) -> ui.element:
        with ui.card().classes('w-full bg-gradient-to-br from-slate-900 to-slate-800 text-white shadow-md p-4') as panel:
            with ui.column().classes('w-full gap-3'):
                ui.label('What should we build?').classes('text-xl font-semibold')
                ui.label('Describe the overall vibe or experience you want.').classes('text-sm text-slate-200')
                self.initial_goal_input = ui.textarea(
                    value=self._current_overall_goal,
                ).props('filled type=textarea autogrow input-style="min-height: 200px; max-height: 400px;"') \
                 .classes('w-full text-base placeholder-transparent').style('white-space: pre-wrap;')

                async def _set_goal_text(value: str) -> None:
                    target = self.initial_goal_input
                    if target is None:
                        return
                    setter = getattr(target, 'set_value', None)
                    if asyncio.iscoroutinefunction(setter):
                        await setter(value)  # type: ignore[arg-type]
                        return
                    if setter is not None:
                        setter(value)  # type: ignore[misc]
                        return
                    setattr(target, 'value', value)
                    try:
                        target.update()
                    except Exception:
                        pass

                async def _submit_goal(*_, goal_override: str | None = None) -> None:
                    og_source = goal_override if goal_override is not None else getattr(self.initial_goal_input, 'value', '')
                    og = (og_source or '').strip()
                    await _set_goal_text(og)
                    if not og:
                        ui.notify('Please enter an overall goal', color='negative', timeout=0, close_button=True)
                        return
                    if not self._begin_operation('Start'):
                        return
                    try:
                        settings_manager = get_settings()
                        settings = self._default_settings(overall_goal=og)
                        settings_manager.save_settings(settings)
                        await self.controller.start_new_tree(settings)
                        self._set_overall_goal_heading(og)
                        print(f"[view] start_new_tree succeeded for goal={og!r}")
                        self._initial_goal_complete()
                    except asyncio.CancelledError:
                        ui.notify('Operation cancelled', color='warning', timeout=2000)
                    except Exception as exc:
                        ui.notify(f'Start failed: {exc}', color='negative', timeout=0, close_button=True)
                        print(f"[view] start_new_tree failed: {exc}")
                    finally:
                        self._end_operation()

                ui.button('Start Building', on_click=_submit_goal).props('unelevated size=lg').classes('bg-amber-300 text-slate-900 font-semibold w-full hover:bg-amber-200 shadow-md rounded-lg')

        return panel

    def _initial_goal_complete(self) -> None:
        if self.goal_panel is not None:
            try:
                self.goal_panel.clear()
            except Exception:
                pass
            self.goal_panel = None
        self.initial_goal_input = None

    def _default_settings(self, overall_goal: str) -> TransitionSettings:
        return get_settings().load_settings(overall_goal=overall_goal)

    def _set_overall_goal_heading(self, goal: str) -> None:
        self._current_overall_goal = goal.strip()
        label = self._goal_heading_label
        if label is None:
            return
        text = f"Goal: {self._current_overall_goal}" if self._current_overall_goal else ''
        try:
            label.text = text
        except Exception:
            try:
                label.set_text(text)  # type: ignore[attr-defined]
            except Exception:
                pass

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

    def _render_settings_editor(
        self,
        initial: TransitionSettings,
        *,
        persistent_selectors: bool = False,
        show_user_feedback: bool = True,
        allow_overall_goal_edit: bool = True,
    ) -> Dict[str, ui.element]:
        """Left-side settings editor used in both Start area and iteration cards."""

        settings_manager = get_settings()

        preset_entries = list(feedback_presets.list_enabled_presets())
        preset_lookup = {p.id: p for p in preset_entries}
        preset_label_to_id = {preset.label: preset.id for preset in preset_entries}
        preset_id_to_label = {preset.id: preset.label for preset in preset_entries}
        first_preset_label = next(iter(preset_label_to_id.keys()), '')
        initial_preset_id = (
            (getattr(initial, 'feedback_preset_id', None) or settings_manager.get_feedback_preset_id()).strip()
        )
        initial_preset_label = preset_id_to_label.get(initial_preset_id, first_preset_label)
        feedback_preset_select = ui.select(
            options=list(preset_label_to_id.keys()),
            value=initial_preset_label,
            label='Auto feedback',
        ).props('dense outlined').classes('w-full')
        feedback_preset_select._preset_value_map = preset_label_to_id  # type: ignore[attr-defined]
        preset_summary_label = ui.label('').classes('text-xs text-gray-500 self-start')

        def _summarize_preset(label: str) -> str:
            preset_id = preset_label_to_id.get(label, '')
            preset = preset_lookup.get(preset_id)
            if not preset:
                return 'Fallback to classic screenshot cadence.'
            fragments: List[str] = []
            for action in preset.actions:
                if action.kind == 'wait':
                    fragments.append(f"wait {action.seconds:.1f}s")
                elif action.kind == 'keypress':
                    fragments.append(f"key {action.key} ({action.duration_ms}ms)")
                elif action.kind == 'screenshot':
                    fragments.append(f'shot "{action.label}"')
            summary = ', '.join(fragments[:6])
            if len(fragments) > 6:
                summary += f", +{len(fragments) - 6} more"
            desc = preset.description or ''
            if desc and summary:
                return f"{desc} · Auto feedback: {summary}"
            if desc:
                return desc
            return f"Auto feedback: {summary}" if summary else 'Auto feedback ready.'

        def _update_preset_summary() -> None:
            try:
                current_label = str(getattr(feedback_preset_select, 'value', initial_preset_label) or initial_preset_label)
            except Exception:
                current_label = initial_preset_label
            summary = _summarize_preset(current_label)
            try:
                preset_summary_label.text = summary
            except Exception:
                try:
                    preset_summary_label.set_text(summary)  # type: ignore[attr-defined]
                except Exception:
                    pass

        _update_preset_summary()

        def _refresh_summary() -> None:
            _update_preset_summary()

        feedback_preset_select.on_value_change(lambda _: _refresh_summary())
        feedback_preset_select.on('update:model-value', lambda _: _refresh_summary())

        if allow_overall_goal_edit:
            overall_goal = ui.textarea(label='Overall goal', value=initial.overall_goal).classes('w-full')
        else:
            overall_goal = SimpleNamespace(value=initial.overall_goal or '')
        if show_user_feedback:
            user_feedback = ui.textarea(label='User feedback', value=initial.user_feedback).classes('w-full')
        else:
            user_feedback = SimpleNamespace(value=initial.user_feedback or '')

        with ui.column().classes('w-full gap-2'):
            ui.label('Coding models').classes('w-full text-base font-medium')
            code_selector = self._register_selector(ModelSelector(
                initial_value=initial.code_model,
                vision_only=False,
                label='model',
                on_change=lambda v: None,
            ), persistent=persistent_selectors)
            code_model = code_selector.input

        with ui.column().classes('w-full gap-2 pt-2'):
            ui.label('Vision model').classes('w-full text-base font-medium')
            vision_selector = self._register_selector(ModelSelector(
                initial_value=initial.vision_model,
                vision_only=True,
                label='model',
                on_change=lambda v: None,
                single_selection=True,
            ), persistent=persistent_selectors)
            vision_model = vision_selector.input

        return {
            'user_feedback': user_feedback,
            'overall_goal': overall_goal,
            'code_model': code_model,
            'vision_model': vision_model,
            'feedback_preset': feedback_preset_select,
        }

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

            first_output = next(iter(node.outputs.values())) if node.outputs else None
            first_output_slug = next(iter(node.outputs.keys())) if node.outputs else None
            preferred_artifacts = node.input_artifacts or (first_output.artifacts if first_output else None)
            analysis_map: Dict[str, Any] = {}
            if preferred_artifacts:
                try:
                    analysis_map = dict(getattr(preferred_artifacts, 'analysis', {}) or {})
                except Exception:
                    analysis_map = {}

            input_messages = self._get_input_messages(node, first_output_slug)

            messages_dialog = None
            open_messages_handler = None
            if input_messages:
                messages_dialog = ui.dialog()
                messages_dialog.props('persistent')
                msgs_snapshot = list(input_messages)

                def _open_messages_dialog(msgs=msgs_snapshot) -> None:
                    self._render_messages_dialog(messages_dialog, list(msgs))
                    messages_dialog.open()

                messages_dialog.on('hide', lambda _: messages_dialog.clear())
                open_messages_handler = _open_messages_dialog

            summary_dialog, summary_button_label, summary_disabled = create_node_summary_dialog(node)

            meta_rendered = False

            def _render_meta_controls() -> None:
                nonlocal meta_rendered
                if meta_rendered:
                    return
                with ui.row().classes('w-full items-center gap-2 mb-2'):
                    if open_messages_handler:
                        ui.button('📋 Messages', on_click=open_messages_handler).props('flat dense').classes('text-sm p-0 min-h-0 self-start')
                meta_rendered = True

            show_input_column = index > 1
            allow_meta = index > 1
            operation_basis = 'basis-1/3' if show_input_column else 'basis-1/2'
            output_basis = 'basis-1/3' if show_input_column else 'basis-1/2'
            column_header_class = 'text-[11px] uppercase tracking-[0.4em] text-gray-400 dark:text-gray-500'

            with ui.row().classes('w-full items-start gap-4 flex-nowrap'):
                if show_input_column:
                    with ui.column().classes('basis-1/3 min-w-0 gap-4 input-column'):
                        ui.label('INPUT').classes(column_header_class)
                        if allow_meta:
                            _render_meta_controls()
                        asset_label_map = {}
                        artifacts = preferred_artifacts
                        try:
                            assets = list(getattr(artifacts, 'assets', []) or [])
                            for asset in assets:
                                if asset.role == 'input':
                                    asset_label_map[asset.path] = str(asset.metadata.get('label', '') or '').strip()
                        except Exception:
                            asset_label_map = {}

                        if node.source_model_slug:
                            ui.label(f'Input from {node.source_model_slug}').classes('text-xs uppercase tracking-wide text-gray-500')

                        ui.label('AUTO FEEDBACK').classes('text-sm font-semibold')
                        input_entries: List[tuple[int, str, str, str]] = []
                        primary_html_url = ''
                        limit_note = ''
                        try:
                            from pathlib import Path as _P
                            raw_paths = list(getattr(artifacts, 'input_screenshot_filenames', []) or [])
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
                            limit_note = analysis_map.get('input_screenshot_limit', '') if isinstance(analysis_map, dict) else ''
                        except Exception:
                            input_entries = []
                            limit_note = ''
                            primary_html_url = ''

                        if artifacts is None:
                            ui.label('Input analysis pending. Select an output to continue.').classes('text-sm text-gray-500')
                        elif input_entries:
                            if limit_note:
                                ui.label(limit_note).classes('text-xs text-amber-300')
                            with ui.row().classes('w-full gap-2 flex-wrap'):
                                for idx, raw_path, artifact_url, html_url in input_entries:
                                    with ui.column().classes('gap-1 w-[120px]'):
                                        target_link = artifact_url or raw_path
                                        with ui.link('', target_link, new_tab=True).classes('block no-underline'):
                                            ui.image(raw_path).classes('w-[120px] h-[80px] object-cover border border-gray-600 rounded hover:border-blue-400 transition-colors duration-150')
                                        with ui.row().classes('items-center justify-between w-full'):
                                            label_text = asset_label_map.get(raw_path) or f'#{idx + 1}'
                                            ui.label(label_text).classes('text-xs text-gray-400')
                            size = format_html_size(node.html_input)
                            with ui.row().classes('items-center gap-2 mt-1'):
                                ui.icon('content_copy').classes('text-sm cursor-pointer').on('click', lambda html=node.html_input: self._copy_to_clipboard(html))
                                ui.label('HTML').classes('text-sm')
                                ui.label(f'({size})').classes('text-sm text-gray-600 dark:text-gray-400')
                                if primary_html_url:
                                    ui.link('Open', primary_html_url, new_tab=True).classes('text-sm')
                        else:
                            ui.label('(no input screenshots yet)').classes('text-sm text-gray-500')

                        in_logs = list(getattr(artifacts, 'input_console_logs', []) or []) if artifacts else []
                        in_title = f"Console logs ({'empty' if len(in_logs) == 0 else len(in_logs)})"
                        with ui.expansion(in_title):
                            if in_logs:
                                in_logs_text = '\n\n'.join(in_logs)
                                ui.markdown(in_logs_text)
                            else:
                                ui.label('(no console logs)')

                        _va_raw = extract_vision_summary(artifacts)
                        _va_lines = [line for line in _va_raw.splitlines() if line.strip()]
                        va_title = f"Vision Analysis ({'empty' if len(_va_lines) == 0 else len(_va_lines)})"
                        with ui.expansion(va_title):
                            va_text = _va_raw
                            has_inputs = bool(getattr(artifacts, 'input_screenshot_filenames', []) if artifacts else [])
                            if not has_inputs:
                                va_text = '(no input screenshot)'
                            elif not va_text.strip():
                                va_text = '(pending)'
                            else:
                                va_text = va_text.replace('\n', '\n\n')
                            ui.markdown(va_text)

                with ui.column().classes(f'{operation_basis} min-w-0 gap-3 operation-column'):
                    if allow_meta and not show_input_column:
                        _render_meta_controls()
                    ui.label('OPERATION').classes(column_header_class)
                    inputs = self._render_settings_editor(
                        node.settings,
                        allow_overall_goal_edit=False,
                        show_user_feedback=(index > 1),
                    )
                    ui.label('Adjust prompt, model, and auto feedback before transforming.').classes('text-xs text-gray-500')

                def _collect_transition_settings(fallback_code_slug: str = '', *, user_feedback_override: str | None = None) -> TransitionSettings:
                    settings_manager = get_settings()
                    feedback_preset_id = self._extract_preset_id(inputs['feedback_preset'])
                    code_template = settings_manager.get_code_template()
                    vision_template = settings_manager.get_vision_template()
                    iter_shots = settings_manager.get_input_screenshot_count()
                    selected_model = inputs['code_model'].value or fallback_code_slug
                    feedback_value = user_feedback_override if user_feedback_override is not None else (inputs['user_feedback'].value or '')
                    updated = TransitionSettings(
                        code_model=selected_model,
                        vision_model=inputs['vision_model'].value or '',
                        overall_goal=node.settings.overall_goal,
                        user_feedback=feedback_value,
                        code_template=code_template,
                        code_system_prompt_template=settings_manager.get_code_system_prompt_template(),
                        vision_template=vision_template,
                        input_screenshot_count=iter_shots,
                        feedback_preset_id=feedback_preset_id,
                    )
                    settings_manager.save_settings(updated)
                    return updated

                async def _transform_current_node() -> None:
                    if not self._begin_operation('Transform'):
                        return
                    try:
                        updated = _collect_transition_settings()
                        await self.controller.rerun_node(node.id, updated)
                    except asyncio.CancelledError:
                        ui.notify('Transform cancelled', color='warning', timeout=2000)
                    except Exception as exc:
                        op_status.enqueue_notification(f'Transform failed: {exc}', color='negative', timeout=0, close_button=True)
                    finally:
                        self._end_operation()

                with ui.column().classes('w-[64px] shrink-0 items-center justify-center h-full transform-column'):
                    ui.button('', icon='arrow_forward', on_click=lambda: asyncio.create_task(_transform_current_node())).props('unelevated size=lg').classes('w-16 h-36 bg-primary-500 text-white shadow-lg rounded-xl mt-4')

                with ui.column().classes(f'{output_basis} min-w-0 gap-6 output-column'):
                    if allow_meta and not show_input_column:
                        _render_meta_controls()
                    output_messages_dialog = ui.dialog()
                    output_messages_dialog.props('persistent')

                    def _open_output_messages_dialog() -> None:
                        if not node.outputs:
                            ui.notify('No messages available for this iteration yet.', color='info', timeout=2000)
                            return
                        message_slugs = list(node.outputs.keys())

                        def _render_for(slug: str) -> None:
                            output = node.outputs.get(slug)
                            if not output:
                                ui.notify(f'Messages missing for {slug}', color='warning', timeout=2000)
                                return
                            history = list(output.messages or [])
                            if output.assistant_response:
                                history.append({"role": "assistant", "content": output.assistant_response})
                            if not history:
                                history = [{"role": "system", "content": "(no message history captured)"}]

                            def _header_controls(row: ui.row, current_slug: str = slug) -> None:
                                with row:
                                    selector = ui.select(
                                        options=message_slugs,
                                        value=current_slug,
                                    ).props('dense outlined hide-dropdown-icon').classes('w-40 text-xs text-gray-500')

                                    def _on_change(e: Any) -> None:
                                        value = getattr(e, 'value', None)
                                        if not value:
                                            args = getattr(e, 'args', None)
                                            if isinstance(args, dict):
                                                value = args.get('value')
                                            elif args:
                                                value = args
                                        if value is not None:
                                            # Handle case where NiceGUI emits index instead of value
                                            try:
                                                # Check if value is an integer (index) or string representation of integer
                                                if isinstance(value, int):
                                                    idx = value
                                                elif isinstance(value, str) and value.isdigit():
                                                    idx = int(value)
                                                else:
                                                    idx = None

                                                if idx is not None and 0 <= idx < len(message_slugs):
                                                    # Use index to get the actual slug
                                                    slug = message_slugs[idx]
                                                else:
                                                    # Use value directly as slug
                                                    slug = str(value)
                                            except (ValueError, IndexError):
                                                slug = str(value)

                                            _render_for(slug)

                                    selector.on('update:model-value', _on_change)

                            self._render_messages_dialog(output_messages_dialog, history, header_controls=_header_controls)

                        _render_for(message_slugs[0])
                        output_messages_dialog.open()

                    with ui.row().classes('w-full items-center justify-between'):
                        ui.label('OUTPUT').classes(column_header_class)
                        if node.outputs:
                            with ui.row().classes('items-center gap-2'):
                                ui.button('📋 Messages', on_click=_open_output_messages_dialog).props('flat dense').classes('text-xs font-semibold tracking-wide uppercase text-gray-500')
                                summary_handler = summary_dialog.open if not summary_disabled else (lambda: None)
                                summary_btn = ui.button('Summary', on_click=summary_handler).props('flat dense').classes('text-xs font-semibold tracking-wide uppercase text-gray-500')
                                if summary_disabled:
                                    summary_btn.props('disable')
                    for model_slug, out in node.outputs.items():
                        with ui.column().classes('w-full min-w-0 gap-2 border rounded p-2'):
                            ui.label(f'{model_slug}').classes('text-sm font-semibold')
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
                                ui.image(out_png).classes('w-full h-auto max-w-full rounded border border-gray-600')
                            out_html = out.html_output
                            from pathlib import Path as _OutputPath
                            out_html_url = ''
                            try:
                                snap_path = _OutputPath(out_png)
                                if snap_path.exists():
                                    html_path = snap_path.with_suffix('.html')
                                    if html_path.exists():
                                        out_html_url = f"/artifacts/{html_path.name}"
                            except Exception:
                                out_html_url = ''
                            diff_dialog = ui.dialog()
                            with diff_dialog, ui.card().classes('w-[90vw] max-w-[900px]'):
                                with ui.row().classes('items-center justify-between w-full'):
                                    ui.label('Diff vs input').classes('text-lg font-semibold')
                                    ui.button(icon='close', on_click=diff_dialog.close).props('flat round dense')
                                diff_html = self._create_visual_diff(node.html_input, out.html_output)
                                ui.html(f'<div class="border rounded p-4 diff-body">{diff_html}</div>')
                            size = format_html_size(out.html_output)
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('content_copy').classes('text-sm cursor-pointer').on('click', lambda html=out.html_output: self._copy_to_clipboard(html))
                                ui.label('HTML').classes('text-sm')
                                ui.label(f'({size})').classes('text-sm text-gray-600 dark:text-gray-400')
                                ui.label(':').classes('text-sm')
                                ui.link('Open', out_html_url, new_tab=True).classes('text-sm')
                                ui.button('Diff', on_click=diff_dialog.open).props('flat dense').classes('text-sm p-0 min-h-0')
                                if (out.reasoning_text or '').strip():
                                    with ui.dialog() as reasoning_dialog:
                                        reasoning_dialog.props('persistent')
                                        with ui.card().classes('w-[90vw] max-w-[900px]'):
                                            with ui.row().classes('items-center justify-between w-full'):
                                                ui.label('Model Reasoning').classes('text-lg font-semibold')
                                                ui.button(icon='close', on_click=reasoning_dialog.close).props('flat round dense')
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

                            async def _select_model(slug: str = model_slug) -> None:
                                if not self._begin_operation('Select'):
                                    return
                                try:
                                    updated = _collect_transition_settings(slug, user_feedback_override='')
                                    await self.controller.select_model(node.id, updated, slug)
                                except asyncio.CancelledError:
                                    ui.notify(f'Select cancelled for {slug}', color='warning', timeout=2000)
                                except Exception as exc:
                                    op_status.enqueue_notification(f'Select failed: {exc}', color='negative', timeout=0, close_button=True)
                                finally:
                                    self._end_operation()

                            ui.button('Select', on_click=(lambda slug=model_slug: lambda: asyncio.create_task(_select_model(slug)))(model_slug)).classes('w-full')
        return card

    def _get_input_messages(self, node: IterationNode, preferred_slug: str | None) -> List[Dict[str, Any]]:
        parent_id = node.parent_id
        if parent_id:
            parent = self.controller.get_node(parent_id)
            if parent:
                parent_output = parent.outputs.get(preferred_slug) if preferred_slug else None
                if parent_output is None and parent.outputs:
                    parent_output = next(iter(parent.outputs.values()))
                if parent_output:
                    history = list(parent_output.messages or [])
                    if parent_output.assistant_response:
                        history.append({"role": "assistant", "content": parent_output.assistant_response})
                    return history

        current_output = node.outputs.get(preferred_slug) if preferred_slug else None
        if current_output is None and node.outputs:
            current_output = next(iter(node.outputs.values()))
        if current_output and current_output.messages:
            return list(current_output.messages)
        return []

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
    def _extract_preset_id(self, element: ui.element) -> str:
        try:
            raw = getattr(element, 'value', '')
            value_map = getattr(element, '_preset_value_map', {}) or {}
            mapped = value_map.get(raw, raw)
        except Exception:
            mapped = ''
        return str(mapped or '')

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

    def _render_messages_dialog(self, dialog: ui.dialog, messages: List[Dict[str, Any]], header_controls: Callable[[ui.row], None] | None = None) -> None:
        """Lazy-render the heavy message history dialog only when the user opens it."""
        def _format_structured_value(value: Any) -> str:
            if isinstance(value, str):
                return value
            try:
                return json.dumps(value, ensure_ascii=False, indent=2)
            except Exception:
                try:
                    return str(value)
                except Exception:
                    return ""

        def _append_text_part(parts: List[Dict[str, Any]], text: str | None) -> None:
            if text:
                parts.append({'type': 'text', 'text': str(text)})

        def _append_tool_metadata(parts: List[Dict[str, Any]], message: Dict[str, Any]) -> None:
            metadata_lines: List[str] = []
            tool_name = message.get('name') or message.get('tool')
            if tool_name:
                metadata_lines.append(f"Tool: {tool_name}")
            tool_id = message.get('tool_call_id')
            if tool_id:
                metadata_lines.append(f"Call ID: {tool_id}")
            arguments = message.get('arguments')
            if arguments is not None:
                metadata_lines.append("Arguments:")
                metadata_lines.append(_format_structured_value(arguments))
            if metadata_lines:
                _append_text_part(parts, "\n".join(metadata_lines))

        def _append_tool_call_details(parts: List[Dict[str, Any]], message: Dict[str, Any]) -> bool:
            tool_calls = message.get('tool_calls')
            if not isinstance(tool_calls, list) or not tool_calls:
                return False
            handled_any = False
            for idx, call in enumerate(tool_calls, start=1):
                if not isinstance(call, dict):
                    continue
                lines: List[str] = [f"Tool call #{idx}"]
                call_type = call.get('type')
                if call_type:
                    lines.append(f"Type: {call_type}")
                call_id = call.get('id') or call.get('tool_call_id')
                if call_id:
                    lines.append(f"Call ID: {call_id}")
                function_meta = call.get('function')
                if isinstance(function_meta, dict):
                    fn_name = function_meta.get('name')
                    arguments = function_meta.get('arguments')
                else:
                    fn_name = call.get('name')
                    arguments = call.get('arguments')
                if fn_name:
                    lines.append(f"Function: {fn_name}")
                if arguments is not None:
                    arg_value: Any = arguments
                    if isinstance(arg_value, str):
                        stripped = arg_value.strip()
                        if stripped:
                            try:
                                arg_value = json.loads(stripped)
                            except Exception:
                                arg_value = stripped
                    lines.append("Arguments:")
                    lines.append(_format_structured_value(arg_value))
                _append_text_part(parts, "\n".join(lines))
                handled_any = True
            return handled_any

        def _handle_tool_output(parts: List[Dict[str, Any]], content: Any) -> bool:
            if not isinstance(content, str):
                return False
            parsed: Any
            try:
                parsed = json.loads(content)
            except Exception:
                return False
            if not isinstance(parsed, dict):
                return False
            handled = False
            if 'result' in parsed:
                _append_text_part(parts, f"Result:\n{_format_structured_value(parsed['result'])}")
                handled = True
            if 'console' in parsed:
                console_raw = parsed['console']
                lines: List[str]
                if isinstance(console_raw, list):
                    lines = [str(line) for line in console_raw]
                else:
                    lines = [str(console_raw)]
                _append_text_part(parts, "Console:\n" + "\n".join(lines))
                handled = True
            for key in sorted(parsed.keys()):
                if key in {'result', 'console'}:
                    continue
                _append_text_part(parts, f"{key}: {_format_structured_value(parsed[key])}")
                handled = True
            return handled

        dialog.clear()
        with dialog:
            with ui.card().classes('w-[90vw] max-w-[1200px]'):
                with ui.row().classes('items-center justify-between w-full'):
                    ui.label('Message History').classes('text-lg font-semibold')
                    controls_row = ui.row().classes('items-center gap-2')
                    if header_controls:
                        header_controls(controls_row)
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
                            message_dict = m if isinstance(m, dict) else {}
                            has_tool_calls = bool(message_dict.get('tool_calls'))
                            special_handled = False
                            if role == 'tool':
                                _append_tool_metadata(parts, message_dict)
                                special_handled = _handle_tool_output(parts, content)
                            elif role == 'assistant' and has_tool_calls:
                                special_handled = _append_tool_call_details(parts, message_dict)
                            if not special_handled:
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
                                    chip_label = 'Tool response' if role == 'tool' else ('Assistant tool call' if role == 'assistant' and has_tool_calls else (role or 'unknown'))
                                    ui.html(f"<span class='msg-chip {role_class}'>{_html.escape(chip_label)}</span>")
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
        for attr in ('chat_container', 'goal_panel', 'scroll_area'):
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
