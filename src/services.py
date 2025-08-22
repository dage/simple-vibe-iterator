# src/services.py
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import List, Tuple

from .interfaces import AICodeService, BrowserService, VisionService
from . import or_client
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
        prompt = (
            "You are evaluating a rendered HTML page screenshot. "
            "Summarize what you see briefly and suggest 2 improvements. "
            "Also consider these console logs: " + "\n".join(console_logs[:10])
        )
        try:
            reply = await or_client.vision_single(prompt=prompt, image=Path(screenshot_path))
            return reply
        except Exception as exc:
            return f"Vision analysis failed: {exc}"


class OpenRouterAICodeService(AICodeService):
    async def generate_html(self, prompt: str) -> str:
        system = (
            "You generate complete standalone HTML documents. "
            "Return ONLY valid HTML (starting with <!DOCTYPE html>), no explanations."
        )
        user = (
            "Create a minimal, clean HTML page based on this description. "
            "Use inline CSS only. Add a visible title and a main section.\n\n"
            f"Description: {prompt}"
        )
        try:
            convo = or_client.Conversation()
            convo.set_system(system)
            reply = await convo.ask(user, temperature=0.2)
        except Exception as exc:
            return (
                "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>Error</title></head>"
                f"<body><h1>Code generation failed</h1><pre>{exc}</pre></body></html>"
            )

        text = (reply or "").strip()
        if "<!DOCTYPE html" not in text[:200].lower():
            # Fallback wrap if model returned fragment
            return (
                "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>Generated Page</title></head>"
                f"<body>{text}</body></html>"
            )
        return text


