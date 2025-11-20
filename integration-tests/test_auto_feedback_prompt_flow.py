from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Sequence, Tuple
from unittest.mock import patch

from support import ensure_project_root

os.environ.setdefault("OPENROUTER_DISABLE_RETRY", "1")


def _prompt_to_text(prompt: Any) -> str:
    try:
        if hasattr(prompt, "messages"):
            messages = list(getattr(prompt, "messages", []) or [])
        elif isinstance(prompt, list):
            messages = list(prompt)
        else:
            return str(prompt or "")
        if not messages:
            return ""
        first = messages[0] if isinstance(messages[0], dict) else {}
        content = first.get("content", "") if isinstance(first, dict) else ""
        if isinstance(content, list):
            texts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            return "\n".join([t for t in texts if t])
        return str(content or "")
    except Exception:
        return str(prompt or "")


class RecordingAICodeService:
    def __init__(self) -> None:
        self.prompts: List[str] = []

    async def generate_html(
        self,
        prompt,
        model: str,
        worker: str = "main",
        *,
        template_context: Dict[str, Any] | None = None,
    ) -> Tuple[str, str | None, Dict | None]:
        self.prompts.append(_prompt_to_text(prompt))
        html = "<!DOCTYPE html><html><body><main>ok</main></body></html>"
        return html, "", {}


class RecordingVisionService:
    def __init__(self) -> None:
        self.prompts: List[str] = []

    async def analyze_screenshot(
        self,
        prompt: str,
        screenshot_paths: Sequence[str],
        console_logs: List[str],
        model: str,
        worker: str = "main",
    ) -> str:
        self.prompts.append(prompt)
        return "vision-ok"


class StubBrowserService:
    def __init__(self) -> None:
        self.preset_calls: List[str] = []
        self.render_calls: List[str] = []

    async def run_feedback_preset(self, html_code: str, preset, worker: str = "main") -> tuple[List[str], List[str], List[str]]:
        self.preset_calls.append(worker)
        return (["input-shot.png"], ["preset-log"], ["press-w", "press-s"])

    async def render_and_capture(
        self,
        html_code: str,
        worker: str = "main",
        *,
        capture_count: int = 1,
        interval_seconds: float = 1.0,
    ) -> tuple[List[str], List[str]]:
        self.render_calls.append(worker)
        return ([f"{worker}-shot.png"], [f"log-{worker}"])


async def _fake_capabilities(models: List[str]) -> Dict[str, bool]:
    return {slug: True for slug in models}


async def test_feedback_preset_auto_feedback_flow() -> Tuple[bool, str]:
    from src.controller import IterationController
    from src.feedback_presets import FeedbackAction, FeedbackPreset
    from src.interfaces import TransitionSettings

    ai = RecordingAICodeService()
    browser = StubBrowserService()
    vision = RecordingVisionService()
    ctrl = IterationController(ai, browser, vision)

    settings = TransitionSettings(
        code_model="cm-auto",
        vision_model="vm-auto",
        overall_goal="Verify auto feedback propagation",
        user_feedback="",
        code_template="CODE TEMPLATE auto_feedback={auto_feedback}",
        vision_template="VISION TEMPLATE auto_feedback={auto_feedback}",
        feedback_preset_id="preset-auto",
    )

    preset = FeedbackPreset(
        id="preset-auto",
        label="Preset Auto",
        actions=(
            FeedbackAction(kind="wait", seconds=0.05),
            FeedbackAction(kind="screenshot", label="press-w"),
            FeedbackAction(kind="screenshot", label="press-s"),
        ),
    )

    expected_auto = "Preset preset-auto steps → #1: press-w, #2: press-s"

    with patch("src.feedback_presets.get_feedback_preset", return_value=preset), patch(
        "src.controller._detect_code_model_image_support",
        new=_fake_capabilities,
    ):
        root_id = await ctrl.apply_transition(None, settings)
        child_id = await ctrl.apply_transition(root_id, settings)

    if not child_id or not ctrl.get_node(child_id):
        return False, "child node missing"

    if browser.preset_calls.count("input") != 1:
        return False, f"unexpected preset invocation(s): {browser.preset_calls}"

    if not ai.prompts or expected_auto not in ai.prompts[-1]:
        return False, "code prompt did not receive auto_feedback string"
    if not vision.prompts or expected_auto not in vision.prompts[-1]:
        return False, "vision prompt did not include auto_feedback"

    child = ctrl.get_node(child_id)
    if not child:
        return False, "child node missing after prompts check"
    output = child.outputs.get(settings.code_model)
    if not output:
        return False, "child missing code model output"
    analysis = output.artifacts.analysis
    if analysis.get("feedback_preset_id") != "preset-auto":
        return False, "artifact analysis missing preset id"

    return True, "auto_feedback string from preset propagated to prompts"


async def main() -> int:
    ensure_project_root()
    try:
        ok, msg = await test_feedback_preset_auto_feedback_flow()
    except Exception as exc:
        ok, msg = False, f"error: {exc}"
    print(f"[ {'OK' if ok else 'FAIL'} ] Auto Feedback Prompt Flow: {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
