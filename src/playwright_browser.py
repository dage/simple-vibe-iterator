# src/playwright_browser.py
from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Any, Tuple, Sequence
from playwright.sync_api import sync_playwright
import time


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


def capture_html(
    html_path: Path,
    out_pngs: Sequence[Path],
    viewport: Tuple[int, int] = (1280, 720),
    interval_seconds: float = 1.0,
) -> List[Dict[str, Any]]:
    if not out_pngs:
        raise ValueError("At least one output path is required for capture_html")
    for path in out_pngs:
        path.parent.mkdir(parents=True, exist_ok=True)
    url = html_path.resolve().as_uri()
    logs: List[Dict[str, Any]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})

            # Track last time we saw an event to implement a short "quiet period" wait
            last_event_ts = time.monotonic()

            def _bump_event_ts() -> None:
                nonlocal last_event_ts
                last_event_ts = time.monotonic()

            def _on_console(msg):
                try:
                    loc = msg.location or {}
                    entry = {
                        "type": getattr(msg, "type", None) if not callable(getattr(msg, "type", None)) else msg.type(),
                        "text": getattr(msg, "text", None) if not callable(getattr(msg, "text", None)) else msg.text(),
                        "url": loc.get("url"),
                        "line": loc.get("lineNumber"),
                        "column": loc.get("columnNumber"),
                    }
                    logs.append(entry)
                    _bump_event_ts()
                except Exception:
                    pass

            def _on_page_error(err):
                try:
                    entry = {
                        "type": "pageerror",
                        "text": str(err),
                        "url": None,
                        "line": None,
                        "column": None,
                    }
                    logs.append(entry)
                    _bump_event_ts()
                except Exception:
                    pass

            def _on_request_failed(request):
                try:
                    # request.failure() returns { 'errorText': ... }
                    failure = request.failure() if callable(getattr(request, "failure", None)) else None
                    error_text = (failure or {}).get("errorText") if isinstance(failure, dict) else None
                    method = request.method if not callable(getattr(request, "method", None)) else request.method()
                    url_s = request.url if not callable(getattr(request, "url", None)) else request.url()
                    text = f"{method} {url_s} - {error_text or 'failed'}"
                    entry = {
                        "type": "requestfailed",
                        "text": text,
                        "url": url_s,
                        "line": None,
                        "column": None,
                    }
                    logs.append(entry)
                    _bump_event_ts()
                except Exception:
                    pass

            # Attach listeners BEFORE navigation so we don't miss early errors
            page.on("console", _on_console)
            page.on("pageerror", _on_page_error)
            page.on("requestfailed", _on_request_failed)

            page.goto(url, wait_until="load")

            # Prefer smarter waits over a fixed sleep:
            # 1) Wait briefly for network to go idle (if applicable)
            try:
                page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:
                pass

            # 2) Wait for a short quiet period without new events (up to a small cap)
            start_ts = time.monotonic()
            QUIET_S = 0.3
            MAX_WAIT_S = 2.5
            while True:
                now = time.monotonic()
                if (now - last_event_ts) >= QUIET_S:
                    break
                if (now - start_ts) >= MAX_WAIT_S:
                    break
                time.sleep(0.05)
            wait_ms = max(0, int(interval_seconds * 1000)) if interval_seconds else 0
            for idx, path in enumerate(out_pngs):
                if idx > 0 and wait_ms > 0:
                    try:
                        page.wait_for_timeout(wait_ms)
                    except Exception:
                        time.sleep(interval_seconds)
                page.screenshot(path=str(path), full_page=False)
        finally:
            browser.close()
    return logs

