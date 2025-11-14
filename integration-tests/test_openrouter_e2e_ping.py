# integration-tests/test_openrouter_e2e_ping.py

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Tuple

os.environ.setdefault("OPENROUTER_DISABLE_RETRY", "1")

from support import bootstrap_test_env, env_ready


async def run_ping() -> Tuple[bool, str]:
    from src.services import (
        OpenRouterAICodeService,
        OpenRouterVisionService,
        PlaywrightBrowserService,
    )
    from src.controller import IterationController
    from src.interfaces import TransitionSettings

    ai = OpenRouterAICodeService()
    vision = OpenRouterVisionService()
    browser = PlaywrightBrowserService()

    from src import config as app_config
    cfg = app_config.get_config()
    code_model = cfg.code_model
    vision_model = cfg.vision_model

    settings = TransitionSettings(
        code_model=code_model,
        vision_model=vision_model,
        overall_goal="Ping E2E (OpenRouter)",
        user_feedback="",
        code_template=(
            "Improve the following HTML while adhering to the goal.\n"
            "Goal: {overall_goal}\n"
            "Vision analysis: {vision_output}\n"
            "User feedback: {user_feedback}\n"
            "HTML:\n{html_input}\n"
        ),
        vision_template=(
            "Analyze the HTML and its rendering to provide guidance.\n"
            "Goal: {overall_goal}\n"
            "User feedback: {user_feedback}\n"
            "HTML:\n{html_input}\n"
        ),
    )

    ctrl = IterationController(ai, browser, vision)
    root_id = await ctrl.apply_transition(None, settings)
    node = ctrl.get_node(root_id)
    if not node:
        return False, "root node missing"

    out = node.outputs.get(code_model)
    if not out or not (out.html_output or "").strip():
        return False, "html_output is empty"

    shot = Path(out.artifacts.screenshot_filename)
    if not shot.exists():
        return False, f"screenshot missing: {shot}"

    # Root may skip vision analysis; do not require it here

    if not isinstance(out.artifacts.console_logs, list):
        return False, "console_logs not a list"

    return True, "end-to-end ping ok"


async def main() -> int:
    project_root, dotenv = bootstrap_test_env()

    ok_env, info_env = env_ready(dotenv, required=("OPENROUTER_BASE_URL", "OPENROUTER_API_KEY"))
    if not ok_env:
        print(f"[ SKIP ] OpenRouter E2E Ping: {info_env}")
        return 0

    try:
        ok, msg = await run_ping()
    except Exception as exc:
        ok, msg = False, f"error: {exc}"
    print(f"[ {'OK' if ok else 'FAIL'} ] OpenRouter E2E Ping: {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
