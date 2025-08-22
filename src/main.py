# src/main.py
from __future__ import annotations

from nicegui import ui

from .controller import SessionController
from .services import OpenRouterVisionService, PlaywrightBrowserService, StubAICodeService
from .view import NiceGUIView


def create_app() -> NiceGUIView:
    ai_service = StubAICodeService()
    browser_service = PlaywrightBrowserService()
    vision_service = OpenRouterVisionService()

    controller = SessionController(ai_service, browser_service, vision_service)
    view = NiceGUIView(controller)
    view.render()
    return view


if __name__ in {"__main__", "__mp_main__"}:
    _ = create_app()
    ui.run(title='AI Code Generator')


