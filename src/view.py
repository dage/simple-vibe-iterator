# src/view.py
from __future__ import annotations

import asyncio
import os
from typing import Dict, List

from nicegui import ui

from .controller import IterationController
from .interfaces import IterationEventListener, IterationNode, TransitionSettings


class NiceGUIView(IterationEventListener):
    def __init__(self, controller: IterationController):
        self.controller = controller
        self.controller.add_listener(self)
        self.node_cards: Dict[str, ui.card] = {}
        self.chat_container: ui.element | None = None
        self.scroll_area: ui.scroll_area | None = None
        self.initial_goal_input: ui.textarea | None = None

    def render(self) -> None:
        with ui.column().classes('w-full h-screen p-4 gap-3'):
            ui.label('Simple Vibe Iterator').classes('text-2xl font-bold')

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

        settings = self._default_settings(overall_goal)
        await self.controller.create_root(settings)

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

            # Settings editor for next transition (pre-filled with node.settings)
            with ui.expansion('Settings for next iteration'):
                code_model = ui.input(label='code_model', value=node.settings.code_model).classes('w-full')
                code_instr = ui.textarea(label='code_instructions', value=node.settings.code_instructions).classes('w-full')
                vision_model = ui.input(label='vision_model', value=node.settings.vision_model).classes('w-full')
                vision_instr = ui.textarea(label='vision_instructions', value=node.settings.vision_instructions).classes('w-full')
                overall_goal = ui.textarea(label='overall_goal', value=node.settings.overall_goal).classes('w-full')
                code_tmpl = ui.textarea(label='code_template', value=node.settings.code_template).classes('w-full')
                vision_tmpl = ui.textarea(label='vision_template', value=node.settings.vision_template).classes('w-full')

                def _iterate_from_node(nid: str) -> None:
                    updated = TransitionSettings(
                        code_model=code_model.value or '',
                        code_instructions=code_instr.value or '',
                        vision_model=vision_model.value or '',
                        vision_instructions=vision_instr.value or '',
                        overall_goal=overall_goal.value or '',
                        code_template=code_tmpl.value or '',
                        vision_template=vision_tmpl.value or '',
                    )
                    asyncio.create_task(self.controller.apply_transition(nid, updated))

                ui.button('Iterate', on_click=lambda nid=node.id: _iterate_from_node(nid)).classes('')
        return card


