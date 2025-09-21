from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import List, Tuple
from unittest.mock import patch


PNG_BYTES = bytes.fromhex(
    "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000A"
    "49444154789C6360000002000100FFFF03000006000557FE0000000049454E44AE426082"
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_root_cwd() -> Path:
    root = project_root()
    os.chdir(root)
    return root


def inject_src() -> None:
    root = project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


os.environ.setdefault("OPENROUTER_DISABLE_RETRY", "1")


def _prompt_to_text(prompt) -> Tuple[str, List[dict]]:
    messages: List[dict]
    if hasattr(prompt, "messages"):
        messages = list(getattr(prompt, "messages", []) or [])
    elif isinstance(prompt, list):
        messages = list(prompt)
    else:
        return str(prompt or ""), []
    if not messages:
        return "", []
    first = messages[0] if isinstance(messages[0], dict) else {}
    content = first.get("content", "") if isinstance(first, dict) else ""
    if isinstance(content, list):
        texts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "\n".join([t for t in texts if t]), content
    return str(content or ""), []


class StubBrowserService:
    def __init__(self, temp_dir: Path) -> None:
        self.temp_dir = temp_dir
        self._counter = 0

    async def render_and_capture(self, html_code: str, worker: str = "main") -> tuple[str, List[str]]:
        self._counter += 1
        target = self.temp_dir / f"capture_{self._counter}.png"
        target.write_bytes(PNG_BYTES)
        return str(target), ["[log] stub"]


class StubVisionService:
    def __init__(self) -> None:
        self.called = False

    async def analyze_screenshot(self, *args, **kwargs) -> str:
        self.called = True
        raise AssertionError("Vision service should not be called in direct mode")


class RecordingAICodeService:
    def __init__(self) -> None:
        self.last_prompt_text: str = ""
        self.last_messages: List[dict] = []

    async def generate_html(self, prompt, model: str, worker: str = "main") -> tuple[str, str | None, dict | None]:
        text, content = _prompt_to_text(prompt)
        self.last_prompt_text = text
        self.last_messages = content if content else []
        return (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\"></head><body><div id=\"app\">v1</div></body></html>",
            "",
            {},
        )


async def test_direct_mode_prompt_contains_image() -> Tuple[bool, str]:
    from src.controller import IterationController
    from src.interfaces import IterationMode, TransitionSettings

    temp_dir = project_root() / "artifacts" / "test_direct_mode"
    temp_dir.mkdir(parents=True, exist_ok=True)

    ai = RecordingAICodeService()
    browser = StubBrowserService(temp_dir)
    vision = StubVisionService()
    ctrl = IterationController(ai, browser, vision)

    settings = TransitionSettings(
        code_model="stub/code",
        vision_model="stub/vision",
        overall_goal="Create a box",
        user_steering="",
        code_template=(
            "Direct mode prompt\n"
            "Goal: {overall_goal}\n"
            "Vision analysis: {vision_output}\n"
            "HTML:\n{html_input}\n"
        ),
        vision_template="",
        mode=IterationMode.DIRECT_TO_CODER,
    )

    # Seed initial node (no screenshot yet)
    async def _noop(*args, **kwargs):
        return None

    with patch("src.controller._ensure_models_support_mode", new=_noop):
        root_id = await ctrl.apply_transition(None, settings)
        root = ctrl.get_node(root_id)
        if not root:
            return False, "root missing"

        child_id = await ctrl.apply_transition(root_id, settings)
    child = ctrl.get_node(child_id)
    if not child:
        return False, "child missing"

    if vision.called:
        return False, "vision service should not be invoked in direct mode"

    if not ai.last_messages:
        return False, "direct mode prompt did not include image content"

    has_image = any(
        isinstance(part, dict)
        and part.get("type") == "image_url"
        and isinstance(part.get("image_url", {}).get("url"), str)
        for part in ai.last_messages
    )
    if not has_image:
        return False, "image attachment missing from prompt"

    if "vision" in ai.last_prompt_text.lower():
        return False, "prompt text still references vision analysis"

    return True, "direct mode prompt attaches screenshot"


async def main() -> int:
    ensure_root_cwd()
    inject_src()
    ok, info = await test_direct_mode_prompt_contains_image()
    status = "OK" if ok else "FAIL"
    print(f"[ {status} ] Direct mode prompt: {info}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
