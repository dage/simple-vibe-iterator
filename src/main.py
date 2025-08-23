# src/main.py
from __future__ import annotations

from nicegui import ui
from dotenv import load_dotenv

from .controller import IterationController
from .services import StubVisionService, PlaywrightBrowserService, StubAICodeService
from .view import NiceGUIView


def create_app() -> NiceGUIView:
    # Load local .env for development so env vars don't need to be exported
    load_dotenv()
    ai_service = StubAICodeService()
    browser_service = PlaywrightBrowserService()
    vision_service = StubVisionService()

    controller = IterationController(ai_service, browser_service, vision_service)
    view = NiceGUIView(controller)
    view.render()
    return view


if __name__ in {"__main__", "__mp_main__"}:
    _ = create_app()
    ui.run(title='AI Code Generator')


