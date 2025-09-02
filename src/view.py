# src/view.py
from __future__ import annotations

import asyncio
import json
from typing import Dict, List
from pathlib import Path

from nicegui import ui
from diff_match_patch import diff_match_patch
import html as _html

from .controller import IterationController
from .interfaces import IterationEventListener, IterationNode, TransitionSettings
from . import op_status
from . import config as app_config
from . import prefs
from .model_selector import ModelSelector


class NiceGUIView(IterationEventListener):
    def __init__(self, controller: IterationController):
        self.controller = controller
        self.controller.add_listener(self)
        self.node_cards: Dict[str, ui.card] = {}
        self.chat_container: ui.element | None = None
        self.scroll_area: ui.scroll_area | None = None
        self.initial_goal_input: ui.textarea | None = None
        # --- Operation status & lock ---
        self._op_busy: bool = False
        self._status_container: ui.element | None = None
        self._status_timer: ui.timer | None = None

        # Set some default styling
        ui.dark_mode().enable()

    def render(self) -> None:
        # Scoped CSS: Make the default CLOSE button text black on error notifications
        ui.html('''<style>
        .q-notification.bg-negative .q-btn--flat,
        .q-notification.text-negative .q-btn--flat { color: black !important; }
        .q-expansion-item.nicegui-expansion { border: 1px solid #555 !important; border-radius: 6px !important; }
        </style>''')
        with ui.column().classes('w-full h-screen p-4 gap-3'):
            ui.label('Simple Vibe Iterator').classes('text-2xl font-bold')

            # Container for worker status boxes
            with ui.row().classes('fixed top-2 right-2 z-50 gap-2 items-start') as sc:
                self._status_container = sc
            self._status_timer = ui.timer(0.25, self._refresh_phase)
            self._refresh_phase()

            with ui.scroll_area().classes('flex-grow w-full') as scroll:
                self.scroll_area = scroll
                with ui.column().classes('w-full gap-4'):
                    # Start area with full settings editor (scrollable like normal cards)
                    with ui.card().classes('w-full p-4'):
                        init_settings = self._default_settings(overall_goal='')
                        inputs = self._render_settings_editor(init_settings)
                        async def _start() -> None:
                            og = (inputs['overall_goal'].value or '').strip()
                            if not og:
                                ui.notify('Please enter an overall goal', color='negative', timeout=0, close_button=True)
                                return
                            if not self._begin_operation('Start'):
                                return
                            try:
                                settings = TransitionSettings(
                                    code_model=inputs['code_model'].value or '',
                                    vision_model=inputs['vision_model'].value or '',
                                    overall_goal=og,
                                    user_steering=inputs['user_steering'].value or '',
                                    code_template=inputs['code_template'].value or '',
                                    vision_template=inputs['vision_template'].value or '',
                                )
                                prefs.set('model.code', settings.code_model)
                                prefs.set('model.vision', settings.vision_model)
                                prefs.set('template.code', settings.code_template)
                                prefs.set('template.vision', settings.vision_template)
                                await self.controller.apply_transition(None, settings)
                            except Exception as exc:
                                ui.notify(f'Start failed: {exc}', color='negative', timeout=0, close_button=True)
                            finally:
                                self._end_operation()
                        ui.button('Start', on_click=_start).classes('w-full')

                    # Iteration chain container
                    self.chat_container = ui.column().classes('w-full gap-4')

    def _default_settings(self, overall_goal: str) -> TransitionSettings:
        cfg = app_config.get_config()
        return TransitionSettings(
            code_model=prefs.get('model.code', cfg.code_model),
            vision_model=prefs.get('model.vision', cfg.vision_model),
            overall_goal=overall_goal,
            user_steering='',
            code_template=prefs.get('template.code', cfg.code_template),
            vision_template=prefs.get('template.vision', cfg.vision_template),
        )

    # IterationEventListener
    async def on_node_created(self, node: IterationNode) -> None:
        await self._rebuild_chain(node.id)
        await asyncio.sleep(0.05)
        if self.scroll_area:
            self.scroll_area.scroll_to(percent=1.0)

    async def _rebuild_chain(self, leaf_id: str) -> None:
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
        self.node_cards.clear()
        with self.chat_container:
            for idx, node in enumerate(chain, start=1):
                card = self._create_node_card(idx, node)
                self.node_cards[node.id] = card

    def _render_settings_editor(self, initial: TransitionSettings) -> Dict[str, ui.element]:
        # Left-side settings editor used in both Start area and iteration cards
        overall_goal = ui.textarea(label='Overall goal', value=initial.overall_goal).classes('w-full')
        user_steering = ui.textarea(label='Optional user steering', value=initial.user_steering).classes('w-full')

        with ui.expansion('Coding').classes('w-full') as code_exp:
            code_selector = ModelSelector(
                initial_value=initial.code_model,
                vision_only=False,
                label='model',
                on_change=lambda v: None,
            )
            code_model = code_selector.input
            code_tmpl = ui.textarea(label='coding template', value=initial.code_template).classes('w-full')

        with ui.expansion('Vision').classes('w-full') as vision_exp:
            vision_selector = ModelSelector(
                initial_value=initial.vision_model,
                vision_only=True,
                label='model',
                on_change=lambda v: None,
                single_selection=True,
            )
            vision_model = vision_selector.input
            vision_tmpl = ui.textarea(label='vision template', value=initial.vision_template).classes('w-full')

        return {
            'user_steering': user_steering,
            'overall_goal': overall_goal,
            'code_model': code_model,
            'vision_model': vision_model,
            'code_template': code_tmpl,
            'vision_template': vision_tmpl,
        }

    def _create_node_card(self, index: int, node: IterationNode) -> ui.card:
        with ui.card().classes('w-full p-4') as card:
            with ui.row().classes('items-center justify-between w-full'):
                ui.label(f'Iteration {index}').classes('text-lg font-semibold')

            with ui.row().classes('w-full items-start gap-6 flex-nowrap'):
                with ui.column().classes('basis-5/12 min-w-0 gap-3'):
                    inputs = self._render_settings_editor(node.settings)

                with ui.column().classes('basis-7/12 min-w-0 gap-4'):
                    first_output = next(iter(node.outputs.values())) if node.outputs else None
                    with ui.row().classes('w-full items-start gap-6 flex-nowrap'):
                        with ui.column().classes('basis-1/2 min-w-0 gap-2'):
                            ui.label('INPUT SCREENSHOT').classes('text-sm font-semibold')
                            try:
                                from pathlib import Path as _P
                                input_png = first_output.artifacts.input_screenshot_filename if first_output else ''
                                input_html_url = ''
                                if input_png:
                                    p = _P(input_png)
                                    html_candidate = p.with_suffix('.html')
                                    if html_candidate.exists():
                                        input_html_url = '/artifacts/' + html_candidate.name
                            except Exception:
                                input_png = ''
                                input_html_url = ''
                            if input_png:
                                ui.image(input_png).classes('w-full h-auto max-w-full border rounded')
                            else:
                                ui.label('(no input screenshot)')
                            if input_html_url:
                                with ui.row().classes('items-center gap-2'):
                                    ui.icon('content_copy').classes('text-sm cursor-pointer').on('click', lambda html=node.html_input: self._copy_to_clipboard(html))
                                    ui.label('HTML:').classes('text-sm')
                                    ui.link('Open', input_html_url, new_tab=True).classes('text-sm')
                            in_logs = list(getattr(first_output.artifacts, 'input_console_logs', []) if first_output else [])
                            in_title = f"Console logs ({'empty' if len(in_logs) == 0 else len(in_logs)})"
                            with ui.expansion(in_title):
                                if in_logs:
                                    in_logs_text = '\n\n'.join(in_logs)
                                    ui.markdown(in_logs_text)
                                else:
                                    ui.label('(no console logs)')
                            _va_raw = first_output.artifacts.vision_output if first_output else ''
                            _va_lines = [l for l in _va_raw.splitlines() if l.strip()]
                            va_title = f"Vision Analysis ({'empty' if len(_va_lines) == 0 else len(_va_lines)})"
                            with ui.expansion(va_title):
                                va_text = first_output.artifacts.vision_output if first_output else ''
                                if not (getattr(first_output.artifacts, 'input_screenshot_filename', '') if first_output else '').strip():
                                    va_text = '(no input screenshot)'
                                elif not (va_text or '').strip():
                                    va_text = '(pending)'
                                else:
                                    va_text = va_text.replace('\n', '\n\n')
                                ui.markdown(va_text)

                        with ui.column().classes('basis-1/2 min-w-0 gap-6'):
                            for model_slug, out in node.outputs.items():
                                with ui.column().classes('min-w-0 gap-2 border rounded p-2'):
                                    ui.label(f'OUTPUT {model_slug}').classes('text-sm font-semibold')
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
                                    if out_html_url:
                                        with ui.row().classes('items-center gap-2'):
                                            ui.icon('content_copy').classes('text-sm cursor-pointer').on('click', lambda html=out.html_output: self._copy_to_clipboard(html))
                                            ui.label('HTML:').classes('text-sm')
                                            ui.link('Open', out_html_url, new_tab=True).classes('text-sm')
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
                                    ui.button('Diff', on_click=diff_dialog.open).props('outline dense')
                                    out_logs = list(out.artifacts.console_logs or [])
                                    out_title = f"Console logs ({'empty' if len(out_logs) == 0 else len(out_logs)})"
                                    with ui.expansion(out_title):
                                        if out_logs:
                                            out_logs_text = '\n\n'.join(out_logs)
                                            ui.markdown(out_logs_text)
                                        else:
                                            ui.label('(no console logs)')
                                    async def _iterate(model_slug=model_slug) -> None:
                                        if not self._begin_operation('Iterate'):
                                            return
                                        try:
                                            updated = TransitionSettings(
                                                code_model=model_slug,
                                                vision_model=inputs['vision_model'].value or '',
                                                overall_goal=inputs['overall_goal'].value or '',
                                                user_steering=inputs['user_steering'].value or '',
                                                code_template=inputs['code_template'].value or '',
                                                vision_template=inputs['vision_template'].value or '',
                                            )
                                            prefs.set('model.code', model_slug)
                                            prefs.set('model.vision', updated.vision_model)
                                            prefs.set('template.code', updated.code_template)
                                            prefs.set('template.vision', updated.vision_template)
                                            await self.controller.apply_transition(node.id, updated)
                                        except Exception as exc:
                                            ui.notify(f'Iterate failed: {exc}', color='negative', timeout=0, close_button=True)
                                        finally:
                                            self._end_operation()
                                    ui.button('Iterate', on_click=lambda m=model_slug: asyncio.create_task(_iterate(m))).classes('w-full')
        return card

    # --- Operation status helpers ---
    def _begin_operation(self, title: str) -> bool:
        if self._op_busy:
            ui.notify('Another operation is running. Please wait until it finishes.', color='warning')
            return False
        self._op_busy = True
        op_status.clear_all()
        self._refresh_phase()
        return True

    def _end_operation(self) -> None:
        self._op_busy = False
        # Ensure UI resets cleanly on success or error
        try:
            op_status.clear_all()
        except Exception:
            pass
        self._refresh_phase()

    def _refresh_phase(self) -> None:
        if self._status_container is None:
            return
        self._status_container.clear()
        phases = op_status.get_all_phases()
        box_classes = (
            'items-start gap-2 bg-white/90 border border-gray-300 rounded px-3 py-2 shadow '
            'dark:bg-indigo-600/20 dark:border-indigo-400/30 dark:text-indigo-100 backdrop-blur-sm'
        )
        if not phases:
            with self._status_container:
                with ui.row().classes(box_classes):
                    if self._op_busy:
                        ui.spinner('dots', color='indigo').classes('w-5 h-5')
                        ui.label('Starting...').classes('font-mono text-sm')
                    else:
                        ui.icon('check_circle', color='green').classes('w-5 h-5')
                        ui.label('No operation running').classes('font-mono text-sm')
            return
        for worker, (phase, elapsed) in phases.items():
            with self._status_container:
                with ui.row().classes(box_classes):
                    ui.spinner('dots', color='indigo').classes('w-5 h-5')
                    with ui.column().classes('leading-none gap-0'):
                        ui.label(worker).classes('font-mono text-sm')
                        ui.label(f"{phase} · {elapsed:.1f}s").classes('font-mono text-xs text-gray-600 dark:text-indigo-200')


    # --- Utilities ---
    def _copy_to_clipboard(self, text: str) -> None:
        try:
            js_text = json.dumps(text)
            ui.run_javascript(f'navigator.clipboard.writeText({js_text});')
            ui.notify('HTML copied to clipboard')
        except Exception as exc:
            ui.notify(f'Copy failed: {exc}', color='negative', timeout=0, close_button=True)



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

