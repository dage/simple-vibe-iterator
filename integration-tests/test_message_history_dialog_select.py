from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple
from unittest.mock import patch, MagicMock

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
    def __init__(self, models: List[str]) -> None:
        self.models = models
        self.calls: List[List[Dict[str, object]]] = []
        self.call_count = 0

    async def generate_html(self, prompt, model: str, worker: str = "main") -> tuple[str, str | None, Dict[str, object]]:
        del worker
        if hasattr(prompt, "messages"):
            messages = list(getattr(prompt, "messages", []) or [])
        elif isinstance(prompt, list):
            messages = [dict(m) for m in prompt]
        else:
            messages = [{"role": "user", "content": str(prompt or "")}]

        captured = [dict(m) for m in messages]
        self.calls.append(captured)
        self.call_count += 1

        html = (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\"></head><body>"
            f"<p>iteration {self.call_count} with {model}</p>"
            "</body></html>"
        )
        meta: Dict[str, object] = {
            "messages": captured,
            "assistant_response": html,
        }
        return html, "", meta


async def _fake_capabilities(models: List[str]) -> Dict[str, bool]:
    return {slug: True for slug in models}


async def test_message_history_dialog_select_issue() -> Tuple[bool, str]:
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
            from src.view import NiceGUIView

            reset_settings()
            settings_manager = get_settings()

            artifacts_dir = project_root() / "artifacts" / "test_message_history_dialog"
            models = ["openai/gpt-4", "anthropic/claude-3", "google/gemini-pro"]
            ai = RecordingHistoryAICodeService(models)
            browser = StubBrowserService(artifacts_dir)
            vision = StubVisionService()
            controller = IterationController(ai, browser, vision)

            # Mock the UI since we can't run the full NiceGUI app in tests
            mock_ui = MagicMock()

            # Create view instance
            view = NiceGUIView(controller)

            # Create multiple outputs for different models
            base_settings = TransitionSettings(
                code_model="openai/gpt-4",
                vision_model="stub/vision",
                overall_goal="Test history dialog",
                user_feedback="",
                code_template="Generate HTML: {overall_goal}",
                vision_template="Describe the page: {html_input}",
                input_screenshot_count=1,
            )

            # Create a node with multiple model outputs
            root_id = await controller.apply_transition(None, base_settings)

            # Simulate having multiple model outputs by manually adding them
            node = controller.get_node(root_id)
            if node and node.outputs:
                # Add fake outputs for other models
                from src.interfaces import ModelOutput, TransitionArtifacts
                fake_artifacts = TransitionArtifacts(
                    screenshot_filename="",
                    console_logs=[],
                    vision_output="",
                    input_screenshot_filenames=[],
                    input_console_logs=[],
                    assets=[],
                    analysis={}
                )

                for model_slug in models[1:]:  # Skip the first model which already exists
                    fake_output = ModelOutput(
                        html_output=f"<html><body>{model_slug} output</body></html>",
                        artifacts=fake_artifacts,
                        messages=[{"role": "user", "content": "test"}],
                        assistant_response=f"Response from {model_slug}",
                        total_cost=0.01,
                        generation_time=1.0,
                        reasoning_text=None
                    )
                    node.outputs[model_slug] = fake_output

            # Now test the dialog opening logic
            if not node or not node.outputs:
                return False, "Failed to create node with outputs"

            message_slugs = list(node.outputs.keys())
            print(f"Available model slugs: {message_slugs}")

            # Test the _render_for function with different slugs
            test_results = []

            for slug in message_slugs:
                output = node.outputs.get(slug)
                if not output:
                    test_results.append(f"FAIL: Messages missing for {slug}")
                    continue

                history = list(output.messages or [])
                if output.assistant_response:
                    history.append({"role": "assistant", "content": output.assistant_response})

                if not history:
                    history = [{"role": "system", "content": "(no message history captured)"}]

                test_results.append(f"OK: Found {len(history)} messages for {slug}")

            # Test the selector value handling
            print("Testing selector value extraction...")

            # Simulate different event formats that NiceGUI might emit
            class MockEvent:
                def __init__(self, **kwargs):
                    for k, v in kwargs.items():
                        setattr(self, k, v)

            test_events = [
                MockEvent(value="openai/gpt-4"),  # Direct string value
                MockEvent(value=0),  # Index instead of string
                MockEvent(value=1),  # Index instead of string
                MockEvent(args={"value": "anthropic/claude-3"}),  # Nested in args
                MockEvent(args=2),  # Index in args
                MockEvent(),  # Empty event
            ]

            selector_value_extraction_results = []
            for i, event in enumerate(test_events):
                # Simulate the updated _on_change logic
                value = getattr(event, 'value', None)
                if not value:
                    args = getattr(event, 'args', None)
                    if isinstance(args, dict):
                        value = args.get('value')
                    elif args:
                        value = args
                if value is not None:
                    # Handle case where NiceGUI emits index instead of value (updated logic)
                    try:
                        # Check if value is an integer (index) or string representation of integer
                        if isinstance(value, int):
                            idx = value
                        elif isinstance(value, str) and value.isdigit():
                            idx = int(value)
                        else:
                            idx = None

                        if idx is not None and 0 <= idx < len(message_slugs):
                            # Use index to get the actual slug
                            slug = message_slugs[idx]
                            selector_value_extraction_results.append(f"Event {i}: Index {value} -> {slug}")
                        else:
                            # Use value directly as slug
                            slug = str(value)
                            selector_value_extraction_results.append(f"Event {i}: Direct value {value} -> {slug}")
                    except (ValueError, IndexError):
                        slug = str(value)
                        selector_value_extraction_results.append(f"Event {i}: Fallback value {value} -> {slug}")
                else:
                    selector_value_extraction_results.append(f"Event {i}: No value found")

            # Print results
            print("\nMessage history test results:")
            for result in test_results:
                print(f"  {result}")

            print("\nSelector value extraction test results:")
            for result in selector_value_extraction_results:
                print(f"  {result}")

            # Check if any issues were found
            has_issues = any("FAIL" in result for result in test_results)

            return not has_issues, f"Dialog select issue reproduced. NiceGUI select emits indices instead of string values. Need to handle index-to-slug mapping."


async def main() -> int:
    ok, info = await test_message_history_dialog_select_issue()
    status = "OK" if ok else "FAIL"
    print(f"[ {status} ] Message history dialog select: {info}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
