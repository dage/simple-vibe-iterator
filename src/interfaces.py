# src/interfaces.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Protocol


class SessionStatus(str, Enum):
    IDLE = "idle"
    GENERATING_HTML = "generating_html"
    CAPTURING_SCREENSHOT = "capturing_screenshot"
    ANALYZING_VISION = "analyzing_vision"
    READY_FOR_FEEDBACK = "ready_for_feedback"
    ERROR = "error"


@dataclass
class SessionData:
    id: str
    iteration: int
    initial_prompt: str
    html_code: str
    screenshot_path: Optional[str]
    console_logs: List[str]
    vision_analysis: str
    user_feedback: str
    status: SessionStatus
    error_message: Optional[str] = None


class AICodeService(Protocol):
    async def generate_html(self, prompt: str) -> str: ...


class BrowserService(Protocol):
    async def render_and_capture(self, html_code: str) -> tuple[str, List[str]]: ...


class VisionService(Protocol):
    async def analyze_screenshot(self, screenshot_path: str, console_logs: List[str]) -> str: ...


class SessionEventListener(Protocol):
    async def on_session_created(self, session: SessionData) -> None: ...

    async def on_session_updated(self, session: SessionData) -> None: ...

    async def on_status_changed(self, session_id: str, status: SessionStatus) -> None: ...


