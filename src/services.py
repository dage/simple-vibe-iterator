# src/services.py
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import List

from .interfaces import AICodeService, BrowserService, VisionService
from . import op_status
from .playwright_browser import capture_html, parse_viewport


class PlaywrightBrowserService(BrowserService):
    def __init__(self, out_dir: Path | None = None, viewport: str | None = None) -> None:
        self._out_dir = Path(out_dir or "artifacts").resolve()
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._viewport = parse_viewport(viewport)
        self._lock = asyncio.Lock()
    async def render_and_capture(self, html_code: str, worker: str = "main") -> tuple[str, List[str]]:
        # Persist HTML to temp file for Playwright to open via file://
        digest = hashlib.sha1(html_code.encode("utf-8")).hexdigest()[:12]
        html_path = self._out_dir / f"page_{digest}.html"
        png_path = self._out_dir / f"page_{digest}.png"
        html_path.write_text(html_code, encoding="utf-8")
        op_status.set_phase(worker, "Playwright: Capture screenshot")
        async with self._lock:
            logs = await asyncio.to_thread(capture_html, html_path, png_path, self._viewport)
        # Flatten console texts for now to meet interface
        flat_logs: List[str] = []
        for entry in logs:
            t = str(entry.get("type") or "log")
            msg = str(entry.get("text") or "")
            flat_logs.append(f"[{t}] {msg}")
        op_status.clear_phase(worker)
        return (str(png_path), flat_logs)


# ---- OpenRouter-backed services ----

class OpenRouterAICodeService(AICodeService):
    async def generate_html(self, prompt: str, model: str, worker: str = "main") -> tuple[str, str | None, dict | None]:
        # Defer import to avoid requiring env when using stubs/tests
        from . import or_client

        # Minimal call: the controller provides a full prompt with context
        op_status.set_phase(worker, f"Code: {model}")
        content, meta = await or_client.chat_with_meta(
            messages=[{"role": "user", "content": prompt}],
            model=model,
        )
        op_status.clear_phase(worker)
        return (content or "", (meta.get("reasoning") or None), meta)


class OpenRouterVisionService(VisionService):
    async def analyze_screenshot(
        self,
        prompt: str,
        screenshot_path: str,
        console_logs: List[str],
        model: str,
        worker: str = "main",
    ) -> str:
        from . import or_client

        op_status.set_phase(worker, f"Vision: {model}")
        reply = await or_client.vision_single(
            prompt=prompt,
            image=screenshot_path,
            model=model,
            temperature=0,
        )
        op_status.clear_phase(worker)
        return reply or ""
