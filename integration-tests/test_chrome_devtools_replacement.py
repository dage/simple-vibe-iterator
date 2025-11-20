"""Chrome DevTools MCP service behavior tests (patched)."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from src.chrome_devtools_service import ChromeDevToolsService


@pytest.mark.asyncio
async def test_service_disabled_without_config(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    service = ChromeDevToolsService(mcp_config_path=str(missing), enabled=True)
    assert service.enabled is False
    assert await service.take_screenshot_mcp() is None


@pytest.mark.asyncio
async def test_devtools_methods_without_server() -> None:
    service = ChromeDevToolsService(enabled=False)
    service.enabled = True

    responses = {
        "evaluate_script": [
            {"result": True},
            {"result": True},
            {"result": True},
            {"result": [{"level": "log", "message": "case:simple"}]},
        ],
        "take_screenshot": [
            {
                "content": [
                    {"type": "image", "mimeType": "image/png", "data": "ZmFrZQ=="},
                ]
            }
        ],
        "press_key": [{"ok": True}],
        "wait_for": [{"ok": True}],
        "performance_start_trace": [{"ok": True}],
        "performance_stop_trace": [{"fps": 60}],
    }

    async def fake_call_tool(self, name: str, arguments: dict | None = None) -> dict:
        queue = responses.get(name)
        assert queue, f"Unexpected tool call {name}"
        return queue.pop(0)

    service._call_tool = fake_call_tool.__get__(service, ChromeDevToolsService)  # type: ignore[attr-defined]

    assert await service.load_html_mcp("<!DOCTYPE html><html><body>Hello</body></html>")

    screenshot = await service.take_screenshot_mcp()
    assert isinstance(screenshot, str) and screenshot.startswith("data:image/png")

    logs = await service.get_console_messages_mcp()
    assert logs and logs[0]["message"] == "case:simple"

    assert await service.press_key_mcp("w") is True
    assert await service.wait_for_selector_mcp("Hello World") is True
    assert await service.performance_trace_start_mcp() is True
    trace = await service.performance_trace_stop_mcp()
    assert isinstance(trace, dict) and trace.get("fps") == 60


@pytest.mark.asyncio
async def test_press_key_hold_path() -> None:
    service = ChromeDevToolsService(enabled=False)
    service.enabled = True

    called: dict = {}

    async def fake_eval(self, script: str, *, is_function: bool = False):
        called["script"] = script
        called["is_function"] = is_function
        return True

    async def fake_call_tool(self, name: str, arguments: dict | None = None):
        raise AssertionError("press_key tool should not be used for long holds")

    service.evaluate_script_mcp = fake_eval.__get__(service, ChromeDevToolsService)  # type: ignore[attr-defined]
    service._call_tool = fake_call_tool.__get__(service, ChromeDevToolsService)  # type: ignore[attr-defined]

    assert await service.press_key_mcp("w", duration_ms=800) is True
    assert "setTimeout" in called["script"]
    assert called["is_function"] is True
