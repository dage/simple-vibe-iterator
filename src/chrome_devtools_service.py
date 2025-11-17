"""Chrome DevTools MCP service wrapper."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .mcp_client import MCPClient, build_command

logger = logging.getLogger(__name__)


@dataclass
class ChromeDevToolsService:
    """Wrapper for Chrome DevTools MCP interactions."""

    mcp_config_path: str = ".mcp/chrome-devtools.json"
    enabled: bool = True
    server_name: str = "chrome-devtools"
    call_timeout: float = 90.0
    _command: Optional[List[str]] = field(default=None, init=False)
    _client: Optional[MCPClient] = field(default=None, init=False)
    _client_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self) -> None:
        config_file = Path(self.mcp_config_path)
        if not config_file.exists() and self.enabled:
            logger.warning("Chrome DevTools MCP config not found at %s", self.mcp_config_path)
            logger.info("Chrome DevTools tools remain disabled until configured")
            self.enabled = False
            return
        if not self.enabled:
            return
        try:
            with config_file.open("r", encoding="utf-8") as handle:
                config_data = json.load(handle)
        except Exception as exc:
            logger.error("Failed to read MCP config: %s", exc)
            self.enabled = False
            return
        entry = (config_data.get("mcpServers") or {}).get(self.server_name)
        if not entry:
            logger.error("MCP config missing server entry '%s'", self.server_name)
            self.enabled = False
            return
        command = entry.get("command")
        args = entry.get("args") or []
        if not command:
            logger.error("MCP server entry missing 'command'")
            self.enabled = False
            return
        self._command = build_command(command, args)
        logger.info("Chrome DevTools MCP configured with command: %s", " ".join(self._command))

    async def load_html_mcp(self, html: str) -> bool:
        """Replace the page contents with provided HTML using document.write."""
        if not html:
            html = "<!DOCTYPE html><title>Empty</title><body></body>"
        instrument = """
