# src/services.py
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path
from typing import Dict, List, Sequence

from .interfaces import AICodeService, BrowserService, VisionService
from . import op_status
from .playwright_browser import capture_html, parse_viewport, run_feedback_sequence
from .prompt_builder import PromptPayload
from .feedback_presets import FeedbackPreset


class PlaywrightBrowserService(BrowserService):
    def __init__(self, out_dir: Path | None = None, viewport: str | None = None) -> None:
        self._out_dir = Path(out_dir or "artifacts").resolve()
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._viewport = parse_viewport(viewport)
        self._lock = asyncio.Lock()
    async def render_and_capture(
        self,
        html_code: str,
        worker: str = "main",
        *,
        capture_count: int = 1,
        interval_seconds: float = 1.0,
    ) -> tuple[List[str], List[str]]:
        try:
            count = int(capture_count)
        except Exception:
            count = 1
        count = max(1, count)

        try:
            interval = float(interval_seconds)
        except Exception:
            interval = 1.0
        if interval <= 0:
            interval = 1.0

        digest = hashlib.sha1(html_code.encode("utf-8")).hexdigest()[:12]
        token = uuid.uuid4().hex[:8]
        base_html = self._out_dir / f"page_{digest}_{token}.html"
        base_html.write_text(html_code, encoding="utf-8")
        png_paths = [self._out_dir / f"page_{digest}_{token}_{idx}.png" for idx in range(count)]
        # Structured phase: "Screenshot|Playwright: capture"
        op_status.set_phase(worker, "Screenshot|Playwright")
        async with self._lock:
            logs = await asyncio.to_thread(
                capture_html,
                base_html,
                png_paths,
                self._viewport,
                interval,
            )
        # Flatten console texts for now to meet interface
        flat_logs: List[str] = []
        for entry in logs:
            t = str(entry.get("type") or "log")
            msg = str(entry.get("text") or "")
            flat_logs.append(f"[{t}] {msg}")
        op_status.clear_phase(worker)
        for png in png_paths:
            html_copy = png.with_suffix('.html')
            try:
                if not html_copy.exists():
                    html_copy.write_text(html_code, encoding="utf-8")
            except Exception:
                pass
        return ([str(p) for p in png_paths], flat_logs)

    async def run_feedback_preset(
        self,
        html_code: str,
        preset: FeedbackPreset,
        worker: str = "main",
    ) -> tuple[List[str], List[str], List[str]]:
        if not preset.actions:
            return ([], [], [])
        digest = hashlib.sha1(html_code.encode("utf-8")).hexdigest()[:12]
        token = uuid.uuid4().hex[:8]
        base_html = self._out_dir / f"preset_{digest}_{token}.html"
        base_html.write_text(html_code, encoding="utf-8")

        plan: List[Dict[str, object]] = []
        screenshot_paths: List[Path] = []
        screenshot_labels: List[str] = []
        shot_idx = 0
        for action in preset.actions:
            kind = action.kind.lower()
            if kind == "wait":
                plan.append({"kind": "wait", "seconds": max(0.0, float(action.seconds))})
            elif kind == "keypress":
                plan.append(
                    {
                        "kind": "keypress",
                        "key": action.key,
                        "duration_ms": max(0, int(action.duration_ms)),
                    }
                )
            elif kind == "screenshot":
                filename = f"preset_{preset.id}_{token}_{shot_idx}.png"
                shot_idx += 1
                path = self._out_dir / filename
                label = action.label or f"shot-{shot_idx}"
                plan.append(
                    {
                        "kind": "screenshot",
                        "path": path,
                        "full_page": bool(action.full_page),
                        "label": label,
                    }
                )
                screenshot_paths.append(path)
                screenshot_labels.append(label)

        op_status.set_phase(worker, f"Feedback|{preset.label or preset.id}")

        def _update_action(desc: str) -> None:
            text = desc or preset.label or preset.id
            op_status.set_phase(worker, f"Feedback|{text}")

        async with self._lock:
            logs = await asyncio.to_thread(
                run_feedback_sequence,
                base_html,
                plan,
                self._viewport,
                _update_action,
            )
        flat_logs: List[str] = []
        for entry in logs:
            t = str(entry.get("type") or "log")
            msg = str(entry.get("text") or "")
            flat_logs.append(f"[{t}] {msg}")
        op_status.clear_phase(worker)

        for png in screenshot_paths:
            html_copy = png.with_suffix(".html")
            try:
                if not html_copy.exists():
                    html_copy.write_text(html_code, encoding="utf-8")
            except Exception:
                pass
        return ([str(p) for p in screenshot_paths], flat_logs, list(screenshot_labels))


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

        if os.getenv("APP_USE_MOCK_AI") == "ui-reasoning":
            # Extract messages for mock case too
            if isinstance(prompt, PromptPayload):
                messages = prompt.messages
            elif isinstance(prompt, list):
                messages = [dict(m) for m in prompt]
            else:
                messages = [{"role": "user", "content": prompt}]

            reasoning_text = "Mock reasoning: UI verification"
            html_output = (
                "<!DOCTYPE html><html><head><meta charset=\"utf-8\"/>"
                "<title>Mock Output</title><style>body{font-family:sans-serif;padding:32px;}"
                "h1{color:#1f2937;}</style></head><body>"
                "<h1>Mock Page</h1><p>This HTML was generated by the test stub.</p>"
                "</body></html>"
            )
            meta = {
                "reasoning": reasoning_text,
                "total_cost": 0.0,
                "generation_time": 0.01,
                "messages": messages,
                "assistant_response": html_output,
            }
            op_status.clear_phase(worker)
            return (html_output, reasoning_text, meta)

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
            try:
                data_url = or_client.encode_image_to_data_url(path)
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
