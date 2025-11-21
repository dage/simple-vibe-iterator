from __future__ import annotations

"""
Headless reproduction helper for analyze_screen behavior.

This script runs a single IterationController transition using the real
OpenRouter-backed services and the Chrome DevTools browser service,
with auto-logging enabled, so that logs/auto_logger.jsonl and
logs/tool_calls.jsonl capture the full call stack.

It uses the prompt discussed in development:

  "Display Hi. Use the new analyze_screen tool EXACTLY ONE invocation
   only and then write the vision analysis results in the static html
   in the final response."

and pins:
  - code model:  x-ai/grok-4.1-fast
  - vision model: qwen/qwen3-vl-8b-instruct

Prerequisites (run from project root):
  - .env with OPENROUTER_API_KEY and OPENROUTER_BASE_URL
  - Chrome DevTools MCP configured via .mcp/chrome-devtools.json

Usage:
  python scripts/repro_analyze_screen_hang.py
"""

import asyncio
import os
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src import config as app_config  # noqa: E402
from src.controller import IterationController  # noqa: E402
from src.interfaces import TransitionSettings  # noqa: E402
from src.logging import start_auto_logger  # noqa: E402
from src.services import (  # noqa: E402
    DevToolsBrowserService,
    OpenRouterAICodeService,
    OpenRouterVisionService,
)


PROMPT = (
    "Display Hi. Use the new analyze_screen tool EXACTLY ONE invocation only "
    "and then write the vision analysis results in the static html in the final response."
)


async def _run_once(timeout_seconds: float = 300.0) -> None:
    # Ensure logs are enabled and the auto-logger is active.
    os.environ.setdefault("APP_ENABLE_JSONL_LOGS", "1")
    os.environ.setdefault("AUTO_LOGGER_ENABLED", "1")
    start_auto_logger()

    cfg = app_config.get_config()

    code_model = "x-ai/grok-4.1-fast"
    vision_model = "qwen/qwen3-vl-8b-instruct"

    settings = TransitionSettings(
        code_model=code_model,
        vision_model=vision_model,
        overall_goal=PROMPT,
        user_feedback="",
        code_template=cfg.code_template,
        vision_template=cfg.vision_template,
        code_system_prompt_template=cfg.code_system_prompt_template,
        code_first_prompt_template=cfg.code_first_prompt_template,
        input_screenshot_count=1,
        feedback_preset_id=None,
    )

    ai_service = OpenRouterAICodeService()
    browser_service = DevToolsBrowserService()
    vision_service = OpenRouterVisionService()

    controller = IterationController(ai_service, browser_service, vision_service)

    print(f"Starting single transition with code model {code_model!r} and vision model {vision_model!r}")
    print(f"Overall goal:\n{PROMPT}\n")

    started = time.monotonic()
    try:
        node_id = await asyncio.wait_for(
            controller.apply_transition(None, settings),
            timeout=timeout_seconds,
        )
        elapsed = time.monotonic() - started
        print(f"Transition completed in {elapsed:.1f}s; node_id={node_id}")
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - started
        print(f"Transition timed out after {elapsed:.1f}s without completing.")

    print("Inspect logs/auto_logger.jsonl and logs/tool_calls.jsonl for details.")


def main() -> None:
    # Run from project root to keep paths consistent.
    os.chdir(PROJECT_ROOT)
    asyncio.run(_run_once())


if __name__ == "__main__":
    main()

