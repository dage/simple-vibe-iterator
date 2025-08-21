# src/playwright_browser.py
from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Any, Tuple
from playwright.sync_api import sync_playwright


def parse_viewport(s: str | None) -> tuple[int, int]:
    try:
        if not s:
            return (1280, 720)
        parts = str(s).lower().replace("x", " ").split()
        w, h = int(parts[0]), int(parts[1])
        if w <= 0 or h <= 0:
            return (1280, 720)
        return (w, h)
    except Exception:
        return (1280, 720)


def capture_html(html_path: Path, out_png: Path, viewport: Tuple[int, int] = (1280, 720)) -> List[Dict[str, Any]]:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    url = html_path.resolve().as_uri()
    logs: List[Dict[str, Any]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})

            def _on_console(msg):
                try:
                    loc = msg.location or {}
                    logs.append({
                        "type": getattr(msg, "type", None) if not callable(getattr(msg, "type", None)) else msg.type(),
                        "text": getattr(msg, "text", None) if not callable(getattr(msg, "text", None)) else msg.text(),
                        "url": loc.get("url"),
                        "line": loc.get("lineNumber"),
                        "column": loc.get("columnNumber"),
                    })
                except Exception:
                    pass

            page.on("console", _on_console)
            page.goto(url, wait_until="load")
            page.screenshot(path=str(out_png), full_page=False)
        finally:
            browser.close()
    return logs


