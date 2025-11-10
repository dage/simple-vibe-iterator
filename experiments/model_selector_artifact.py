"""Model selector artifact harness.

This NiceGUI script renders the `ModelSelector` component with mock data so we can
visually validate new columns/capabilities (e.g., upcoming tool-access iconography)
without relying on OpenRouter. The harness focuses solely on coding models while
still surfacing whether those models also accept vision input. It supports two modes:

1. Interactive (`--serve`): launch the harness locally and explore it in a browser.
2. Capture (`--capture`): spin up the harness headlessly, take a Playwright
   screenshot, and write it to `artifacts/experiments/model_selector/`.

Usage examples:

```bash
# Open the harness at http://localhost:8060 and interact manually
python experiments/model_selector_artifact.py --serve

# Capture a screenshot artifact without opening a browser window
python experiments/model_selector_artifact.py --capture
```
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List
import argparse
import asyncio
import threading
import time
import urllib.error
import urllib.request

from nicegui import ui, app

# Ensure project root is importable when executing from experiments/
ROOT_DIR = Path(__file__).resolve().parents[1]
import sys
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src import model_selector as selector_module
from src.model_selector import ModelSelector
from src.or_client import ModelInfo


# ---------------- Mock model data ---------------- #

_NOW = int(time.time())

_MOCK_MODELS: List[ModelInfo] = [
    ModelInfo(
        id='anthropic/claude-3.7-sonnet',
        name='Claude 3.7 Sonnet',
        has_text_input=True,
        has_image_input=True,
        prompt_price=18.75,
        completion_price=37.50,
        created=_NOW - 21 * 24 * 3600,
        supported_parameters=['vision', 'tools', 'json_output'],
    ),
    ModelInfo(
        id='anthropic/claude-3.7-haiku',
        name='Claude 3.7 Haiku',
        has_text_input=True,
        has_image_input=False,
        prompt_price=8.50,
        completion_price=25.50,
        created=_NOW - 45 * 24 * 3600,
        supported_parameters=['tools'],
    ),
    ModelInfo(
        id='meta-llama/llama-3.3-70b-instruct',
        name='Llama 3.3 70B Instruct',
        has_text_input=True,
        has_image_input=False,
        prompt_price=6.00,
        completion_price=12.00,
        created=_NOW - 60 * 24 * 3600,
        supported_parameters=['json_output'],
    ),
    ModelInfo(
        id='google/gemini-flash-2.0',
        name='Gemini Flash 2.0',
        has_text_input=True,
        has_image_input=True,
        prompt_price=4.50,
        completion_price=9.00,
        created=_NOW - 15 * 24 * 3600,
        supported_parameters=['tools', 'function_calling'],
    ),
    ModelInfo(
        id='google/gemini-pro-1.5',
        name='Gemini Pro 1.5',
        has_text_input=True,
        has_image_input=False,
        prompt_price=10.00,
        completion_price=20.00,
        created=_NOW - 120 * 24 * 3600,
        supported_parameters=['function_calling'],
    ),
    ModelInfo(
        id='openai/o4-mini',
        name='OpenAI o4 Mini',
        has_text_input=True,
        has_image_input=False,
        prompt_price=30.00,
        completion_price=60.00,
        created=_NOW - 7 * 24 * 3600,
        supported_parameters=['tools', 'json_output'],
    ),
    ModelInfo(
        id='qwen/qwq-32b',
        name='Qwen QwQ 32B',
        has_text_input=True,
        has_image_input=True,
        prompt_price=3.50,
        completion_price=7.25,
        created=_NOW - 3 * 24 * 3600,
        supported_parameters=['vision'],
    ),
]


# `tools` or `function_calling` are treated as tool-capable for previewing the new column.
_MOCK_TOOL_CAPS: Dict[str, bool] = {
    m.id: any(key in (m.supported_parameters or []) for key in ('tools', 'function_calling'))
    for m in _MOCK_MODELS
}


async def _fake_list_models(query: str = '', vision_only: bool = False, limit: int = 2000, **_: object) -> List[ModelInfo]:
    """Offline replacement for `or_client.list_models` used inside ModelSelector."""
    await asyncio.sleep(0.05)
    words = query.lower().split()

    def _matches(model: ModelInfo) -> bool:
        text = f"{model.id} {model.name}".lower()
        if vision_only and not model.has_image_input:
            return False
        return all(word in text for word in words)

    return [m for m in _MOCK_MODELS if _matches(m)][:limit]


def _install_fake_or_client() -> None:
    selector_module.orc.list_models = _fake_list_models  # type: ignore[assignment]


# ---------------- UI harness ---------------- #


@dataclass
class HarnessState:
    code_selector: ModelSelector | None = None
    summary_label: ui.label | None = None


def _format_tools(slug: str) -> str:
    return 'Yes' if _MOCK_TOOL_CAPS.get(slug, False) else 'No'


def _update_summary(state: HarnessState) -> None:
    if not state.summary_label:
        return
    code = state.code_selector.get_value() if state.code_selector else ''
    state.summary_label.text = f"Selected code models: {code or '(none)'}"


def _build_table() -> None:
    rows = [
        {
            'id': m.id,
            'name': m.name,
            'text': 'Yes' if m.has_text_input else 'No',
            'vision': 'Yes' if m.has_image_input else 'No',
            'tools': _format_tools(m.id),
        }
        for m in _MOCK_MODELS
    ]
    columns = [
        {'name': 'name', 'label': 'Name', 'field': 'name'},
        {'name': 'id', 'label': 'ID', 'field': 'id'},
        {'name': 'text', 'label': 'Text In', 'field': 'text'},
        {'name': 'vision', 'label': 'Vision In', 'field': 'vision'},
        {'name': 'tools', 'label': 'Tools?', 'field': 'tools'},
    ]
    ui.table(columns=columns, rows=rows).classes('w-full').props('dense flat wrap-cells')


def _build_app(state: HarnessState) -> None:
    _install_fake_or_client()

    def _on_change(_: str) -> None:
        _update_summary(state)

    with ui.column().classes('max-w-5xl mx-auto p-4 gap-3'):
        ui.label('Model Selector Harness').classes('text-2xl font-semibold')
        ui.label(
            'Offline harness with synthetic model metadata. Use it to verify new '
            'columns (e.g., tool access icons) before wiring them to OpenRouter.'
        ).classes('text-sm text-gray-500')

        state.code_selector = ModelSelector(
            initial_value='anthropic/claude-3.7-sonnet',
            vision_only=False,
            label='Code models',
            on_change=_on_change,
        )

        state.summary_label = ui.label('').classes('whitespace-pre-line text-sm')
        _update_summary(state)

        ui.separator()
        ui.label('Mock capability matrix').classes('text-base font-semibold')
        _build_table()

    def _expand_code_selector() -> None:
        try:
            state.code_selector._expander.set_value(True)  # type: ignore[attr-defined]
        except Exception:
            pass

    ui.timer(0.3, _expand_code_selector, once=True)


# ---------------- Capture helpers ---------------- #


def _wait_for_server(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    url = f'http://127.0.0.1:{port}/'
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.1)
    raise RuntimeError('Model selector harness did not start in time')


def _capture_screenshot(port: int, output_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    url = f'http://127.0.0.1:{port}/'
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 800})
        page.goto(url, wait_until='networkidle')
        page.wait_for_timeout(1000)
        page.screenshot(path=str(output_path), full_page=True)
        browser.close()


# ---------------- Entrypoint ---------------- #


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Model selector NiceGUI harness')
    parser.add_argument('--serve', action='store_true', help='Start the harness and keep it running until interrupted')
    parser.add_argument('--capture', action='store_true', help='Capture a screenshot artifact and exit')
    parser.add_argument('--port', type=int, default=8060, help='Port to host the harness on')
    parser.add_argument('--artifact-dir', type=Path, default=Path('artifacts/experiments/model_selector'), help='Directory for screenshot artifacts')
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.serve and not args.capture:
        args.serve = True

    # Build the UI tree once before launching the server.
    state = HarnessState()
    _build_app(state)

    if args.capture:
        args.artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = args.artifact_dir / f'model_selector_{int(time.time())}.png'

        def _run_server() -> None:
            ui.run(port=args.port, title='Model Selector Harness', reload=False, show=False, native=False)

        thread = threading.Thread(target=_run_server, daemon=True)
        thread.start()
        _wait_for_server(args.port)
        _capture_screenshot(args.port, artifact_path)
        app.shutdown()
        thread.join(timeout=3)
        print(f'Captured artifact at {artifact_path}')
        return

    ui.run(port=args.port, title='Model Selector Harness', reload=False, native=False)


if __name__ == '__main__':
    main()
