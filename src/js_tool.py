# src/js_tool.py
"""Javascript code interpreter tool powered by QuickJS."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
from typing import Dict, List

import quickjs


TOOL_NAME = "evaluate_javascript"
_TIME_LIMIT_MS = 1500
_MEMORY_LIMIT_BYTES = 8 * 1024 * 1024  # 8MB sandbox per execution


@dataclass
class JsExecutionResult:
    result: object
    console: List[str]


def _stringify_value(value):
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    json_method = getattr(value, "json", None)
    if callable(json_method):
        try:
            return json.loads(json_method())
        except Exception:
            return json_method()
    return str(value)


def evaluate(code: str) -> JsExecutionResult:
    if not isinstance(code, str) or not code.strip():
        raise ValueError("JavaScript code must be a non-empty string")

    ctx = quickjs.Context()
    ctx.set_time_limit(_TIME_LIMIT_MS)
    ctx.set_memory_limit(_MEMORY_LIMIT_BYTES)

    ctx.eval(
        """
        globalThis.__sviLogs = [];
        function __fmt(args) {
          return Array.prototype.map.call(args, function(v) { return String(v); }).join(' ');
        }
        globalThis.console = {
          log: function() { globalThis.__sviLogs.push(__fmt(arguments)); },
          error: function() { globalThis.__sviLogs.push('ERROR: ' + __fmt(arguments)); }
        };
        """
    )

    try:
        value = ctx.eval(code)
    except quickjs.JSException as exc:
        raise RuntimeError(f"JavaScriptError: {exc}") from exc

    logs_value = ctx.eval("globalThis.__sviLogs")
    logs = _stringify_value(logs_value) or []
    if not isinstance(logs, list):
        logs = [str(logs)]
    return JsExecutionResult(result=_stringify_value(value), console=list(logs))


JS_TOOL_SPEC: Dict[str, Dict[str, object]] = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Execute JavaScript to validate snippets, returning the result string and console output.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "JavaScript source code to run. Should include the return expression you want evaluated.",
                }
            },
            "required": ["code"],
        },
    },
}


async def execute_tool(code: str) -> str:
    def _run() -> str:
        result = evaluate(code)
        payload = {
            "result": result.result,
            "console": result.console,
        }
        return json.dumps(payload, ensure_ascii=False)

    return await asyncio.to_thread(_run)
