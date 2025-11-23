"""Chrome DevTools MCP service wrapper."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

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
    _max_tool_attempts: int = field(default=2, init=False)

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

    async def load_html_mcp(self, html: str) -> Dict[str, Any]:
        """Replace the page contents with provided HTML using document.write."""
        started = time.monotonic()
        if not html:
            html = "<!DOCTYPE html><title>Empty</title><body></body>"
        escaped = json.dumps(html)
        fn = (
            "() => {"
            f"  const html = {escaped};"
            "  delete window.__sviWaitForLoad;"
            "  document.open();"
            "  document.write(html);"
            "  document.close();"
            "  return true;"
            "}"
        )
        await self._install_console_capture(reset_logs=True)
        result = await self.evaluate_script_mcp(fn, is_function=True)
        await self._wait_for_page_ready()
        await self._install_console_capture(reset_logs=False)
        duration_ms = int((time.monotonic() - started) * 1000)
        ok = bool(result) if isinstance(result, bool) else True
        return {"ok": ok, "duration_ms": duration_ms}

    async def take_screenshot_mcp(self) -> Optional[str]:
        if not self.enabled:
            return None
        await self._wait_for_page_ready()
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

    async def _install_console_capture(self, *, reset_logs: bool = False) -> None:
        reset_literal = "true" if reset_logs else "false"
        script = (
            "() => {"
            f" const resetLogs = {reset_literal};"
            " const stringify = (value) => {"
            "   if (typeof value === 'string') { return value; }"
            "   try { return JSON.stringify(value); } catch (err) { return String(value); }"
            " };"
            " const levels = ['log','info','warn','error'];"
            " const ensureBuffer = () => {"
            "   if (!Array.isArray(window.__sviLogs)) {"
            "     window.__sviLogs = [];"
            "   } else if (resetLogs) {"
            "     window.__sviLogs.length = 0;"
            "   }"
            " };"
            " const pushEntry = (level, args) => {"
            "   try {"
            "     const items = Array.isArray(args) ? args : Array.from(args || []);"
            "     const message = items.map(stringify).join(' ');"
            "     const entry = { level, message };"
            "     if (typeof Date !== 'undefined' && typeof Date.now === 'function') {"
            "       entry.timestamp = Date.now();"
            "     }"
            "     window.__sviLogs.push(entry);"
            "   } catch (err) {"
            "     try { window.__sviLogs.push({ level, message: '[capture-error] ' + err }); } catch (ignore) {}"
            "   }"
            " };"
            " ensureBuffer();"
            " if (!window.__sviConsoleOriginals) { window.__sviConsoleOriginals = {}; }"
            " levels.forEach((level) => {"
            "   const originals = window.__sviConsoleOriginals;"
            "   if (!originals[level]) { originals[level] = console[level]; }"
            "   const original = originals[level];"
            "   console[level] = (...args) => {"
            "     pushEntry(level, args);"
            "     try {"
            "       if (original && original.apply) { original.apply(console, args); }"
            "       else if (typeof original === 'function') { original(...args); }"
            "     } catch (err) { }"
            "   };"
            " });"
            " window.__sviLogs.__patched = true;"
            " return true;"
            "}"
        )
        try:
            await self.evaluate_script_mcp(script, is_function=True)
        except Exception:
            logger.exception("Failed to install console capture hook")

    async def _wait_for_page_ready(self, *, timeout_ms: int = 10000, extra_delay_s: float = 0.5) -> bool:
        delay_ms = max(0, int(extra_delay_s * 1000))
        timeout = max(0, int(timeout_ms))
        script = (
            "() => {"
            f"  const timeoutMs = {timeout};"
            f"  const extraDelayMs = {delay_ms};"
            "  if (window.__sviWaitForLoad) { return window.__sviWaitForLoad; }"
            "  const hasRenderableDom = () => {"
            "    return !!document.querySelector('canvas, svg, video, img, body > *:not(script):not(style)');"
            "  };"
            "  let loadDone = document.readyState === 'complete';"
            "  let renderDone = hasRenderableDom();"
            "  let settle;"
            "  window.__sviWaitForLoad = new Promise((resolve) => {"
            "    let finished = false;"
            "    settle = () => {"
            "      if (finished) { return; }"
            "      finished = true;"
            "      resolve(true);"
            "    };"
            "    const maybeDone = () => { if (loadDone && renderDone) { settle(); } };"
            "    if (renderDone && loadDone) { settle(); return; }"
            "    const observer = new MutationObserver(() => {"
            "      if (renderDone) { return; }"
            "      renderDone = hasRenderableDom();"
            "      maybeDone();"
            "    });"
            "    observer.observe(document.documentElement || document, { childList: true, subtree: true });"
            "    window.addEventListener('load', () => { loadDone = true; maybeDone(); }, { once: true });"
            "    const poll = setInterval(() => {"
            "      if (renderDone) { return; }"
            "      renderDone = hasRenderableDom();"
            "      maybeDone();"
            "    }, 50);"
            "    setTimeout(() => {"
            "      clearInterval(poll);"
            "      observer.disconnect();"
            "      settle();"
            "    }, timeoutMs);"
            "  })"
            "    .then(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))))"
            "    .then(() => new Promise((resolve) => {"
            "      if (extraDelayMs <= 0) { resolve(true); return; }"
            "      setTimeout(resolve, extraDelayMs);"
            "    }))"
            "    .then(() => true)"
            "    .finally(() => { window.__sviWaitForLoad = null; });"
            "  return window.__sviWaitForLoad;"
            "}"
        )
        result = await self.evaluate_script_mcp(script, is_function=True)
        return bool(result) if isinstance(result, bool) else True

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

    async def wait_for_selector_mcp(self, selector: str, timeout_ms: int = 5000) -> Dict[str, Any]:
        started = time.monotonic()
        payload = {
            "selector": selector,
            "timeout_ms": max(0, int(timeout_ms)),
        }
        result = await self._call_tool("wait_for", payload)
        ok = self._extract_bool(result)
        duration_ms = int((time.monotonic() - started) * 1000)
        status = self._extract_status(result, ok)
        return {"ok": ok, "duration_ms": duration_ms, "status": status}

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

        client: MCPClient | None
        async with self._client_lock:
            client = self._client
        if client and not self._client_is_alive(client):
            await self._close_client()

        async with self._client_lock:
            if self._client is None:
                self._client = MCPClient(self._command)
            client = self._client

        if client is None:
            return None

        try:
            await client.start()
            return client
        except Exception:
            await self._close_client()
            raise

    async def aclose(self) -> None:
        await self._close_client()

    async def _call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        last_exc: Exception | None = None
        timeout = max(5.0, float(self.call_timeout))
        for attempt in range(self._max_tool_attempts):
            try:
                return await self._call_tool_once(name, arguments, timeout)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._is_recoverable_exception(exc):
                    raise
                last_exc = exc
                logger.warning(
                    "Chrome DevTools tool '%s' failed (attempt %d/%d): %s; restarting MCP session",
                    name,
                    attempt + 1,
                    self._max_tool_attempts,
                    exc,
                )
                await self._restart_client()
                if attempt == self._max_tool_attempts - 1:
                    logger.error(
                        "Chrome DevTools tool '%s' failed after %d attempts: %s",
                        name,
                        self._max_tool_attempts,
                        exc,
                    )
                    raise
                await asyncio.sleep(0.1)
        assert last_exc is not None
        raise last_exc

    async def _call_tool_once(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]],
        timeout: float,
    ) -> Any:
        client = await self._ensure_client()
        if client is None:
            raise RuntimeError("Chrome DevTools MCP client unavailable")
        params = {
            "name": name,
            "arguments": arguments or {},
        }
        return await asyncio.wait_for(client.request("tools/call", params), timeout=timeout)

    @staticmethod
    def _is_recoverable_exception(exc: Exception) -> bool:
        recoverable_types = (
            asyncio.TimeoutError,
            BrokenPipeError,
            ConnectionResetError,
            EOFError,
            asyncio.IncompleteReadError,
        )
        if isinstance(exc, recoverable_types):
            return True
        if isinstance(exc, RuntimeError):
            text = str(exc).lower()
            keywords = ("mcp server", "read() called", "timeout after")
            if any(keyword in text for keyword in keywords):
                return True
        return False

    @staticmethod
    def _client_is_alive(client: MCPClient | None) -> bool:
        if client is None:
            return False
        proc = getattr(client, "_proc", None)
        if proc is None:
            return False
        if proc.returncode is not None:
            return False
        reader = getattr(proc, "stdout", None)
        if reader and getattr(reader, "at_eof", lambda: False)():
            return False
        return True

    async def _restart_client(self) -> None:
        await self._close_client()

    async def _close_client(self) -> None:
        async with self._client_lock:
            client = self._client
            self._client = None
        if client is None:
            return
        try:
            await client.close()
        except Exception as exc:  # pragma: no cover - defensive close
            logger.warning("Failed to close Chrome DevTools MCP client: %s", exc)

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

    @staticmethod
    def _extract_status(result: Any, ok: bool) -> str:
        if isinstance(result, dict):
            status = result.get("status")
            if isinstance(status, str) and status:
                return status
            if result.get("timed_out") is True:
                return "timed_out"
            if result.get("error"):
                return "error"
        return "ok" if ok else "timed_out"


def create_chrome_devtools_service(
    mcp_config_path: str = ".mcp/chrome-devtools.json",
    enabled: bool = True,
) -> ChromeDevToolsService:
    return ChromeDevToolsService(mcp_config_path=mcp_config_path, enabled=enabled)


class ChromeDevToolsSessionManager:
    """Allocates isolated Chrome DevTools instances per agent."""

    def __init__(self, factory: Callable[[], ChromeDevToolsService] | None = None) -> None:
        self._factory = factory or ChromeDevToolsService
        self._sessions: Dict[str, ChromeDevToolsService] = {}
        self._lock = asyncio.Lock()

    async def get_session(self, agent_id: str) -> ChromeDevToolsService:
        if not agent_id:
            raise ValueError("agent_id is required for Chrome DevTools session")
        async with self._lock:
            service = self._sessions.get(agent_id)
            if service is None:
                service = self._factory()
                self._sessions[agent_id] = service
            return service

    async def release_session(self, agent_id: str) -> None:
        if not agent_id:
            return
        async with self._lock:
            service = self._sessions.pop(agent_id, None)
        if service is None:
            return
        try:
            await service.aclose()
        except Exception as exc:  # pragma: no cover - defensive cleanup
            logger.warning("Error closing Chrome DevTools session %s: %s", agent_id, exc)


_CURRENT_AGENT_ID: ContextVar[Optional[str]] = ContextVar("chrome_devtools_agent_id", default=None)
_SESSION_MANAGER: ChromeDevToolsSessionManager | None = None


def get_chrome_devtools_session_manager() -> ChromeDevToolsSessionManager:
    global _SESSION_MANAGER
    if _SESSION_MANAGER is None:
        _SESSION_MANAGER = ChromeDevToolsSessionManager()
    return _SESSION_MANAGER


def get_current_devtools_agent_id() -> Optional[str]:
    return _CURRENT_AGENT_ID.get()


@asynccontextmanager
async def bind_chrome_devtools_agent(agent_id: str | None):
    """Bind Chrome DevTools calls within the context to a dedicated session."""

    if not agent_id:
        yield None
        return
    manager = get_chrome_devtools_session_manager()
    token: Token[Optional[str]] = _CURRENT_AGENT_ID.set(agent_id)
    await manager.get_session(agent_id)
    try:
        yield agent_id
    finally:
        _CURRENT_AGENT_ID.reset(token)
        await manager.release_session(agent_id)
