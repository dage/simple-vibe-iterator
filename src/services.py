# src/services.py
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import List

from .interfaces import AICodeService, BrowserService, VisionService
from .playwright_browser import capture_html, parse_viewport


class StubAICodeService(AICodeService):
    async def generate_html(self, prompt: str) -> str:
        await asyncio.sleep(0.1)
        safe = (prompt or "").strip()[:200]
        return (
            "<!DOCTYPE html>\n"
            "<html><head><meta charset=\"utf-8\"><title>Generated Page</title>\n"
            "<style>body{font-family:sans-serif;padding:24px} .box{padding:16px;border:1px solid #ccc;border-radius:8px}</style>\n"
            "</head><body>\n"
            f"<h1>Generated from prompt</h1><div class=\"box\"><pre>{safe}</pre></div>\n"
            "<script>console.log('Page loaded');</script>\n"
            "</body></html>"
        )


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


class OpenRouterVisionService(VisionService):
    async def analyze_screenshot(self, screenshot_path: str, console_logs: List[str]) -> str:
        # Stubbed vision: summarize basic info without calling an LLM
        await asyncio.sleep(0.05)
        name = Path(screenshot_path).name
        summary = [
            f"Vision stub", 
            f"Screenshot: {name}",
            f"Console entries: {len(console_logs)}",
        ]
        # Include first few console lines for context
        head = console_logs[:5]
        if head:
            summary.append("\nSample console logs:")
            summary.extend(head)
        return "\n".join(summary)


class StubVisionService(VisionService):
    async def analyze_screenshot(self, screenshot_path: str, console_logs: List[str]) -> str:
        # Same behavior as OpenRouterVisionService placeholder but clearly marked as stub
        await asyncio.sleep(0.05)
        name = Path(screenshot_path).name
        summary = [
            f"Vision stub", 
            f"Screenshot: {name}",
            f"Console entries: {len(console_logs)}",
        ]
        head = console_logs[:5]
        if head:
            summary.append("\nSample console logs:")
            summary.extend(head)
        return "\n".join(summary)


