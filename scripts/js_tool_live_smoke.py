#!/usr/bin/env python3
"""Manual smoke test to validate the JavaScript tool via Grok-4-fast."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")


async def main() -> None:
    from src import or_client

    model = os.getenv("JS_TOOL_TEST_MODEL", os.getenv("CODE_MODEL", "x-ai/grok-4-fast"))

    scenario = (
        "Explain in one paragraph how to compute the sum of cubes of the first N integers in JavaScript."
        " Then you MUST call the evaluate_javascript tool with the exact code snippet"
        " (() => { let total = 0; for (let i = 1; i <= 20; i++) { total += i * i * i; } return total; })();"
        " so we can capture the numeric output. After the tool call, restate the precise number returned by the tool."
    )

    messages = [
        {
            "role": "system",
            "content": "You have access to evaluate_javascript for verifying code. Always use it if asked.",
        },
        {
            "role": "user",
            "content": scenario,
        },
    ]

    content, meta = await or_client.chat_with_meta(messages=messages, model=model)

    print("\n=== Model Response ===\n")
    print(content)

    tool_messages = [m for m in meta.get("messages", []) if m.get("role") == "tool"]
    if tool_messages:
        print("\n=== Tool Outputs ===\n")
        for idx, msg in enumerate(tool_messages, 1):
            payload = msg.get("content")
            try:
                parsed = json.loads(payload)
            except Exception:
                parsed = payload
            print(f"Call {idx}: {json.dumps(parsed, indent=2) if isinstance(parsed, dict) else parsed}")
    else:
        print("\n⚠️ No tool calls detected. Inspect meta messages for troubleshooting.\n")

    print("\n=== Conversation Log ===\n")
    for msg in meta.get("messages", []):
        role = msg.get("role")
        name = msg.get("name")
        summary = msg.get("content")
        print(f"[{role}{'/' + name if name else ''}] {summary}")


if __name__ == "__main__":
    asyncio.run(main())
