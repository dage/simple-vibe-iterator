# src/services.py
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import List

from .interfaces import AICodeService, BrowserService, VisionService
from .playwright_browser import capture_html, parse_viewport


class PlaywrightBrowserService(BrowserService):
    def __init__(self, out_dir: Path | None = None, viewport: str | None = None) -> None:
        self._out_dir = Path(out_dir or "artifacts").resolve()
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._viewport = parse_viewport(viewport)

    async def render_and_capture(self, html_code: str) -> tuple[str, List[str]]:
        # Persist HTML to temp file for Playwright to open via file://
        digest = hashlib.sha1(html_code.encode("utf-8")).hexdigest()[:12]
        html_path = self._out_dir / f"page_{digest}.html"
        png_path = self._out_dir / f"page_{digest}.png"
        html_path.write_text(html_code, encoding="utf-8")
        logs = await asyncio.to_thread(capture_html, html_path, png_path, self._viewport)
        # Flatten console texts for now to meet interface
        flat_logs: List[str] = []
        for entry in logs:
            t = str(entry.get("type") or "log")
            msg = str(entry.get("text") or "")
            flat_logs.append(f"[{t}] {msg}")
        return (str(png_path), flat_logs)


# ---- OpenRouter-backed services ----

class OpenRouterAICodeService(AICodeService):
    async def generate_html(self, prompt: str) -> str:
        # Defer import to avoid requiring env when using stubs/tests
        from . import or_client

        # Minimal call: the controller provides a full prompt with context
        reply = await or_client.chat(
            messages=[{"role": "user", "content": prompt}],
        )
        return reply or ""


class OpenRouterVisionService(VisionService):
    async def analyze_screenshot(self, screenshot_path: str, console_logs: List[str]) -> str:
        from . import or_client

        # Build a concise prompt including a short sample of console logs
        preview_logs: List[str] = console_logs[:20] if console_logs else []
        logs_text = "\n".join(preview_logs)
        prompt = (
            "Analyze this rendered page screenshot and the console log excerpt.\n"
            "- Identify visual issues or layout problems.\n"
            "- Note any console errors/warnings impacting rendering.\n"
            "- Provide concrete, concise suggestions to improve the HTML/CSS/JS.\n\n"
        )
        if logs_text:
            prompt += f"Console log excerpt (first {len(preview_logs)} lines):\n{logs_text}\n\n"

        reply = await or_client.vision_single(
            prompt=prompt,
            image=screenshot_path,
            temperature=0,
        )
        return reply or ""


