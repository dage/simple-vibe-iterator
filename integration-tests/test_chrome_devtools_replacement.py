"""Chrome DevTools MCP service behavior tests (patched)."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

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
        "take_screenshot": [
            {
                "content": [
                    {"type": "image", "mimeType": "image/png", "data": "ZmFrZQ=="},
                ]
            }
        ],
        "press_key": [{"ok": True}],
        "wait_for": [{"ok": True}],
    }

    async def fake_eval(self, script: str, *, is_function: bool = False):
        if script.strip() == "() => window.__sviLogs || []":
            return [{"level": "log", "message": "case:simple"}]
        return True

    async def fake_call_tool(self, name: str, arguments: dict | None = None) -> dict:
        queue = responses.get(name)
        assert queue, f"Unexpected tool call {name}"
        return queue.pop(0)

    service.evaluate_script_mcp = fake_eval.__get__(service, ChromeDevToolsService)  # type: ignore[attr-defined]
    service._call_tool = fake_call_tool.__get__(service, ChromeDevToolsService)  # type: ignore[attr-defined]

    load_result = await service.load_html_mcp("<!DOCTYPE html><html><body>Hello</body></html>")
    assert isinstance(load_result, dict)
    assert load_result["ok"] is True
    assert isinstance(load_result["duration_ms"], int)

    screenshot = await service.take_screenshot_mcp()
    assert isinstance(screenshot, str) and screenshot.startswith("data:image/png")

    logs = await service.get_console_messages_mcp()
    assert logs and logs[0]["message"] == "case:simple"

    assert await service.press_key_mcp("w") is True
    wait_result = await service.wait_for_selector_mcp("Hello World")
    assert isinstance(wait_result, dict)
    assert wait_result.get("ok") is True
    assert isinstance(wait_result.get("duration_ms"), int)
    assert wait_result.get("status") == "ok"
    assert await service.performance_trace_start_mcp() is True
    trace = await service.performance_trace_stop_mcp()
    assert isinstance(trace, dict) and trace.get("fps") == 60


@pytest.mark.asyncio
async def test_waits_for_load_before_actions() -> None:
    service = ChromeDevToolsService(enabled=False)
    service.enabled = True

    scripts: list[str] = []

    async def fake_eval(self, script: str, *, is_function: bool = False):
        scripts.append(script)
        return True

    async def fake_call_tool(self, name: str, arguments: dict | None = None):
        if name == "take_screenshot":
            return {
                "content": [
                    {"type": "image", "mimeType": "image/png", "data": "ZmFrZQ=="},
                ]
            }
        return {"ok": True}

    service.evaluate_script_mcp = fake_eval.__get__(service, ChromeDevToolsService)  # type: ignore[attr-defined]
    service._call_tool = fake_call_tool.__get__(service, ChromeDevToolsService)  # type: ignore[attr-defined]

    await service.load_html_mcp("<!DOCTYPE html><html><body>Hi</body></html>")
    load_wait = any("addEventListener('load'" in script and "extraDelayMs" in script for script in scripts)

    scripts.clear()
    await service.take_screenshot_mcp()
    screenshot_wait = any("addEventListener('load'" in script and "extraDelayMs" in script for script in scripts)

    assert load_wait is True
    assert screenshot_wait is True


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


@pytest.mark.asyncio
async def test_load_html_resets_console_logs_and_reports_duration() -> None:
    service = ChromeDevToolsService(enabled=False)
    service.enabled = True

    steps: list[tuple[str, bool | None]] = []

    async def fake_install(self, *, reset_logs: bool = False):
        steps.append(("install", reset_logs))
        return True

    async def fake_eval(self, script: str, *, is_function: bool = False):
        if "document.write" in script:
            steps.append(("write", None))
        return True

    async def fake_wait(self, *, timeout_ms: int = 10000, extra_delay_s: float = 0.5):
        steps.append(("wait_ready", True))
        return True

    service._install_console_capture = fake_install.__get__(service, ChromeDevToolsService)  # type: ignore[attr-defined]
    service.evaluate_script_mcp = fake_eval.__get__(service, ChromeDevToolsService)  # type: ignore[attr-defined]
    service._wait_for_page_ready = fake_wait.__get__(service, ChromeDevToolsService)  # type: ignore[attr-defined]

    result = await service.load_html_mcp("<!DOCTYPE html><html><body>Reset</body></html>")
    assert result["ok"] is True
    assert isinstance(result["duration_ms"], int)
    assert steps[0] == ("install", True)
    assert any(action[0] == "write" for action in steps)
    assert steps[-1] == ("install", False)


@pytest.mark.asyncio
async def test_wait_for_selector_argument_passthrough_and_status() -> None:
    service = ChromeDevToolsService(enabled=False)
    service.enabled = True

    captured: dict[str, dict[str, Any]] = {}

    async def fake_call_tool(self, name: str, arguments: dict | None = None):
        captured["name"] = name
        captured["arguments"] = dict(arguments or {})
        return {"ok": False}

    service._call_tool = fake_call_tool.__get__(service, ChromeDevToolsService)  # type: ignore[attr-defined]

    result = await service.wait_for_selector_mcp("#demo", timeout_ms=1234)
    assert captured["name"] == "wait_for"
    assert captured["arguments"] == {"selector": "#demo", "timeout_ms": 1234}
    assert result["status"] == "timed_out"
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_get_console_messages_filters_levels() -> None:
    service = ChromeDevToolsService(enabled=False)
    service.enabled = True

    async def fake_eval(self, script: str, *, is_function: bool = False):
        assert script.strip() == "() => window.__sviLogs || []"
        return [
            {"level": "log", "message": "first"},
            {"level": "warn", "message": "second"},
        ]

    service.evaluate_script_mcp = fake_eval.__get__(service, ChromeDevToolsService)  # type: ignore[attr-defined]

    warn_entries = await service.get_console_messages_mcp(level="warn")
    assert warn_entries == [{"level": "warn", "message": "second"}]
