from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
import contextlib
import time
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_root_cwd() -> Path:
    root = project_root()
    os.chdir(root)
    return root


def inject_src() -> None:
    p = project_root() / "src"
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


async def _wait_http_ready(url: str, timeout_s: float = 120.0) -> None:
    import httpx
    start = time.monotonic()
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            try:
                r = await client.get(url)
                if r.status_code < 500:
                    return
            except Exception:
                pass
            if time.monotonic() - start > timeout_s:
                raise TimeoutError(f"Server at {url} not ready after {timeout_s}s")
            await asyncio.sleep(0.5)


async def test_ui_reasoning_dialog_twice() -> tuple[bool, str]:
    ensure_root_cwd()
    inject_src()

    import prefs
    import config as app_config
    import importlib
    try:
        from src.interfaces import IterationMode
    except Exception:
        from interfaces import IterationMode  # type: ignore

    # Route model params to a temp file and enable reasoning
    tmp = tempfile.NamedTemporaryFile(prefix="model_params_", suffix=".json", delete=False)
    os.environ["MODEL_PARAMS_PATH"] = tmp.name
    mp = importlib.import_module('model_params')

    # Configure defaults for the app via prefs
    cfg = app_config.get_config()
    code_model = 'meta-llama/llama-3.2-90b-vision-instruct'
    vision_model = cfg.vision_model
    prefs.set('model.code', code_model)
    prefs.set('model.vision', vision_model)
    prefs.set('template.code', cfg.code_template)
    prefs.set('template.vision', cfg.vision_template)
    prefs.set('iteration.mode', IterationMode.DIRECT_TO_CODER.value)

    # Enable reasoning for DeepSeek
    mp.set_params(code_model, {
        'include_reasoning': 'true',
        'reasoning': json.dumps({'effort': 'medium'}),
        'max_tokens': '256',
        'temperature': '0.1',
    })

    # Launch app server as subprocess
    import subprocess
    env = os.environ.copy()
    env["APP_USE_MOCK_AI"] = "ui-reasoning"
    proc = subprocess.Popen([sys.executable, '-m', 'src.main'], cwd=str(project_root()), env=env)
    try:
        await _wait_http_ready('http://localhost:8055')

        # Drive UI with Playwright
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.goto('http://localhost:8055', wait_until='domcontentloaded')

            # Fill overall goal and start
            goal_input = page.locator('textarea[aria-label="Overall goal"]').first
            await goal_input.fill('Create a minimal HTML with a heading and a short line of text.')
            await page.get_by_text('Start').click()

            # Wait for first iteration output card to render and reasoning icon to appear
            # Brain icon uses material icon name 'psychology'
            # Allow generous timeout due to model latency
            await page.wait_for_selector('i.material-icons:has-text("psychology")', timeout=180000)

            # Click brain icon and verify dialog title, twice
            icon = page.locator('i.material-icons', has_text='psychology').first
            for i in range(2):
                await icon.click()
                await page.wait_for_selector('.q-dialog .q-card >> text=Model Reasoning', timeout=10000)
                # Close dialog via close button (material icon 'close')
                await page.locator('.q-dialog .q-card i.material-icons', has_text='close').click()
                # Ensure dialog closed
                await page.wait_for_selector('.q-dialog', state='hidden', timeout=10000)

            await browser.close()
        return True, 'Reasoning dialog opened and closed twice successfully'
    finally:
        with contextlib.suppress(Exception):
            proc.send_signal(signal.SIGINT)
        with contextlib.suppress(Exception):
            proc.kill()


async def main() -> int:
    ok, info = await test_ui_reasoning_dialog_twice()
    print(f"[ {'OK' if ok else 'FAIL'} ] UI reasoning dialog: {info}")
    return 0 if ok else 1


if __name__ == '__main__':
    import contextlib
    raise SystemExit(asyncio.run(main()))
