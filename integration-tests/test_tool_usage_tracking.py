from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Tuple
from unittest.mock import AsyncMock, patch

os.environ.setdefault("OPENROUTER_DISABLE_RETRY", "1")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import context_data, or_client  # noqa: E402
from src.controller import IterationController  # noqa: E402
from src.interfaces import TransitionArtifacts  # noqa: E402


def ensure_cwd_project_root() -> Path:
    root = PROJECT_ROOT
    os.chdir(root)
    return root


async def test_tool_call_status_and_counter() -> Tuple[bool, str]:
    fake_browser = AsyncMock(return_value={"result": "ok"})
    expected_tool_phase = or_client._describe_tool_phase("press_key", "test-model")
    expected_coding_phase = "Coding|test-model"
    token = context_data.reset_context({
        "tool_call_count": 0,
        "worker_id": "test-model",
        "operation_id": "test",
    })
    try:
        with patch.object(or_client, "_execute_browser_tool", fake_browser):
            with patch.object(or_client.op_status, "set_phase") as mock_set_phase:
                await or_client._execute_tool_call("test-model", "press_key", '{"key":"w"}')
        if mock_set_phase.call_count != 2:
            return False, f"expected two status updates, got {mock_set_phase.call_count}"
        first_args = mock_set_phase.call_args_list[0][0]
        second_args = mock_set_phase.call_args_list[1][0]
        if first_args[1] != expected_tool_phase:
            return False, f"tool phase mismatch: expected {expected_tool_phase!r}, got {first_args[1]!r}"
        if second_args[1] != expected_coding_phase:
            return False, f"resume phase mismatch: expected {expected_coding_phase!r}, got {second_args[1]!r}"
        try:
            fake_browser.assert_awaited_once_with("press_key", {"key": "w"})
        except AssertionError as exc:
            return False, f"browser tool not invoked as expected: {exc}"
        calls = context_data.get("tool_call_count")
        if calls != 1:
            return False, f"tool counter value wrong: {calls}"
        snapshot = context_data.get_worker_snapshot("test-model")
        if snapshot.get("tool_call_count") != 1:
            return False, "context snapshot missing tool count"
        return True, "tracks status updates and counter"
    finally:
        context_data.restore_context(token)


async def test_wait_for_phase_includes_selector() -> Tuple[bool, str]:
    selector = "#main-content .item"
    fake_browser = AsyncMock(return_value={"ok": True})
    payload = {"selector": selector, "timeout_ms": 1234}
    token = context_data.reset_context({"tool_call_count": 0})
    try:
        with patch.object(or_client, "_execute_browser_tool", fake_browser):
            with patch.object(or_client.op_status, "set_phase") as mock_set_phase:
                await or_client._execute_tool_call("test-model", "wait_for", json.dumps(payload))
    finally:
        context_data.restore_context(token)
    first_phase = mock_set_phase.call_args_list[0][0][1]
    expected = "wait_for #main-cont|test-model"
    if first_phase != expected:
        return False, f"unexpected wait_for phase: {first_phase!r} (expected {expected!r})"
    return True, "wait_for phase includes selector"


async def test_results_include_tool_call_counts() -> Tuple[bool, str]:
    controller = IterationController(object(), object(), object())
    artifacts = TransitionArtifacts(
        screenshot_filename="out.png",
        console_logs=[],
        vision_output="",
        input_screenshot_filenames=[],
        input_console_logs=[],
    )
    meta: dict = {"tool_call_count": 5}
    outputs = controller._results_to_model_outputs({"slug": ("<div/>", "reason", meta, artifacts)})
    output = outputs.get("slug")
    if output is None:
        return False, "missing model output"
    if output.tool_call_count != 5:
        return False, f"expected 5 tool calls, got {output.tool_call_count!r}"
    return True, "tool call count recorded on model output"


async def test_analyze_screen_tool_runs_vision() -> Tuple[bool, str]:
    class StubService:
        def __init__(self) -> None:
            self.enabled = True

        async def take_screenshot_mcp(self):
            return "data:image/png;base64,ZmFrZQ=="

        async def get_console_messages_mcp(self, level: str | None = None):
            return [
                {"level": "log", "message": "render complete"},
                {"level": "error", "message": "bad shader"},
            ]

    service = StubService()
    resolver = AsyncMock(return_value=(service, False))
    fake_vision = AsyncMock(return_value="- Observed UI state")
    query = "Is the save button visible?"

    with patch.object(or_client, "_resolve_devtools_service", resolver):
        with patch.object(or_client, "vision_single", fake_vision):
            result = await or_client._execute_browser_tool("analyze_screen", {"query": query})

    if "analysis" not in result or not result["analysis"]:
        return False, "analyze_screen did not return analysis text"
    if not result.get("console_logs"):
        return False, "console logs missing from analyze_screen response"
    try:
        fake_vision.assert_awaited_once()
    except AssertionError as exc:
        return False, f"vision helper not invoked: {exc}"
    prompt, image = fake_vision.call_args[0][:2]
    if query not in prompt:
        return False, "custom query not passed to vision prompt"
    if "console logs" not in prompt.lower():
        return False, "console logs not included in vision prompt"
    if not isinstance(image, str) or not image.startswith("data:image/png"):
        return False, "screenshot data URL not forwarded"
    return True, "analyze_screen captures screenshot and runs vision"


async def main() -> int:
    ensure_cwd_project_root()

    checks = [
        ("Tool call status tracking", test_tool_call_status_and_counter),
        ("wait_for selector labeling", test_wait_for_phase_includes_selector),
        ("Model output tool counts", test_results_include_tool_call_counts),
        ("analyze_screen vision bridge", test_analyze_screen_tool_runs_vision),
    ]

    ok_all = True
    for name, fn in checks:
        try:
            ok, msg = await fn()
        except Exception as exc:
            ok, msg = False, f"error: {exc}"
        status = "OK" if ok else "FAIL"
        print(f"[ {status} ] {name}: {msg}")
        ok_all = ok_all and ok

    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
