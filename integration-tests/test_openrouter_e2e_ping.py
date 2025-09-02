# integration-tests/test_openrouter_e2e_ping.py

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_cwd_project_root() -> Path:
    root = get_project_root()
    os.chdir(root)
    return root


def inject_project_into_syspath(project_root: Path) -> None:
    # Ensure project root is on sys.path so we can import the src package
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


def parse_dotenv(env_path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not env_path.exists():
        return env
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def get_env_value(name: str, dotenv: Dict[str, str]) -> Optional[str]:
    return os.getenv(name) or dotenv.get(name)


def env_ready(dotenv: Dict[str, str]) -> Tuple[bool, str]:
    need = [
        "OPENROUTER_BASE_URL",
        "OPENROUTER_API_KEY",
    ]
    missing = [k for k in need if not get_env_value(k, dotenv)]
    if missing:
        return False, f"missing: {', '.join(missing)}"
    return True, "all present"


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
        user_steering="",
        code_template=(
            "Improve the following HTML while adhering to the goal.\n"
            "Goal: {overall_goal}\n"
            "Vision analysis: {vision_output}\n"
            "User steering: {user_steering}\n"
            "HTML:\n{html_input}\n"
        ),
        vision_template=(
            "Analyze the HTML and its rendering to provide guidance.\n"
            "Goal: {overall_goal}\n"
            "User steering: {user_steering}\n"
            "HTML:\n{html_input}\n"
        ),
    )

    ctrl = IterationController(ai, browser, vision)
    root_id = await ctrl.apply_transition(None, settings)
    node = ctrl.get_node(root_id)
    if not node:
        return False, "root node missing"

    # Get first output for single-model test
    if not node.outputs:
        return False, "node has no outputs"
    first_output = next(iter(node.outputs.values()))
    
    if not (first_output.html_output or "").strip():
        return False, "html_output is empty"

    shot = Path(first_output.artifacts.screenshot_filename)
    if not shot.exists():
        return False, f"screenshot missing: {shot}"

    # Root may skip vision analysis; do not require it here

    if not isinstance(first_output.artifacts.console_logs, list):
        return False, "console_logs not a list"

    return True, "end-to-end ping ok"


async def main() -> int:
    project_root = ensure_cwd_project_root()
    inject_project_into_syspath(project_root)

    dotenv_path = project_root / ".env"
    dotenv = parse_dotenv(dotenv_path)

    ok_env, info_env = env_ready(dotenv)
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