<script>
(function(){
  window.__sviLogs = [];
  const stringify = (value) => {
    if (typeof value === 'string') { return value; }
    try { return JSON.stringify(value); } catch (err) { return String(value); }
  };
  ['log','info','warn','error'].forEach((level) => {
    const original = console[level];
    console[level] = (...args) => {
      const message = args.map(stringify).join(' ');
      window.__sviLogs.push({ level, message });
      if (original && original.apply) { original.apply(console, args); }
    };
  });
})();
</script>
""".strip()
        escaped = json.dumps(instrument + html)
        fn = (
            "() => {"
            "  document.open();"
            f"  document.write({escaped});"
            "  document.close();"
            "  return true;"
            "}"
        )
        result = await self.evaluate_script_mcp(fn, is_function=True)
        await self.evaluate_script_mcp(
            "() => {"
            " const root = document.documentElement; "
            " if (root && !root.style.backgroundColor) { root.style.backgroundColor = '#ffffff'; }"
            " let body = document.body;"
            " if (!body) {"
            "   body = document.createElement('body');"
            "   if (root) { root.appendChild(body); }"
            " }"
            " if (body && !body.style.backgroundColor) { body.style.backgroundColor = '#ffffff'; }"
            " if (body && !body.style.color) { body.style.color = '#000000'; }"
            " return true;"
            "}",
            is_function=True,
        )
        await self.evaluate_script_mcp(
            "(async () => { await new Promise(requestAnimationFrame); return true; })()",
            is_function=True,
        )
        return bool(result) if isinstance(result, bool) else True

    async def take_screenshot_mcp(self) -> Optional[str]:
        result = await self._call_tool("take_screenshot")
        image = self._extract_field(result, "content")
        if isinstance(image, list):
            for part in image:
                if isinstance(part, dict) and part.get("type") == "image":
                    data = part.get("data")
                    if isinstance(data, str) and data:
                        mime = part.get("mimeType", "image/png")
                        return f"data:{mime};base64,{data}"
        return None

    async def get_console_messages_mcp(self, level: Optional[str] = None) -> List[Dict[str, Any]]:
        console_data = await self.evaluate_script_mcp("() => window.__sviLogs || []", is_function=True)
        if not isinstance(console_data, list):
            return []
        if level:
            return [entry for entry in console_data if entry.get("level") == level]
        return console_data

    async def evaluate_script_mcp(self, script: str, *, is_function: bool = False) -> Any:
        payload_script = self._format_function(script) if not is_function else script
        result = await self._call_tool("evaluate_script", {"function": payload_script})
        if isinstance(result, dict):
            if "result" in result:
                return result["result"]
            content = result.get("content")
            if isinstance(content, list) and content:
                for entry in content:
                    text = entry.get("text") if isinstance(entry, dict) else None
                    parsed = self._parse_content_json(text)
                    if parsed is not None:
                        return parsed
                return content[0]
        return result

    @staticmethod
    def _parse_content_json(text: Any) -> Any:
        if not isinstance(text, str):
            return None
        if "```json" in text:
            start = text.find("```json") + len("```json")
            end = text.find("```", start)
            snippet = text[start:end].strip()
        else:
            snippet = text.strip()
        if not snippet:
            return None
        try:
            return json.loads(snippet)
        except Exception:
            return None

    async def get_network_requests_mcp(self, filter_url: Optional[str] = None) -> List[Dict[str, Any]]:
        result = await self._call_tool("list_network_requests")
        value = self._extract_field(result, "requests")
        entries = value if isinstance(value, list) else []
        if filter_url:
            return [entry for entry in entries if filter_url in str(entry.get("url", ""))]
        return entries

    async def press_key_mcp(self, key: str, duration_ms: int = 100) -> bool:
        normalized = (key or "").strip()
        if not normalized:
            return False
        hold_ms = max(0, int(duration_ms or 0))
        if hold_ms <= 200:
            result = await self._call_tool("press_key", {"key": normalized})
            return self._extract_bool(result)
        return await self._press_key_with_hold(normalized, hold_ms)

    async def _press_key_with_hold(self, key: str, duration_ms: int) -> bool:
        hold = max(50, duration_ms)
        key_literal = json.dumps(key)
        script = (
            "() => {"
            " const raw = (" + key_literal + ").trim();"
            " if (!raw) { return false; }"
            " const lower = raw.toLowerCase();"
            " const isSpace = lower === 'space' || lower === 'spacebar';"
            " const keyValue = isSpace ? ' ' : raw;"
            " const code = isSpace ? 'Space' : (keyValue.length === 1 ? ((/[0-9]/.test(keyValue)) ? `Digit${keyValue}` : ((/[a-z]/i.test(keyValue)) ? `Key${keyValue.toUpperCase()}` : keyValue)) : raw);"
            " const keyCode = keyValue === ' ' ? 32 : (keyValue.length === 1 ? keyValue.toUpperCase().charCodeAt(0) : 0);"
            " const init = { key: keyValue, code, keyCode, which: keyCode, bubbles: true, cancelable: true, composed: true };"
            " const target = document.activeElement || document.body || document;"
            " if (!target) { return false; }"
            " const dispatch = (type) => {"
            "   const event = new KeyboardEvent(type, init);"
            "   target.dispatchEvent(event);"
            " };"
            " if (!Array.isArray(window.__sviActiveKeys)) { window.__sviActiveKeys = []; }"
            " window.__sviActiveKeys.push({ key: raw, releaseAt: performance.now() + " + str(hold) + " });"
            " const release = () => {"
            "   dispatch('keyup');"
            "   window.__sviActiveKeys = window.__sviActiveKeys.filter((entry) => entry.key !== raw);"
            " };"
            " dispatch('keydown');"
            " dispatch('keypress');"
            " setTimeout(release, " + str(hold) + ");"
            " if (console && console.info) { console.info(`[devtools] key ${raw} held for " + str(hold) + "ms`); }"
            " return true;"
            "}"
        )
        result = await self.evaluate_script_mcp(script, is_function=True)
        await asyncio.sleep(min(hold, 250) / 1000.0)
        return bool(result)

    async def wait_for_selector_mcp(self, selector: str, timeout_ms: int = 5000) -> bool:
        result = await self._call_tool(
            "wait_for",
            {
                "text": selector,
                "timeout": timeout_ms,
            },
        )
        return self._extract_bool(result)

    async def performance_trace_start_mcp(self) -> bool:
        result = await self._call_tool("performance_start_trace")
        return self._extract_bool(result)

    async def performance_trace_stop_mcp(self) -> Optional[Dict[str, Any]]:
        result = await self._call_tool("performance_stop_trace")
        if isinstance(result, dict):
            return result
        return None

    @staticmethod
    def _format_function(script: str) -> str:
        stripped = script.strip()
        if not stripped:
            return "() => { return null; }"
        header = stripped.split("\n", 1)[0].strip()
        if header.startswith("(") or header.startswith("async") or header.startswith("function") or "=>" in header:
            return stripped
        return f"() => {{ return ({stripped}); }}"

    async def _ensure_client(self) -> Optional[MCPClient]:
        if not self.enabled or not self._command:
            return None
        async with self._client_lock:
            if self._client is None:
                self._client = MCPClient(self._command)
        await self._client.start()
        return self._client

    async def _call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        client = await self._ensure_client()
        if client is None:
            return None
        try:
            params = {
                "name": name,
                "arguments": arguments or {},
            }
            return await asyncio.wait_for(
                client.request("tools/call", params),
                timeout=max(5.0, float(self.call_timeout)),
            )
        except asyncio.TimeoutError:
            logger.error("Chrome DevTools tool '%s' timed out after %.1fs", name, self.call_timeout)
            return {"error": f"timeout after {self.call_timeout}s"}
        except Exception as exc:  # pragma: no cover - protective logging
            logger.error("Chrome DevTools tool '%s' failed: %s", name, exc)
            return {"error": str(exc)}

    @staticmethod
    def _extract_field(result: Any, key: str) -> Any:
        if isinstance(result, dict):
            if key in result:
                return result[key]
            content = result.get("content")
            if isinstance(content, dict) and key in content:
                return content[key]
            if isinstance(content, list):
                for entry in content:
                    if isinstance(entry, dict) and key in entry:
                        return entry[key]
        return result

    @staticmethod
    def _extract_bool(result: Any) -> bool:
        if isinstance(result, dict):
            if "ok" in result:
                return bool(result["ok"])
            if "success" in result:
                return bool(result["success"])
        return bool(result)


def create_chrome_devtools_service(
    mcp_config_path: str = ".mcp/chrome-devtools.json",
    enabled: bool = True,
) -> ChromeDevToolsService:
    return ChromeDevToolsService(mcp_config_path=mcp_config_path, enabled=enabled)
