from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from unittest.mock import AsyncMock, patch

os.environ.setdefault("OPENROUTER_DISABLE_RETRY", "1")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import context_data, or_client  # noqa: E402


class TestDevToolsService:
    """Stateful stub that mirrors the successful tool diagnostics flow."""

    def __init__(self) -> None:
        self.enabled = True
        self.trace_active = False
        self.html: str = ""
        self.reset_state()

    def reset_state(self) -> None:
        self.count = 0
        self.last_key = ""
        self.logs: List[Dict[str, str]] = [
            {"level": "log", "message": "Page loaded"},
            {"level": "info", "message": "Initial info"},
            {"level": "warn", "message": "⚠️ WARN: This is warn"},
            {"level": "error", "message": "❌ ERROR: This is error"},
        ]

    async def load_html_mcp(self, html: str) -> Dict[str, object]:
        self.html = html
        self.reset_state()
        return {"ok": True, "duration_ms": 2477}

    async def get_console_messages_mcp(self, level: str | None = None) -> List[Dict[str, str]]:
        messages = list(self.logs)
        if level:
            messages = [entry for entry in messages if entry.get("level") == level]
        return messages

    async def press_key_mcp(self, key: str, duration_ms: int = 100) -> bool:
        self.count += 1
        if key == "Space":
            display = " "
            self.last_key = " "
        else:
            display = key
            self.last_key = key
        self.logs.append({"level": "log", "message": f"{display} (count now {self.count})"})
        return True

    async def evaluate_script_mcp(self, script: str):
        script = script.strip()
        if script == "window.testVar":
            return "⭐ Success from evaluate_script!"
        if script == "window.testObj.count":
            return self.count
        if script == "window.testObj.lastKey":
            return self.last_key
        return None

    async def wait_for_selector_mcp(self, selector: str, timeout_ms: int = 5000) -> Dict[str, object]:
        if selector in ("#title", "#late-element"):
            return {"ok": True, "status": "ok", "duration_ms": 1}
        return {"ok": False, "status": "timed_out", "duration_ms": timeout_ms}

    async def take_screenshot_mcp(self) -> str:
        return "data:image/png;base64,ZmFrZQ=="

    async def performance_trace_start_mcp(self) -> bool:
        self.trace_active = True
        return True

    async def performance_trace_stop_mcp(self) -> Dict[str, object]:
        was_active = self.trace_active
        self.trace_active = False
        return {"fps": 25, "active_before_stop": was_active}

    async def aclose(self) -> None:
        return None


async def test_full_tools_flow() -> Tuple[bool, str]:
    token = context_data.reset_context({"tool_call_count": 0, "worker_id": "test-worker", "model_slug": "test-model"})
    service = TestDevToolsService()
    vision_mock = AsyncMock(return_value="Vision OK with logs")

    async def _resolve_devtools_service():
        return service, False

    try:
        with patch.object(or_client, "_resolve_devtools_service", side_effect=_resolve_devtools_service):
            with patch.object(or_client, "vision_single", vision_mock):
                html = "<!DOCTYPE html><html><body>Diagnostics</body></html>"
                load_result = await or_client._execute_browser_tool("load_html", {"html_content": html})
                if not (isinstance(load_result, dict) and load_result.get("ok") is True):
                    return False, f"load_html failed: {load_result}"
                if not isinstance(load_result.get("duration_ms"), int):
                    return False, "load_html did not return duration_ms"

                logs_all = await or_client._execute_browser_tool("list_console_messages", {})
                if len(logs_all.get("messages", [])) < 4:
                    return False, "expected initial console messages"

                logs_error = await or_client._execute_browser_tool("list_console_messages", {"level": "error"})
                msgs_error = logs_error.get("messages", [])
                if len(msgs_error) != 1 or "ERROR" not in str(msgs_error[0]):
                    return False, f"error filter mismatch: {msgs_error}"

                for key in ("a", "ArrowUp", "Space"):
                    press_result = await or_client._execute_browser_tool("press_key", {"key": key})
                    if press_result.get("ok") is not True:
                        return False, f"press_key failed for {key}: {press_result}"

                logs_log = await or_client._execute_browser_tool("list_console_messages", {"level": "log"})
                log_entries = logs_log.get("messages", [])
                if len(log_entries) < 4:
                    return False, f"missing keydown log entries: {log_entries}"

                eval_var = await or_client._execute_browser_tool("evaluate_script", {"script": "window.testVar"})
                if eval_var.get("result") != "⭐ Success from evaluate_script!":
                    return False, f"unexpected evaluate_script testVar: {eval_var}"

                eval_count = await or_client._execute_browser_tool("evaluate_script", {"script": "window.testObj.count"})
                if eval_count.get("result") != 3:
                    return False, f"count not incremented to 3: {eval_count}"

                eval_last = await or_client._execute_browser_tool("evaluate_script", {"script": "window.testObj.lastKey"})
                if eval_last.get("result") != " ":
                    return False, f"lastKey not tracked as space: {eval_last}"

                wait_title = await or_client._execute_browser_tool("wait_for", {"selector": "#title", "timeout_ms": 1000})
                wait_late = await or_client._execute_browser_tool("wait_for", {"selector": "#late-element", "timeout_ms": 5000})
                if not (wait_title.get("ok") and wait_late.get("ok")):
                    return False, f"wait_for did not succeed: {wait_title}, {wait_late}"

                perf_start = await or_client._execute_browser_tool("performance_start_trace", {})
                if perf_start.get("ok") is not True:
                    return False, f"performance_start_trace failed: {perf_start}"
                perf_stop = await or_client._execute_browser_tool("performance_stop_trace", {})
                if not isinstance(perf_stop.get("result"), dict) or "fps" not in perf_stop["result"]:
                    return False, f"performance_stop_trace missing fps: {perf_stop}"

                analysis = await or_client._execute_browser_tool("analyze_screen", {"query": "describe initial"})
                if not analysis.get("analysis"):
                    return False, f"analyze_screen missing analysis: {analysis}"
                if not analysis.get("console_logs"):
                    return False, f"analyze_screen missing console logs: {analysis}"
                if not any("Page loaded" in entry for entry in analysis.get("console_logs", [])):
                    return False, f"analyze_screen console logs unexpected: {analysis.get('console_logs')}"

                try:
                    vision_mock.assert_awaited_once()
                except AssertionError as exc:
                    return False, f"vision_single not awaited: {exc}"

                return True, "All devtools tools validated end-to-end"
    finally:
        context_data.restore_context(token)


async def main() -> int:
    checks = [
        ("DevTools full tools flow", test_full_tools_flow),
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
