from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Dict, Sequence
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


def _prompt_to_text(prompt) -> str:
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


class RecordingAICodeService:
    def __init__(self, html_response: str) -> None:
        self.html_response = html_response
        self.last_prompt: str = ""

    async def generate_html(self, prompt, model: str, worker: str = "main", *, template_context: Dict | None = None):
        self.last_prompt = _prompt_to_text(prompt)
        return self.html_response, "", {}


class StubBrowserService:
    def __init__(self, tmp_dir: Path) -> None:
        self.tmp_dir = tmp_dir
        self._counter = 0
        self.last_html: str = ""

    async def render_and_capture(
        self,
        html_code: str,
        worker: str = "main",
        *,
        capture_count: int = 1,
        interval_seconds: float = 1.0,
    ) -> tuple[list[str], list[str]]:
        self.last_html = html_code
        self._counter += 1
        target = self.tmp_dir / f"capture_{self._counter}.png"
        target.write_bytes(PNG_BYTES)
        return [str(target)], []


class StubVisionService:
    async def analyze_screenshot(
        self,
        prompt: str,
        screenshot_paths: Sequence[str],
        console_logs: list[str],
        model: str,
        worker: str = "vision",
    ) -> str:
        return "- ok"


async def _fake_capabilities(models):
    return {slug: False for slug in models}


async def test_template_variables_injection() -> tuple[bool, str]:
    inject_src()
    from src.controller import IterationController
    from src.interfaces import TransitionSettings

    tmp_dir = project_root() / "artifacts" / "test_template_vars"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    html_response = (
        "<!DOCTYPE html><html><body>"
        "<audio id=\"bg\" src=\"{{SOUND_MP3_DATA_URL}}\"></audio>"
        "<pre>{{API_DOCS}}</pre>"
        "<div class=\"text\">{{PLAINTEXT_FILE}}</div>"
        "</body></html>"
    )
    ai = RecordingAICodeService(html_response)
    browser = StubBrowserService(tmp_dir)
    vision = StubVisionService()
    controller = IterationController(ai, browser, vision)

    controller.set_template_file_variable(
        "sound_mp3_data_url",
        b"abc123",
        mime_type="audio/mpeg",
        filename="sound.mp3",
    )
    controller.set_template_text_variable(
        "API_DOCS",
        "Web Audio API lets you create immersive spatial mixes.",
    )
    controller.set_template_text_variable(
        "TEST_SINGLE",
        "Single brace injection works!",
    )
    controller.set_template_file_variable(
        "PLAINTEXT_FILE",
        b"Alpha Beta",
        mime_type="text/plain",
        filename="note.txt",
    )

    settings = TransitionSettings(
        code_model="stub/code",
        vision_model="stub/vision",
        overall_goal="Embed audio",
        user_feedback="",
        code_template="HTML: {html_input}",
        code_system_prompt_template="Vars:\n{template_vars_list}",
        vision_template="Vision uses {template_vars_list}",
    )

    with patch("src.controller._detect_code_model_image_support", new=_fake_capabilities):
        node_id = await controller.apply_transition(None, settings)
        root_browser_html = browser.last_html
        ai.html_response = (
            "<!DOCTYPE html><html><body>"
            "<section>{TEST_SINGLE}</section>"
            "</body></html>"
        )
        child_id = await controller.apply_transition(node_id, settings)

    node = controller.get_node(node_id)
    if node is None:
        return False, "node missing"
    if not node.outputs:
        return False, "outputs missing"
    output = next(iter(node.outputs.values()))
    html = output.html_output
    if "{{SOUND_MP3_DATA_URL}}" in html:
        return False, "placeholder was not injected for files"
    if "{{API_DOCS}}" in html:
        return False, "placeholder was not injected for text"
    if "{{PLAINTEXT_FILE}}" in html:
        return False, "text-mode file placeholder not injected"
    if "data:audio/mpeg;base64" not in html:
        return False, "audio data url missing"
    if "Web Audio API" not in html:
        return False, "text variable missing"
    if "Alpha Beta" not in html:
        return False, "plaintext file content missing"
    if "SOUND_MP3_DATA_URL" not in ai.last_prompt:
        return False, "system prompt missing template variable summary"
    if "audio/mpeg" not in ai.last_prompt:
        return False, "template variable summary missing mime type detail"
    if "injected as data-url" not in ai.last_prompt:
        return False, "template variable summary missing injection mode"
    if "API_DOCS" not in ai.last_prompt:
        return False, "system prompt missing text variable entry"
    if "text (54 chars)" not in ai.last_prompt:
        return False, "text variable summary missing length"
    if root_browser_html != html:
        return False, "browser received different HTML than stored output"
    if "data:audio/mpeg;base64" not in root_browser_html:
        return False, "browser HTML missing injected data url"

    child = controller.get_node(child_id)
    if child is None:
        return False, "child missing"
    child_output = next(iter(child.outputs.values()))
    child_html = child_output.html_output
    if "{TEST_SINGLE}" in child_html:
        return False, "single-brace placeholder was not replaced"
    if "Single brace injection works!" not in child_html:
        return False, "single-brace template text missing"

    return True, "template variables injected into HTML and prompt context"


async def test_template_var_summary_fallback() -> tuple[bool, str]:
    inject_src()
    from src.controller import IterationController
    from src.interfaces import TransitionSettings

    class BlankSummaryController(IterationController):
        def template_vars_prompt_text(self) -> str:
            # Simulate a caller forgetting to pass the summary string
            return ""

    tmp_dir = project_root() / "artifacts" / "test_template_vars"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    ai = RecordingAICodeService("<!DOCTYPE html><html><body>{{FILE_ONE}}</body></html>")
    browser = StubBrowserService(tmp_dir)
    vision = StubVisionService()
    controller = BlankSummaryController(ai, browser, vision)

    controller.set_template_file_variable(
        "FILE_ONE",
        b"abc123",
        mime_type="audio/mpeg",
        filename="song.mp3",
    )
    controller.set_template_file_variable(
        "FILE_TWO",
        b"alpha beta",
        mime_type="text/plain",
        filename="note.txt",
    )

    settings = TransitionSettings(
        code_model="stub/code",
        vision_model="stub/vision",
        overall_goal="Fallback summary check",
        user_feedback="",
        code_template="{html_input}",
        code_system_prompt_template="Vars:\n{template_vars_list}",
        vision_template="",
    )

    with patch("src.controller._detect_code_model_image_support", new=_fake_capabilities):
        await controller.apply_transition(None, settings)

    prompt_text = ai.last_prompt
    if "FILE_ONE" not in prompt_text or "FILE_TWO" not in prompt_text:
        return False, "template variable keys missing from system prompt"
    if "None" in prompt_text:
        return False, "fallback summary did not populate template variable list"
    if "audio/mpeg" not in prompt_text or "injected as" not in prompt_text:
        return False, "file variable details missing from summary"
    return True, "template variable summary recomputed when missing"


async def main() -> int:
    ensure_root_cwd()
    tests = [
        ("Template variables", test_template_variables_injection),
        ("Template variable summary fallback", test_template_var_summary_fallback),
    ]
    rc = 0
    for label, fn in tests:
        ok, info = await fn()
        status = "OK" if ok else "FAIL"
        print(f"[ {status} ] {label}: {info}")
        if not ok:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
