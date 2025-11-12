# src/playwright_browser.py
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, List, Dict, Any, Tuple, Sequence
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
    with _page_session(html_path, viewport) as (page, logs):
        wait_ms = max(0, int(interval_seconds * 1000)) if interval_seconds else 0
        for idx, path in enumerate(out_pngs):
            if idx > 0 and wait_ms > 0:
                try:
                    page.wait_for_timeout(wait_ms)
                except Exception:
                    time.sleep(interval_seconds)
            page.screenshot(path=str(path), full_page=False)
        return logs


def run_feedback_sequence(
    html_path: Path,
    plan: Sequence[Dict[str, Any]],
    viewport: Tuple[int, int] = (1280, 720),
    on_action: Callable[[str], None] | None = None,
) -> List[Dict[str, Any]]:
    with _page_session(html_path, viewport) as (page, logs):
        keyboard = page.keyboard
        held_keys: List[Dict[str, object]] = []

        def _release_due(force: bool = False) -> None:
            now = time.monotonic()
            remaining: List[Dict[str, object]] = []
            for entry in held_keys:
                release_at = float(entry["release_at"])
                key = str(entry["key"])
                if force or now >= release_at:
                    try:
                        keyboard.up(key)
                    except Exception:
                        pass
                else:
                    remaining.append(entry)
            held_keys[:] = remaining

        def _next_release_delta() -> float | None:
            if not held_keys:
                return None
            now = time.monotonic()
            return max(0.0, min(float(entry["release_at"]) - now for entry in held_keys))

        def _notify(desc: str) -> None:
            if on_action is None:
                return
            try:
                on_action(desc)
            except Exception:
                pass

        def _next_release_delta() -> float | None:
            if not held_keys:
                return None
            now = time.monotonic()
            return max(0.0, min(float(entry["release_at"]) - now for entry in held_keys))

        def _wait_seconds(seconds: float) -> None:
            remaining = max(0.0, seconds)
            MIN_SLICE = 0.01
            while remaining > 0:
                _release_due()
                now = time.monotonic()
                next_release = min((float(entry["release_at"]) for entry in held_keys), default=None)
                slice_len = remaining
                if next_release is not None:
                    delta = max(0.0, next_release - now)
                    if delta < slice_len and delta > 0:
                        slice_len = delta
                slice_len = max(MIN_SLICE, slice_len)
                slice_len = min(slice_len, remaining)
                try:
                    page.wait_for_timeout(int(slice_len * 1000))
                except Exception:
                    time.sleep(slice_len)
                remaining -= slice_len
            _release_due()

        for step in plan or []:
            kind = str(step.get("kind") or "").lower()
            if kind == "wait":
                seconds = max(0.0, float(step.get("seconds") or 0.0))
                if seconds <= 0:
                    _release_due()
                    continue
                _notify(f"wait {seconds:.1f}s")
                _wait_seconds(seconds)
            elif kind == "keypress":
                key = str(step.get("key") or "").strip()
                if not key:
                    continue
                duration_ms = max(0, int(step.get("duration_ms") or 0))
                delta = _next_release_delta()
                if delta is not None and delta > 0:
                    _wait_seconds(delta)
                _notify(f"keypress {key}")
                try:
                    keyboard.down(key)
                except Exception:
                    continue
                release_at = time.monotonic() + (duration_ms / 1000.0 if duration_ms > 0 else 0.0)
                if duration_ms <= 0:
                    try:
                        keyboard.up(key)
                    except Exception:
                        pass
                else:
                    held_keys.append({"key": key, "release_at": release_at})
            elif kind == "screenshot":
                path = step.get("path")
                if not path:
                    continue
                label = str(step.get("label") or "").strip()
                _notify(label or "screenshot")
                path_obj = Path(path)
                path_obj.parent.mkdir(parents=True, exist_ok=True)
                full_page = bool(step.get("full_page", False))
                page.screenshot(path=str(path_obj), full_page=full_page)
                _release_due()
            else:
                _release_due()

        while held_keys:
            _wait_seconds(max(0.0, min(float(entry["release_at"]) for entry in held_keys) - time.monotonic()))
        _release_due(force=True)
        return logs


@contextmanager
def _page_session(html_path: Path, viewport: Tuple[int, int]):
    url = html_path.resolve().as_uri()
    logs: List[Dict[str, Any]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})

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

            page.on("console", _on_console)
            page.on("pageerror", _on_page_error)
            page.on("requestfailed", _on_request_failed)

            page.goto(url, wait_until="load")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            try:
                page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:
                pass

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

            yield page, logs
        finally:
            browser.close()
