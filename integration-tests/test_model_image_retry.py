from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple
from unittest.mock import patch

os.environ.setdefault("OPENROUTER_DISABLE_RETRY", "1")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_root() -> Path:
    root = project_root()
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


PNG_BYTES = bytes.fromhex(
    "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000A"
    "49444154789C6360000002000100FFFF03000006000557FE0000000049454E44AE426082"
)


class RecordingBrowserService:
    def __init__(self, tmp_dir: Path) -> None:
        self.tmp_dir = tmp_dir
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.counter = 0

    async def render_and_capture(
        self,
        html_code: str,
        worker: str = "main",
        *,
        capture_count: int = 1,
        interval_seconds: float = 1.0,
    ) -> tuple[List[str], List[str]]:
        del html_code, worker, capture_count, interval_seconds
        self.counter += 1
        target = self.tmp_dir / f"capture_{self.counter}.png"
        target.write_bytes(PNG_BYTES)
        return [str(target)], ["[log] stub"]


class StubVisionService:
    async def analyze_screenshot(
        self,
        prompt: str,
        screenshot_paths: Sequence[str],
        console_logs: List[str],
        model: str,
        worker: str = "main",
    ) -> str:
        del prompt, screenshot_paths, console_logs, model, worker
        return "vision summary"


class FlakyAICodeService:
    def __init__(self) -> None:
        self.calls: List[List[Dict[str, object]]] = []
        self.fail_next_with_images = True

    async def generate_html(self, prompt, model: str, worker: str = "main") -> tuple[str, str | None, dict | None]:
        del model, worker
        if hasattr(prompt, "messages"):
            messages = list(getattr(prompt, "messages", []) or [])
        elif isinstance(prompt, list):
            messages = [dict(m) for m in prompt]
        else:
            messages = [{"role": "user", "content": str(prompt)}]
        self.calls.append(messages)

        def _has_images(msgs: List[Dict[str, object]]) -> bool:
            if not msgs:
                return False
            content = msgs[-1].get("content") if isinstance(msgs[-1], dict) else None
            if isinstance(content, list):
                return any(isinstance(part, dict) and part.get("type") == "image_url" for part in content)
            return False

        if self.fail_next_with_images and _has_images(messages):
            self.fail_next_with_images = False
            raise RuntimeError("No endpoints found that support image input")

        html = (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\"></head>"
            "<body><div id=\"app\">ok</div></body></html>"
        )
        meta = {"messages": messages, "assistant_response": html}
        return html, "", meta


async def _fake_detect(models: Sequence[str]) -> Dict[str, bool]:
    return {slug: True for slug in models}


async def test_retry_without_image_support() -> Tuple[bool, str]:
    ensure_root()

    tmp_dir = project_root() / "artifacts" / "test-model-image-retry"
    browser = RecordingBrowserService(tmp_dir)
    vision = StubVisionService()
    ai = FlakyAICodeService()

    from src.controller import IterationController
    from src.interfaces import TransitionSettings

    controller = IterationController(ai, browser, vision)

    settings = TransitionSettings(
        code_model="stub/code",
        vision_model="stub/vision",
        overall_goal="Test image retry",
        user_feedback="",
        code_template="Return HTML",
        vision_template="Describe",
        input_screenshot_count=1,
    )

    with patch("src.controller._detect_code_model_image_support", new=_fake_detect):
        root_id = await controller.apply_transition(None, settings)
        child_id = await controller.apply_transition(root_id, settings)

    node = controller.get_node(child_id)
    if not node:
        return False, "child node missing"

    if len(ai.calls) < 3:
        return False, "expected multiple AI calls for retry"

    # Focus on the last two calls from the second iteration
    first_retry = ai.calls[-2]
    second_retry = ai.calls[-1]

    def _has_images(msgs: List[Dict[str, object]]) -> bool:
        if not msgs:
            return False
        content = msgs[-1].get("content") if isinstance(msgs[-1], dict) else None
        if isinstance(content, list):
            return any(isinstance(part, dict) and part.get("type") == "image_url" for part in content)
        return False

    if not (_has_images(first_retry) and not _has_images(second_retry)):
        return False, "retry did not remove screenshot attachments"

    return True, "retry removes screenshots after failure"


async def main() -> int:
    ok, info = await test_retry_without_image_support()
    print(f"[ {'OK' if ok else 'FAIL'} ] Model image retry: {info}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
