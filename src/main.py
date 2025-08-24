# src/main.py
from __future__ import annotations

from nicegui import ui
from nicegui import app
from pathlib import Path
from dotenv import load_dotenv

from .controller import IterationController
from .services import (
    PlaywrightBrowserService,
    OpenRouterAICodeService,
    OpenRouterVisionService,
)
from .view import NiceGUIView


def create_app() -> NiceGUIView:
    # Load local .env for development so env vars don't need to be exported
    load_dotenv()
    # Serve artifacts directory statically for viewing saved HTML/PNGs
    artifacts_dir = str((Path.cwd() / 'artifacts').resolve())
    try:
        app.add_static_files('/artifacts', artifacts_dir)
    except Exception:
        # ignore if already added or path issues; UI still works without static route
        pass
    # Use OpenRouter-backed services (requires .env configuration)
    ai_service = OpenRouterAICodeService()
    vision_service = OpenRouterVisionService()
    browser_service = PlaywrightBrowserService()

    controller = IterationController(ai_service, browser_service, vision_service)
    view = NiceGUIView(controller)
    view.render()
    return view


if __name__ in {"__main__", "__mp_main__"}:
    _ = create_app()
    ui.run(title='AI Code Generator')


