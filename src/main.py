# src/main.py
from __future__ import annotations

import subprocess
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


def kill_port_process(port: int) -> None:
    """Kill any process using the specified port (except current process)"""
    import os
    import time
    current_pid = os.getpid()
    
    try:
        result = subprocess.run(['lsof', '-ti', f':{port}'], 
                              capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            killed_any = False
            for pid in pids:
                if pid and pid != str(current_pid):
                    subprocess.run(['kill', '-9', pid], check=False)
                    print(f"Killed process {pid} using port {port}")
                    killed_any = True
            
            # Wait a moment for the port to be freed
            if killed_any:
                time.sleep(0.5)
    except Exception as e:
        print(f"Could not kill process on port {port}: {e}")


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
    PORT = 8055
    
    # Kill any process using the port before starting
    kill_port_process(PORT)
    
    _ = create_app()
    ui.run(title='AI Code Generator', port=PORT)
