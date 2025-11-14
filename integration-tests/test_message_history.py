from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple
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


class StubBrowserService:
    def __init__(self, temp_dir: Path) -> None:
        self.temp_dir = temp_dir
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._counter = 0

    async def render_and_capture(
        self,
        html_code: str,
        worker: str = "main",
        *,
        capture_count: int = 1,
        interval_seconds: float = 1.0,
    ) -> tuple[List[str], List[str]]:
        del worker, interval_seconds
        try:
            count = int(capture_count)
        except Exception:
            count = 1
        count = max(1, count)
        outputs: List[str] = []
        for _ in range(count):
            self._counter += 1
            target = self.temp_dir / f"capture_{self._counter}.png"
            target.write_bytes(PNG_BYTES)
            outputs.append(str(target))
        return outputs, ["[log] stub"]


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
        return "stub vision"


class RecordingHistoryAICodeService:
    def __init__(self) -> None:
        self.calls: List[List[Dict[str, object]]] = []

    async def generate_html(self, prompt, model: str, worker: str = "main") -> tuple[str, str | None, Dict[str, object]]:
        del model, worker
        if hasattr(prompt, "messages"):
            messages = list(getattr(prompt, "messages", []) or [])
        elif isinstance(prompt, list):
            messages = [dict(m) for m in prompt]
        else:
            messages = [{"role": "user", "content": str(prompt or "")}]
        captured = [dict(m) for m in messages]
        self.calls.append(captured)
        iteration = len(self.calls)
        html = (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\"></head><body>"
            f"<p>iteration {iteration}</p>"
            "</body></html>"
        )
        meta: Dict[str, object] = {
            "messages": captured,
            "assistant_response": html,
        }
        return html, "", meta

async def _fake_capabilities(models: List[str]) -> Dict[str, bool]:
    return {slug: True for slug in models}


async def test_message_history_without_duplication() -> Tuple[bool, str]:
    ensure_root_cwd()
    inject_src()

    prefs_store: Dict[str, str] = {}

    def _fake_get(key: str, default: str = "") -> str:
        return str(prefs_store.get(key, default))

    def _fake_set(key: str, value: str) -> None:
        prefs_store[key] = str(value)

    with patch("src.prefs.get", new=_fake_get), patch("src.prefs.set", new=_fake_set):
        with patch("src.controller._detect_code_model_image_support", new=_fake_capabilities):
            from src.controller import IterationController
            from src.interfaces import TransitionSettings
            from src.settings import get_settings, reset_settings

            reset_settings()
            settings_manager = get_settings()

            artifacts_dir = project_root() / "artifacts" / "test_message_history"
            ai = RecordingHistoryAICodeService()
            browser = StubBrowserService(artifacts_dir)
            vision = StubVisionService()
            controller = IterationController(ai, browser, vision)

            base_settings = TransitionSettings(
                code_model="stub/code",
                vision_model="stub/vision",
                overall_goal="Test history",
                user_feedback="",
                code_template=(
                    "Refine HTML given the goal.\n"
                    "Goal: {overall_goal}\n"
                    "Vision: {vision_output}\n"
                    "HTML:\n{html_input}\n"
                ),
                vision_template="Describe the page.\nHTML:\n{html_input}\n",
                input_screenshot_count=1,
            )

            root_id = await controller.apply_transition(None, base_settings)
            child1_id = await controller.apply_transition(root_id, base_settings)
            child2_id = await controller.apply_transition(child1_id, base_settings)

        if len(ai.calls) != 3:
            return False, f"expected 3 model calls, found {len(ai.calls)}"

        expected_roles = [
            ["system", "user"],
            ["system", "user", "assistant", "user"],
            ["system", "user", "assistant", "user", "assistant", "user"],
        ]
        for idx, call in enumerate(ai.calls):
            roles = [str(msg.get("role")) for msg in call]
            if roles != expected_roles[idx]:
                return False, f"iteration {idx + 1} roles mismatch: {roles}"

        final_node = controller.get_node(child2_id)
        if not final_node:
            return False, "final node missing"
        final_output = final_node.outputs.get("stub/code")
        if not final_output or not final_output.messages:
            return False, "final output lacks stored messages"

        # Messages stored on the node should match the prompt sent for that iteration.
        if final_output.messages != ai.calls[-1]:
            return False, "stored messages differ from prompt"

        user_count = sum(1 for msg in final_output.messages if msg.get("role") == "user")
        if user_count != 3:
            return False, f"expected 3 distinct user messages, found {user_count}"

        return True, "message history carries forward without duplication"


async def main() -> int:
    ok, info = await test_message_history_without_duplication()
    status = "OK" if ok else "FAIL"
    print(f"[ {status} ] Message history: {info}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
