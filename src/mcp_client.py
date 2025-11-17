"""Lightweight Model Context Protocol (MCP) stdio client."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from asyncio.subprocess import Process
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)


class MCPClient:
    """Minimal JSON-RPC client for stdio-based MCP servers."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        env: Optional[Dict[str, str]] = None,
        client_name: str = "simple-vibe-iterator",
        client_version: str = "0.0.0",
    ) -> None:
        if not command:
            raise ValueError("command is required to launch MCP server")
        self._command: List[str] = list(command)
        self._cwd = cwd
        self._env = dict(env or {})
        self._client_name = client_name
        self._client_version = client_version
        self._proc: Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._text_buffer: str = ""
        self._request_id = 0
        self._initialized = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._proc and self._proc.returncode is None:
                return
            logger.info("Starting MCP server: %s", " ".join(self._command))
            env = os.environ.copy()
            env.update(self._env)
            homebrew_path = "/opt/homebrew/bin"
            path = env.get("PATH", "")
            if homebrew_path and homebrew_path not in path and Path(homebrew_path).exists():
                env["PATH"] = f"{homebrew_path}:{path}" if path else homebrew_path
            self._proc = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env=env,
            )
            self._reader = self._proc.stdout
            self._writer = self._proc.stdin
            self._initialized = False
            await self._initialize()

    async def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        await self.start()
        return await self._rpc_call(method, params or {})

    async def close(self) -> None:
        proc = self._proc
        if not proc:
            return
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass
        proc = self._proc
        if proc is not None:
            transport = getattr(proc, "_transport", None)
            if transport is not None:
                with contextlib.suppress(Exception):
                    transport.close()
        self._proc = None
        writer = self._writer
        if writer is not None:
            transport = getattr(writer, "transport", None)
            if transport is not None:
                with contextlib.suppress(Exception):
                    transport.close()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        reader = self._reader
        if reader is not None:
            transport = getattr(reader, "_transport", None)
            if transport is not None:
                with contextlib.suppress(Exception):
                    transport.close()
        self._reader = None
        self._writer = None
        self._initialized = False

    async def _initialize(self) -> None:
        params = {
            "protocolVersion": "0.1.0",
            "clientInfo": {
                "name": self._client_name,
                "version": self._client_version,
            },
            "capabilities": {
                "roots": {},
                "tools": {},
            },
        }
        await self._rpc_call("initialize", params, expect_initialized=False)
        self._initialized = True

    async def _rpc_call(
        self,
        method: str,
        params: Dict[str, Any],
        *,
        expect_initialized: bool = True,
    ) -> Any:
        if expect_initialized and not self._initialized:
            raise RuntimeError("MCP client not initialized")
        self._request_id += 1
        req_id = self._request_id
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        await self._send(payload)
        while True:
            message = await self._read_message()
            if message is None:
                raise RuntimeError("MCP server closed connection")
            if message.get("id") != req_id:
                self._handle_notification(message)
                continue
            if "error" in message:
                raise RuntimeError(str(message["error"]))
            return message.get("result")

    async def _send(self, payload: Dict[str, Any]) -> None:
        if not self._writer:
            raise RuntimeError("MCP writer unavailable")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = (
            f"Content-Length: {len(data)}\r\n"
            "Content-Type: application/json\r\n\r\n"
        ).encode("ascii")
        self._writer.write(header)
        self._writer.write(data + b"\r\n")
        await self._writer.drain()

    async def _read_message(self) -> Optional[Dict[str, Any]]:
        reader = self._reader
        if reader is None:
            return None
        decoder = json.JSONDecoder()
        while True:
            message = self._consume_buffer(decoder)
            if message is not None:
                return message
            chunk = await reader.read(4096)
            if not chunk:
                return None
            self._text_buffer += chunk.decode("utf-8", errors="ignore")

    def _consume_buffer(self, decoder: json.JSONDecoder) -> Optional[Dict[str, Any]]:
        text = self._trim_noise(self._text_buffer)
        self._text_buffer = text
        if not text:
            return None
        lowered = text.lower()
        if lowered.startswith("content-length"):
            header_end = text.find("\r\n\r\n")
            if header_end == -1:
                self._text_buffer = text
                return None
            header = text[:header_end]
            rest = text[header_end + 4 :]
            length = self._parse_content_length(header)
            if length is None or len(rest) < length:
                self._text_buffer = text
                return None
            body = rest[:length]
            self._text_buffer = rest[length:]
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                logger.error("Failed to decode MCP response: %s", body)
                return None
        stripped = text.lstrip()
        offset = len(text) - len(stripped)
        try:
            obj, end = decoder.raw_decode(stripped)
        except json.JSONDecodeError:
            self._text_buffer = text
            return None
        total_end = offset + end
        self._text_buffer = text[total_end:]
        return obj

    @staticmethod
    def _parse_content_length(header: str) -> Optional[int]:
        for line in header.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.strip().lower() == "content-length":
                try:
                    return int(value.strip())
                except ValueError:
                    return None
        return None

    @staticmethod
    def _trim_noise(text: str) -> str:
        trimmed = text
        while trimmed and not trimmed.lstrip().startswith(("Content-Length", "{", "[")):
            newline = trimmed.find("\n")
            if newline == -1:
                return ""
            trimmed = trimmed[newline + 1 :]
        return trimmed

    @staticmethod
    def _parse_headers(raw: str) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        for line in raw.split("\r\n"):
            if not line.strip():
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        return headers

    def _handle_notification(self, message: Dict[str, Any]) -> None:
        if not message:
            return
        method = message.get("method")
        if method:
            logger.debug("MCP notification: %s", method)


def build_command(command: str, args: Iterable[str] | None = None) -> List[str]:
    parts = [command]
    if args:
        parts.extend(str(a) for a in args)
    return parts
