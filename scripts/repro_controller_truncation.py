from __future__ import annotations

"""
Reproduce short-output issues through the full IterationController for multiple models.

This drives the controller exactly like the app does (same templates, services, and
TransitionSettings) for each configured code model to see whether completions stop early.
It prints lengths and saves the assistant response and message meta per model.

Usage (from repo root):
  python scripts/repro_controller_truncation.py

Prereqs:
  - .env with OPENROUTER_API_KEY and OPENROUTER_BASE_URL
  - Chrome DevTools MCP configured if you expect screenshots (first iteration has no HTML so none are taken)
"""

import asyncio
import os
import sys
import textwrap
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

# Models to probe (run via controller) – grok + deepseek
CODE_MODELS = [
    "x-ai/grok-4.1-fast",
    "deepseek/deepseek-v3.2-exp",
]

# Toggle this to reproduce the user-reported Mandelbrot prompt directly.
USE_MANDELBROT_PROMPT = True
USE_TOOL_FREE_PROMPT = os.getenv("REPRO_TOOL_FREE", "").strip().lower() in {"1", "true", "yes", "on"}

def long_goal() -> str:
    return textwrap.dedent(
        """
        Produce a comprehensive technical report as plain text inside an HTML body.
        The report must be at least 4,000 words and should not stop early.
        Structure:
        - 60 numbered findings, each 3-5 sentences.
        - A 100-sentence continuous narrative (no headings).
        - 50 action items, each exactly 2 sentences.
        - 35 risk blocks, each 4 lines (Title:, Risk:, Mitigation:, Follow-up:).
        Topic: Mars habitat life-support, comms, power, and research. Use numbers,
        percentages, and concrete follow-ups. Do not summarize prematurely; keep
        writing until every section is complete and overall word count is met.
        """
    ).strip()


def mandelbrot_goal() -> str:
    return "Create a mandelbrot zoomer that slowly zooms to an interesting point in the mandelbrot set."


def tool_free_goal() -> str:
    return (
        "Return the full final HTML for a Mandelbrot zoomer directly, without using any tools. "
        "Do not call load_html or any tool; simply respond with the complete HTML document."
    )


async def run_for_model(code_model: str) -> None:
    # Local imports after env load
    from src import config as app_config
    from src.controller import IterationController
    from src.interfaces import TransitionSettings
    from src.services import (
        DevToolsBrowserService,
        OpenRouterAICodeService,
        OpenRouterVisionService,
    )

    cfg = app_config.get_config()

    vision_model = cfg.vision_model

    if USE_TOOL_FREE_PROMPT:
        goal = tool_free_goal()
    else:
        goal = mandelbrot_goal() if USE_MANDELBROT_PROMPT else long_goal()

    settings = TransitionSettings(
        code_model=code_model,
        vision_model=vision_model,
        overall_goal=goal,
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

    print(f"Starting controller transition with {code_model}")
    node_id = await controller.apply_transition(None, settings)
    node = controller.get_node(node_id)
    if not node:
        raise RuntimeError("No node returned from controller")

    output = node.outputs.get(code_model) or next(iter(node.outputs.values()))
    content = output.assistant_response or output.html_output or ""
    meta = output.messages or []
    reasoning = output.reasoning_text or ""

    words = len(content.split())
    chars = len(content)
    print(f"[{code_model}] Assistant response: {words} words, {chars} chars")
    if reasoning:
        print(f"[{code_model}] Reasoning length: {len(reasoning)} chars")

    # Save and print full content so we can see whether truncation is model-side.
    artifacts_dir = PROJECT_ROOT / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_path = artifacts_dir / f"repro_controller_output_{code_model.replace('/', '_')}_{node_id}.txt"
    out_path.write_text(content, encoding="utf-8")

    meta_path = artifacts_dir / f"repro_controller_meta_{code_model.replace('/', '_')}_{node_id}.json"
    try:
        import json

        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved message meta to {meta_path}")
    except Exception as exc:
        print(f"Failed to save meta: {exc}")

    print(f"\n--- Full assistant response start ---\n{content}\n--- Full assistant response end ---")
    print(f"\nSaved full response to {out_path}")


async def main() -> None:
    # Run each model in parallel to save time; each gets its own controller instance.
    tasks = [run_for_model(slug) for slug in CODE_MODELS]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
