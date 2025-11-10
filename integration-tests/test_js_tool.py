#!/usr/bin/env python3
"""Quick smoke tests for the JS code interpreter tool."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src import js_tool


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_js_tool_sum_of_cubes():
    code = "(() => { let total = 0; for (let i = 1; i <= 20; i++) { total += i * i * i; } return total; })();"
    payload = run(js_tool.execute_tool(code))
    data = json.loads(payload)
    assert data["result"] == 44100


def test_js_tool_console_log():
    payload = run(js_tool.execute_tool("(() => { console.log('hi'); return 1; })();"))
    data = json.loads(payload)
    assert data["result"] == 1
    assert "hi" in data["console"][0]
