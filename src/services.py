# src/services.py
from __future__ import annotations

import asyncio
import base64
import hashlib
import uuid
from pathlib import Path
from typing import Dict, List, Sequence

from .interfaces import AICodeService, BrowserService, VisionService
from .image_downscale import load_scaled_image_bytes
from . import op_status
from .prompt_builder import PromptPayload
from .feedback_presets import FeedbackPreset
from .chrome_devtools_service import ChromeDevToolsService


def _write_html_artifact(target: Path, html_code: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html_code, encoding="utf-8")


def _save_data_url(data_url: str, target: Path) -> None:
    if not data_url or "," not in data_url:
        return
    _, payload = data_url.split(",", 1)
    target.write_bytes(base64.b64decode(payload))


def _format_console_entries(entries: List[Dict[str, str]]) -> List[str]:
    flat: List[str] = []
    for entry in entries or []:
        level = str(entry.get("level") or "log")
        message = str(entry.get("message") or "")
        flat.append(f"[{level}] {message}")
    return flat


class DevToolsBrowserService(BrowserService):
    """Default browser implementation backed by Chrome DevTools MCP."""

    def __init__(self, out_dir: Path | None = None) -> None:
        self._out_dir = Path(out_dir or "artifacts").resolve()
        self._out_dir.mkdir(parents=True, exist_ok=True)

    async def render_and_capture(
        self,
        html_code: str,
        worker: str = "main",
        *,
        capture_count: int = 1,
        interval_seconds: float = 1.0,
    ) -> tuple[List[str], List[str]]:
        service = ChromeDevToolsService()
        if not service.enabled:
            raise RuntimeError("Chrome DevTools MCP is not configured; cannot capture screenshots.")

        count = max(1, int(capture_count or 1))
        try:
            interval = float(interval_seconds)
        except Exception:
            interval = 1.0
        if interval <= 0:
            interval = 1.0

        digest = hashlib.sha1(html_code.encode("utf-8")).hexdigest()[:12]
        token = uuid.uuid4().hex[:8]
        base_html = self._out_dir / f"page_{digest}_{token}.html"
        _write_html_artifact(base_html, html_code)

        screenshot_paths: List[str] = []
        op_status.set_phase(worker, "Screenshot|DevTools")
        try:
            await service.load_html_mcp(html_code)
            for idx in range(count):
                if idx > 0:
                    await asyncio.sleep(interval)
                data_url = await service.take_screenshot_mcp()
                if not data_url:
                    break
                shot_path = self._out_dir / f"page_{digest}_{token}_{idx}.png"
                _save_data_url(data_url, shot_path)
                html_copy = shot_path.with_suffix(".html")
                if not html_copy.exists():
                    _write_html_artifact(html_copy, html_code)
                screenshot_paths.append(str(shot_path))
            log_entries = await service.get_console_messages_mcp()
            log_strings = _format_console_entries(log_entries)
        finally:
            op_status.clear_phase(worker)
        return screenshot_paths, log_strings

    async def run_feedback_preset(
        self,
        html_code: str,
        preset: FeedbackPreset,
        worker: str = "main",
    ) -> tuple[List[str], List[str], List[str]]:
        if not preset.actions:
            return ([], [], [])
        service = ChromeDevToolsService()
        if not service.enabled:
            raise RuntimeError("Chrome DevTools MCP is not configured; cannot run feedback preset.")

        digest = hashlib.sha1(html_code.encode("utf-8")).hexdigest()[:12]
        token = uuid.uuid4().hex[:8]
        base_html = self._out_dir / f"preset_{digest}_{token}.html"
        _write_html_artifact(base_html, html_code)

        screenshot_paths: List[str] = []
        screenshot_labels: List[str] = []
        shot_idx = 0

        op_status.set_phase(worker, f"Feedback|{preset.label or preset.id}")
        try:
            await service.load_html_mcp(html_code)
            for action in preset.actions:
                kind = (action.kind or "").lower()
                if kind == "wait":
                    await asyncio.sleep(max(0.0, float(action.seconds)))
                elif kind == "keypress":
                    await service.press_key_mcp(action.key or "", max(0, int(action.duration_ms)))
                elif kind == "screenshot":
                    data_url = await service.take_screenshot_mcp()
                    if not data_url:
                        continue
                    filename = f"preset_{preset.id}_{token}_{shot_idx}.png"
                    shot_idx += 1
                    path = self._out_dir / filename
                    _save_data_url(data_url, path)
                    html_copy = path.with_suffix(".html")
                    if not html_copy.exists():
                        _write_html_artifact(html_copy, html_code)
                    label = action.label or f"shot-{shot_idx}"
                    screenshot_paths.append(str(path))
                    screenshot_labels.append(label)
        finally:
            op_status.clear_phase(worker)

        log_entries = await service.get_console_messages_mcp()
        log_strings = _format_console_entries(log_entries)
        return screenshot_paths, log_strings, screenshot_labels




# ---- OpenRouter-backed services ----

class OpenRouterAICodeService(AICodeService):
    async def generate_html(
        self,
        prompt: PromptPayload | Sequence[dict] | str,
        model: str,
        worker: str = "main",
    ) -> tuple[str, str | None, dict | None]:
        # Defer import to avoid requiring env when using stubs/tests
        from . import or_client

        # Minimal call: the controller provides a full prompt with context
        # Structured phase: "Coding|<model>"
        op_status.set_phase(worker, f"Coding|{model}")

        if isinstance(prompt, PromptPayload):
            messages = prompt.messages
        elif isinstance(prompt, list):
            messages = [dict(m) for m in prompt]
        else:
            messages = [{"role": "user", "content": prompt}]

        content, meta = await or_client.chat_with_meta(
            messages=messages,
            model=model,
        )
        reasoning_result = (meta.get("reasoning") or None)

        # Add prompt messages (with any image attachments) and assistant response
        if meta is None:
            meta = {}
        meta["messages"] = messages
        meta["assistant_response"] = content or ""

        op_status.clear_phase(worker)
        return (content or "", reasoning_result, meta)


class OpenRouterVisionService(VisionService):
    async def analyze_screenshot(
        self,
        prompt: str,
        screenshot_paths: Sequence[str],
        console_logs: List[str],
        model: str,
        worker: str = "main",
    ) -> str:
        from . import or_client

        # Structured phase: "Vision|<model>"
        op_status.set_phase(worker, f"Vision|{model}")
        parts = [{"type": "text", "text": prompt}]
        for path in screenshot_paths:
            if not (path or "").strip():
                continue
            scaled_bytes = load_scaled_image_bytes(path)
            data_source = scaled_bytes if scaled_bytes is not None else path
            try:
                data_url = or_client.encode_image_to_data_url(data_source)
            except Exception:
                continue
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
        if len(parts) == 1:
            op_status.clear_phase(worker)
            return ""
        reply = await or_client.chat(
            messages=[{"role": "user", "content": parts}],
            model=model,
            temperature=0,
        )
        op_status.clear_phase(worker)
        return reply or ""
