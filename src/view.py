# src/view.py
from __future__ import annotations

import asyncio
import os
from typing import Dict, List

from nicegui import ui

from .controller import IterationController
from .interfaces import IterationEventListener, IterationNode, TransitionSettings
from . import op_status


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
        self._status_spinner: ui.element | None = None
        self._status_ok_icon: ui.element | None = None
        self._status_title: ui.label | None = None
        self._status_detail: ui.label | None = None
        self._status_timer: ui.timer | None = None

    def render(self) -> None:
        with ui.column().classes('w-full h-screen p-4 gap-3'):
            ui.label('Simple Vibe Iterator').classes('text-2xl font-bold')

            # Sticky top-right operation status (two lines, system-like font)
            with ui.row().classes('fixed top-2 right-2 z-50 items-start gap-2 bg-white/90 border border-gray-300 rounded px-3 py-2 shadow') as sc:
                self._status_container = sc
                self._status_spinner = ui.spinner('dots').classes('w-5 h-5')
                self._status_ok_icon = ui.icon('check_circle', color='green').classes('w-5 h-5')
                with ui.column().classes('leading-none gap-0'):
                    self._status_title = ui.label('No operation running').classes('font-mono text-sm')
                    self._status_detail = ui.label('').classes('font-mono text-xs text-gray-600')
            self._status_timer = ui.timer(0.25, self._refresh_phase)
            self._update_status_ui()

            # Root creation area (overall goal only)
            with ui.card().classes('w-full p-4'):
                self.initial_goal_input = ui.textarea(
                    placeholder='Overall goal...',
                ).classes('w-full mb-3')
                ui.button('Create root', on_click=self._on_create_root_click).classes('w-full')

            with ui.scroll_area().classes('flex-grow w-full') as scroll:
                self.chat_container = ui.column().classes('w-full gap-4')
                self.scroll_area = scroll

    async def _on_create_root_click(self) -> None:
        overall_goal = (self.initial_goal_input.value or '').strip() if self.initial_goal_input else ''
        if not overall_goal:
            ui.notify('Please enter an overall goal', color='negative')
            return
        if not self._begin_operation('Create root'):
            return
        try:
            settings = self._default_settings(overall_goal)
            await self.controller.apply_transition(None, settings)
        except Exception as exc:
            ui.notify(f'Create root failed: {exc}', color='negative')
        finally:
            self._end_operation()

    def _default_settings(self, overall_goal: str) -> TransitionSettings:
        code_model = os.getenv('VIBES_CODE_MODEL', 'code-model')
        vision_model = os.getenv('VIBES_VISION_MODEL', 'vision-model')
        return TransitionSettings(
            code_model=code_model,
            vision_model=vision_model,
            overall_goal=overall_goal,
            user_steering='',
            code_template=(
                'You are a code generator that must output ONLY a complete, standalone HTML document.\n'
                '- Do NOT include any explanations, comments, markdown, backticks, or fences.\n'
                '- Output must begin with <!DOCTYPE html> and contain <html>, <head>, and <body>.\n'
                '- Self-contained only: no external network assets; inline CSS/JS permitted.\n'
                '- You may use console.log() to get feedback from the browser for your next iteration.\n'
                '- Do not echo this instruction or the prompt.\n'
                '\n'
                'Goal: {overall_goal}\n'
                'User steering (guidance; do not render as text):\n{user_steering}\n'
                'Vision findings (for guidance only, do not render as text):\n{vision_output}\n'
                'Console logs (for guidance only, do not render as text):\n{console_logs}\n'
                '\n'
                'Existing HTML (may be empty) for reference and incremental improvement:\n{html_input}\n'
            ),
            vision_template=(
                'You are a vision analyzer. Your output will be fed directly to a coding model without vision.\n'
                '\n'
                'Your role:\n'
                '- Describe ONLY what is visually present in the screenshot.\n'
                '- Report concrete observations succinctly: layout, colors, text, positions, animations.\n'
                '- If the page is blank or broken, explicitly state it (e.g., "The page is completely blank" or "Only a white background is visible").\n'
                '- Flag scale/viewport problems: elements that are too small, too large, cut off, or outside the visible area.\n'
                '- Tailor observations to what helps progress toward the overall goal and user steering.\n'
                '- Do NOT give instructions, do NOT suggest code, do NOT act as a planner or orchestrator.\n'
                '- Do NOT reference files or linking; the coding model will handle implementation.\n'
                '- Use short bullet-like lines; no long prose.\n'
                '\n'
                'Context (for understanding, not to be echoed):\n'
                'Overall goal: {overall_goal}\n'
                'User steering: {user_steering}\n'
                'Code model: {code_model}\n'
                'Vision model: {vision_model}\n'
                'Browser console logs (summarize only if visually relevant):\n{console_logs}\n'
                'HTML (reference only; do not quote):\n{html_input}\n'
                '\n'
                'Output format (no preface, no labels, no code blocks):\n'
                '- Observation 1\n- Observation 2\n- Observation 3\n'
            ),
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

    def _create_node_card(self, index: int, node: IterationNode) -> ui.card:
        with ui.card().classes('w-full p-4') as card:
            with ui.row().classes('items-center justify-between w-full'):
                ui.label(f'Iteration {index}').classes('text-lg font-semibold')

            # --- Two-pane layout matching sketch ---
            with ui.row().classes('w-full items-start gap-6 flex-nowrap'):
                # Left: steering, goal, coding/vision expanders with templates
                with ui.column().classes('basis-5/12 min-w-0 gap-3'):
                    # User steering and Overall goal (order per sketch)
                    user_steering = ui.textarea(label='User steering', value=node.settings.user_steering).classes('w-full')
                    overall_goal = ui.textarea(label='Overall goal', value=node.settings.overall_goal).classes('w-full')

                    # Coding settings (model + template)
                    with ui.expansion(f'Coding ({node.settings.code_model})').classes('w-full'):
                        code_model = ui.input(label='model', value=node.settings.code_model).classes('w-full')
                        code_tmpl = ui.textarea(label='coding template', value=node.settings.code_template).classes('w-full')

                    # Vision settings (model + template)
                    with ui.expansion(f'Vision ({node.settings.vision_model})').classes('w-full'):
                        vision_model = ui.input(label='model', value=node.settings.vision_model).classes('w-full')
                        vision_tmpl = ui.textarea(label='vision template', value=node.settings.vision_template).classes('w-full')

                # Right: input/output screenshots with links and vision analysis under input
                with ui.column().classes('basis-7/12 min-w-0 gap-4'):
                    with ui.row().classes('w-full items-start gap-6 flex-nowrap'):
                        # INPUT side
                        with ui.column().classes('basis-1/2 min-w-0 gap-2'):
                            ui.label('INPUT SCREENSHOT').classes('text-sm font-semibold')
                            try:
                                from pathlib import Path as _P
                                input_png = ''
                                input_html_url = ''
                                if node.parent_id:
                                    parent = self.controller.get_node(node.parent_id)
                                    if parent and parent.artifacts.screenshot_filename:
                                        input_png = parent.artifacts.screenshot_filename
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
                                ui.link('Open input HTML', input_html_url, new_tab=True)
                            ui.label('Vision Analysis').classes('text-sm font-semibold mt-2')
                            ui.markdown(node.artifacts.vision_output or '(pending)')

                        # Center arrow
                        with ui.column().classes('basis-[60px] items-center justify-center'):
                            ui.icon('arrow_forward').classes('text-5xl text-gray-600 mt-16')

                        # OUTPUT side
                        with ui.column().classes('basis-1/2 min-w-0 gap-2'):
                            ui.label('OUTPUT SCREENSHOT').classes('text-sm font-semibold')
                            out_png = node.artifacts.screenshot_filename
                            if out_png:
                                ui.image(out_png).classes('w-full h-auto max-w-full border rounded')
                            else:
                                ui.label('(no output screenshot)')
                            try:
                                from pathlib import Path as _P
                                if out_png:
                                    p = _P(out_png)
                                    html_candidate = p.with_suffix('.html')
                                    if html_candidate.exists():
                                        ui.link('Open output HTML', '/artifacts/' + html_candidate.name, new_tab=True)
                            except Exception:
                                pass

                    # Bottom-right iterate button
                    with ui.row().classes('w-full justify-end'):
                        async def _iterate_from_node(nid: str) -> None:
                            if not self._begin_operation('Iterate'):
                                return
                            try:
                                updated = TransitionSettings(
                                    code_model=code_model.value or '',
                                    vision_model=vision_model.value or '',
                                    overall_goal=overall_goal.value or '',
                                    user_steering=user_steering.value or '',
                                    code_template=code_tmpl.value or '',
                                    vision_template=vision_tmpl.value or '',
                                )
                                await self.controller.apply_transition(nid, updated)
                            except Exception as exc:
                                ui.notify(f'Iterate failed: {exc}', color='negative')
                            finally:
                                self._end_operation()

                        ui.button('Iterate', on_click=lambda nid=node.id: asyncio.create_task(_iterate_from_node(nid))).classes('')
        return card

    # --- Operation status helpers ---
    def _update_status_ui(self) -> None:
        busy = self._op_busy
        if self._status_spinner is not None:
            self._status_spinner.visible = busy
        if self._status_ok_icon is not None:
            self._status_ok_icon.visible = not busy
        if not busy:
            if self._status_title is not None:
                self._status_title.text = 'No operation running'

    def _begin_operation(self, title: str) -> bool:
        if self._op_busy:
            ui.notify('Another operation is running. Please wait until it finishes.', color='warning')
            return False
        self._op_busy = True
        if self._status_title is not None:
            self._status_title.text = title
        if self._status_detail is not None:
            self._status_detail.text = 'Starting'
        self._update_status_ui()
        return True

    def _end_operation(self) -> None:
        self._op_busy = False
        self._update_status_ui()

    def _refresh_phase(self) -> None:
        if self._status_detail is None:
            return
        phase, elapsed = op_status.get_phase_and_elapsed()
        if phase:
            self._status_detail.text = f"{phase} · {elapsed:.1f}s"
        else:
            self._status_detail.text = ''


