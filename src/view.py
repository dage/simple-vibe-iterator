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
            await self.controller.create_root(settings)
        except Exception as exc:
            ui.notify(f'Create root failed: {exc}', color='negative')
        finally:
            self._end_operation()

    def _default_settings(self, overall_goal: str) -> TransitionSettings:
        code_model = os.getenv('VIBES_CODE_MODEL', 'code-model')
        vision_model = os.getenv('VIBES_VISION_MODEL', 'vision-model')
        return TransitionSettings(
            code_model=code_model,
            code_instructions='',
            vision_model=vision_model,
            vision_instructions='',
            overall_goal=overall_goal,
            code_template=(
                'Improve the following HTML while adhering to the goal.\n'
                'Goal: {overall_goal}\n'
                'Vision analysis: {vision_output}\n'
                'Instructions: {code_instructions}\n'
                'HTML:\n{html_input}\n'
            ),
            vision_template=(
                'Analyze the HTML and its rendering to provide guidance.\n'
                'Goal: {overall_goal}\n'
                'Instructions: {vision_instructions}\n'
                'HTML:\n{html_input}\n'
            ),
        )

    # IterationEventListener
    async def on_node_created(self, node: IterationNode) -> None:
        await self._rebuild_chain(node.id)
        await asyncio.sleep(0.05)
        if self.scroll_area:
            self.scroll_area.scroll_to(percent=1.0)

    async def on_node_updated(self, node: IterationNode) -> None:
        # Not used currently; δ is atomic in this prototype
        pass

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
        with ui.card().classes('w-full p-3') as card:
            with ui.row().classes('items-center justify-between w-full'):
                ui.label(f'Iteration {index}').classes('text-lg font-semibold')

            # HTML input and output
            html_in = ui.expansion('HTML Input')
            with html_in:
                ui.code(node.html_input or '(empty)').classes('w-full')

            html_out = ui.expansion('HTML Output')
            with html_out:
                ui.code(node.html_output or '(empty)').classes('w-full')

            # Screenshot
            image_area = ui.expansion('Screenshot')
            with image_area:
                if node.artifacts.screenshot_filename:
                    ui.image(node.artifacts.screenshot_filename).classes('w-[1600px] h-auto max-w-none')
                else:
                    ui.label('(no screenshot yet)')

            # Console Logs
            logs_area = ui.expansion('Console Logs')
            with logs_area:
                logs_text = '\n'.join(node.artifacts.console_logs or []) or '(no logs)'
                ui.code(logs_text).classes('w-full')

            # Vision Analysis
            vision_area = ui.expansion('Vision Analysis')
            with vision_area:
                ui.markdown(node.artifacts.vision_output or '(pending)')

            # Flat settings inputs (pre-filled with node.settings)
            code_model = ui.input(label='code_model', value=node.settings.code_model).classes('w-full')
            code_instr = ui.textarea(label='code_instructions', value=node.settings.code_instructions).classes('w-full')
            vision_model = ui.input(label='vision_model', value=node.settings.vision_model).classes('w-full')
            vision_instr = ui.textarea(label='vision_instructions', value=node.settings.vision_instructions).classes('w-full')
            overall_goal = ui.textarea(label='overall_goal', value=node.settings.overall_goal).classes('w-full')
            code_tmpl = ui.textarea(label='code_template', value=node.settings.code_template).classes('w-full')
            vision_tmpl = ui.textarea(label='vision_template', value=node.settings.vision_template).classes('w-full')

            async def _iterate_from_node(nid: str) -> None:
                if not self._begin_operation('Iterate'):
                    return
                try:
                    updated = TransitionSettings(
                        code_model=code_model.value or '',
                        code_instructions=code_instr.value or '',
                        vision_model=vision_model.value or '',
                        vision_instructions=vision_instr.value or '',
                        overall_goal=overall_goal.value or '',
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


