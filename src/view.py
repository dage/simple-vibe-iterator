# src/view.py
from __future__ import annotations

import asyncio
from typing import Dict

from nicegui import ui

from .controller import SessionController
from .interfaces import SessionData, SessionEventListener, SessionStatus


STATUS_TO_COLOR = {
    SessionStatus.IDLE: "grey",
    SessionStatus.GENERATING_HTML: "blue",
    SessionStatus.CAPTURING_SCREENSHOT: "amber",
    SessionStatus.ANALYZING_VISION: "purple",
    SessionStatus.READY_FOR_FEEDBACK: "green",
    SessionStatus.ERROR: "red",
}


class NiceGUIView(SessionEventListener):
    def __init__(self, controller: SessionController):
        self.controller = controller
        self.controller.add_listener(self)
        self.session_cards: Dict[str, ui.card] = {}
        self.chat_container: ui.element | None = None
        self.scroll_area: ui.scroll_area | None = None
        self.prompt_input: ui.textarea | None = None

    def render(self) -> None:
        with ui.column().classes('w-full h-screen p-4 gap-3'):
            ui.label('AI Code Generator').classes('text-2xl font-bold')

            with ui.scroll_area().classes('flex-grow w-full') as scroll:
                self.chat_container = ui.column().classes('w-full gap-4')
                self.scroll_area = scroll

            with ui.card().classes('w-full p-4'):
                self.prompt_input = ui.textarea(
                    placeholder='Describe your desired HTML page...'
                ).classes('w-full mb-3')
                ui.button('Generate', on_click=self._on_generate_click).classes('w-full')

    async def _on_generate_click(self) -> None:
        if not self.prompt_input:
            return
        prompt = (self.prompt_input.value or '').strip()
        if not prompt:
            ui.notify('Please enter a prompt', color='negative')
            return
        self.prompt_input.value = ''
        await self.controller.create_session(prompt)

    # SessionEventListener
    async def on_session_created(self, session: SessionData) -> None:
        if self.chat_container is None:
            return
        with self.chat_container:
            card = self._create_session_card(session)
            self.session_cards[session.id] = card
        await asyncio.sleep(0.05)
        if self.scroll_area:
            self.scroll_area.scroll_to(percent=1.0)

    async def on_session_updated(self, session: SessionData) -> None:
        await self._update_session_card(session)

    async def on_status_changed(self, session_id: str, status: SessionStatus) -> None:
        s = self.controller.get_session(session_id)
        if s:
            await self._update_session_card(s)

    def _create_session_card(self, session: SessionData) -> ui.card:
        badge = ui.badge(session.status.value).props(f'color={STATUS_TO_COLOR.get(session.status, "grey")}')
        with ui.card().classes('w-full p-3') as card:
            with ui.row().classes('items-center justify-between w-full'):
                ui.label(f'Iteration {session.iteration}').classes('text-lg font-semibold')
                card.badge = badge  # type: ignore[attr-defined]

            card.html_area = ui.expansion('HTML')  # type: ignore[attr-defined]
            with card.html_area:
                ui.code(session.html_code or '(empty)').classes('w-full')

            card.image_area = ui.expansion('Screenshot')  # type: ignore[attr-defined]
            with card.image_area:
                if session.screenshot_path:
                    ui.image(session.screenshot_path)
                else:
                    ui.label('(no screenshot yet)')

            card.logs_area = ui.expansion('Console Logs')  # type: ignore[attr-defined]
            with card.logs_area:
                logs_text = '\n'.join(session.console_logs or []) or '(no logs)'
                ui.code(logs_text).classes('w-full')

            card.vision_area = ui.expansion('Vision Analysis')  # type: ignore[attr-defined]
            with card.vision_area:
                ui.markdown(session.vision_analysis or '(pending)')

            with ui.row().classes('w-full'):
                feedback = ui.textarea(placeholder='Optional feedback for next iteration...').classes('w-full')
                ui.button('Send Feedback', on_click=lambda fb=feedback, sid=session.id: self._send_feedback(sid, fb))
        return card

    async def _update_session_card(self, session: SessionData) -> None:
        card = self.session_cards.get(session.id)
        if not card:
            return
        # status badge
        badge = getattr(card, 'badge', None)
        if badge:
            badge.text = session.status.value
            badge.props(f'color={STATUS_TO_COLOR.get(session.status, "grey")}')
        # html
        html_area = getattr(card, 'html_area', None)
        if html_area:
            html_area.clear()
            with html_area:
                ui.code(session.html_code or '(empty)').classes('w-full')
        # image
        image_area = getattr(card, 'image_area', None)
        if image_area:
            image_area.clear()
            with image_area:
                if session.screenshot_path:
                    ui.image(session.screenshot_path)
                else:
                    ui.label('(no screenshot yet)')
        # logs
        logs_area = getattr(card, 'logs_area', None)
        if logs_area:
            logs_area.clear()
            with logs_area:
                logs_text = '\n'.join(session.console_logs or []) or '(no logs)'
                ui.code(logs_text).classes('w-full')
        # vision
        vision_area = getattr(card, 'vision_area', None)
        if vision_area:
            vision_area.clear()
            with vision_area:
                ui.markdown(session.vision_analysis or '(pending)')

    async def _send_feedback(self, session_id: str, feedback_widget: ui.textarea) -> None:
        text = (feedback_widget.value or '').strip()
        if not text:
            ui.notify('Enter feedback text first', color='negative')
            return
        feedback_widget.value = ''
        await self.controller.send_feedback(session_id, text)


