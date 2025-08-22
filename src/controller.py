# src/controller.py
from __future__ import annotations

import asyncio
import uuid
from typing import Dict, List, Optional

from .interfaces import (
    AICodeService,
    BrowserService,
    SessionData,
    SessionEventListener,
    SessionStatus,
    VisionService,
)


class SessionController:
    def __init__(
        self,
        ai_service: AICodeService,
        browser_service: BrowserService,
        vision_service: VisionService,
    ) -> None:
        self._ai_service = ai_service
        self._browser_service = browser_service
        self._vision_service = vision_service
        self._sessions: Dict[str, SessionData] = {}
        self._listeners: List[SessionEventListener] = []

    def add_listener(self, listener: SessionEventListener) -> None:
        self._listeners.append(listener)

    async def create_session(self, prompt: str) -> str:
        session_id = str(uuid.uuid4())
        session = SessionData(
            id=session_id,
            iteration=1,
            initial_prompt=prompt,
            html_code="",
            screenshot_path=None,
            console_logs=[],
            vision_analysis="",
            user_feedback="",
            status=SessionStatus.IDLE,
        )

        self._sessions[session_id] = session
        await self._notify_session_created(session)
        asyncio.create_task(self._process_session(session_id))
        return session_id

    async def send_feedback(self, session_id: str, feedback: str) -> str:
        if session_id not in self._sessions:
            raise ValueError(f"Session {session_id} not found")

        old_session = self._sessions[session_id]
        new_session_id = str(uuid.uuid4())

        new_session = SessionData(
            id=new_session_id,
            iteration=old_session.iteration + 1,
            initial_prompt=old_session.initial_prompt,
            html_code="",
            screenshot_path=None,
            console_logs=[],
            vision_analysis="",
            user_feedback=feedback,
            status=SessionStatus.IDLE,
        )

        self._sessions[new_session_id] = new_session
        await self._notify_session_created(new_session)
        asyncio.create_task(self._process_session(new_session_id))
        return new_session_id

    def get_session(self, session_id: str) -> Optional[SessionData]:
        return self._sessions.get(session_id)

    def get_all_sessions(self) -> List[SessionData]:
        return list(self._sessions.values())

    async def _process_session(self, session_id: str) -> None:
        session = self._sessions[session_id]
        try:
            await self._update_status(session_id, SessionStatus.GENERATING_HTML)
            if session.iteration == 1:
                session.html_code = await self._ai_service.generate_html(session.initial_prompt)
            else:
                session.html_code = await self._ai_service.generate_html(session.user_feedback)
            await self._notify_session_updated(session)

            await self._update_status(session_id, SessionStatus.CAPTURING_SCREENSHOT)
            screenshot_path, console_logs = await self._browser_service.render_and_capture(session.html_code)
            session.screenshot_path = screenshot_path
            session.console_logs = console_logs
            await self._notify_session_updated(session)

            await self._update_status(session_id, SessionStatus.ANALYZING_VISION)
            session.vision_analysis = await self._vision_service.analyze_screenshot(
                screenshot_path, console_logs
            )

            await self._update_status(session_id, SessionStatus.READY_FOR_FEEDBACK)
            await self._notify_session_updated(session)
        except Exception as exc:
            session.error_message = str(exc)
            await self._update_status(session_id, SessionStatus.ERROR)
            await self._notify_session_updated(session)

    async def _update_status(self, session_id: str, status: SessionStatus) -> None:
        self._sessions[session_id].status = status
        await self._notify_status_changed(session_id, status)

    async def _notify_session_created(self, session: SessionData) -> None:
        for listener in self._listeners:
            await listener.on_session_created(session)

    async def _notify_session_updated(self, session: SessionData) -> None:
        for listener in self._listeners:
            await listener.on_session_updated(session)

    async def _notify_status_changed(self, session_id: str, status: SessionStatus) -> None:
        for listener in self._listeners:
            await listener.on_status_changed(session_id, status)


