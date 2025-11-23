# src/services.py
from __future__ import annotations

import asyncio
import base64
import hashlib
import mimetypes
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence

from .interfaces import AICodeService, BrowserService, VisionService
from .image_downscale import load_scaled_image_bytes
from . import context_data, op_status
from .prompt_builder import PromptPayload
from .feedback_presets import FeedbackPreset
from .chrome_devtools_service import ChromeDevToolsService, bind_chrome_devtools_agent


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
            try:
                await service.aclose()
            except Exception:
                pass
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
        log_entries: List[Dict[str, str]] = []

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
            log_entries = await service.get_console_messages_mcp()
        finally:
            op_status.clear_phase(worker)
            try:
                await service.aclose()
            except Exception:
                pass
        log_strings = _format_console_entries(log_entries)
        return screenshot_paths, log_strings, screenshot_labels




# ---- OpenRouter-backed services ----

class OpenRouterAICodeService(AICodeService):
    async def generate_html(
        self,
        prompt: PromptPayload | Sequence[dict] | str,
        model: str,
        worker: str = "main",
        *,
        template_context: Dict[str, Any] | None = None,
    ) -> tuple[str, str | None, dict | None]:
        # Defer import to avoid requiring env when using stubs/tests
        from . import or_client

        # Minimal call: the controller provides a full prompt with context
        # Structured phase: "Coding|<model>"
        worker_name = worker or model or "agent"
        operation_id = uuid.uuid4().hex
        started_monotonic = time.monotonic()
        started_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        devtools_agent_id = f"{worker_name}:{operation_id}"
        templ_ctx = template_context or {}
        ctx_token = context_data.reset_context({
            "tool_call_count": 0,
            "worker_id": worker_name,
            "operation_id": operation_id,
            "model_slug": model,
            "session_started_at_monotonic": started_monotonic,
            "session_started_at_iso": started_iso,
            "devtools_agent_id": devtools_agent_id,
            "vision_template": str(templ_ctx.get("vision_template") or ""),
            "vision_template_context": dict(templ_ctx.get("template_vars") or {}),
            "active_vision_model": str(templ_ctx.get("vision_model") or ""),
        })
        op_status.set_phase(worker, f"Coding|{model}")

        if isinstance(prompt, PromptPayload):
            messages = prompt.messages
        elif isinstance(prompt, list):
            messages = [dict(m) for m in prompt]
        else:
            messages = [{"role": "user", "content": prompt}]

        try:
            async with bind_chrome_devtools_agent(devtools_agent_id):
                content, meta = await or_client.chat_with_meta(
                    messages=messages,
                    model=model,
                )
            reasoning_result = (meta.get("reasoning") or None)

            # Add prompt messages (with any image attachments) and assistant response
            if meta is None:
                meta = {}
            # Preserve the full conversation (including tool calls) returned by chat_with_meta.
            # If missing or malformed, fall back to the prompt messages but surface a warning to the user.
            if "messages" not in meta:
                try:
                    op_status.enqueue_notification(
                        f"{model}: provider returned no message history; showing prompt messages only",
                        color="warning",
                        timeout=6000,
                        close_button=True,
                    )
                except Exception:
                    print(f"[warn] {model}: provider returned no message history; showing prompt messages only")
                meta["messages"] = list(messages)
            else:
                try:
                    meta["messages"] = list(meta.get("messages") or [])
                except Exception:
                    try:
                        op_status.enqueue_notification(
                            f"{model}: message history malformed; showing prompt messages only",
                            color="warning",
                            timeout=6000,
                            close_button=True,
                        )
                    except Exception:
                        print(f"[warn] {model}: message history malformed; showing prompt messages only")
                    meta["messages"] = list(messages)
            meta["assistant_response"] = content or ""

            op_status.clear_phase(worker)
            return (content or "", reasoning_result, meta)
        finally:
            context_data.restore_context(ctx_token)


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
        worker_name = worker or "vision"
        operation_id = uuid.uuid4().hex
        started_monotonic = time.monotonic()
        started_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        ctx_token = context_data.reset_context({
            "tool_call_count": 0,
            "worker_id": worker_name,
            "operation_id": operation_id,
            "model_slug": model,
            "session_started_at_monotonic": started_monotonic,
            "session_started_at_iso": started_iso,
        })
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
            context_data.restore_context(ctx_token)
            return ""
        try:
            reply = await or_client.chat(
                messages=[{"role": "user", "content": parts}],
                model=model,
                temperature=0,
                allow_tools=False,
            )
            op_status.clear_phase(worker)
            return reply or ""
        finally:
            context_data.restore_context(ctx_token)


def detect_mime_type(filename: str, default: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or default


def encode_file_to_data_url(file_bytes: bytes, mime_type: str | None = None) -> str:
    safe_mime = (mime_type or "").strip() or "application/octet-stream"
    payload = base64.b64encode(file_bytes or b"").decode("ascii") if file_bytes else ""
    return f"data:{safe_mime};base64,{payload}"
