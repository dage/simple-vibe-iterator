# integration-tests/test_openrouter_iterate_number.py

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
        "VIBES_API_KEY",
        "VIBES_CODE_MODEL",
        "VIBES_VISION_MODEL",
    ]
    missing = [k for k in need if not get_env_value(k, dotenv)]
    if missing:
        return False, f"missing: {', '.join(missing)}"
    return True, "all present"


async def run_iterate_number() -> Tuple[bool, str]:
    from src.services import (
        OpenRouterAICodeService,
        OpenRouterVisionService,
        PlaywrightBrowserService,
    )
    from src.controller import IterationController
    from src.interfaces import TransitionSettings
    from src import or_client

    ai = OpenRouterAICodeService()
    vision = OpenRouterVisionService()
    browser = PlaywrightBrowserService()
    ctrl = IterationController(ai, browser, vision)

    code_model = os.getenv("VIBES_CODE_MODEL", "code-model")
    vision_model = os.getenv("VIBES_VISION_MODEL", "vision-model")

    # Root: render a single huge centered number '1'
    root_settings = TransitionSettings(
        code_model=code_model,
        vision_model=vision_model,
        overall_goal="Show a large centered number '1'",
        user_steering=(
            "Return standalone HTML for a white page showing a single extremely large, centered number '1'. "
            "Use very high contrast: pure black text on white background, font-weight 900, font-size at least 800px, "
            "no shadows, no outlines, and center both vertically and horizontally. Body must contain only the number."
        ),
        code_template=(
            "Improve or create the HTML per the goal and instructions.\n"
            "Goal: {overall_goal}\n"
            "Vision analysis: {vision_output}\n"
            "User steering: {user_steering}\n"
            "HTML:\n{html_input}\n"
        ),
        vision_template=(
            "Analyze the rendered page. Identify the number visibly shown.\n"
            "Goal: {overall_goal}\n"
            "User steering: {user_steering}\n"
            "HTML:\n{html_input}\n"
        ),
    )

    root_id = await ctrl.apply_transition(None, root_settings)
    root = ctrl.get_node(root_id)
    if not root:
        return False, "root missing"

    # Iteration: change the number to '2'
    iter_settings = TransitionSettings(
        code_model=code_model,
        vision_model=vision_model,
        overall_goal="Change the number to '2'",
        user_steering=(
            "Modify the HTML so the displayed number is now '2' (not '1'). Keep the page as only a single, extremely large, high-contrast numeral '2' "
            "(pure black on white, font-weight 900, font-size at least 800px), centered both vertically and horizontally."
        ),
        code_template=root_settings.code_template,
        vision_template=root_settings.vision_template,
    )

    child_id = await ctrl.apply_transition(root_id, iter_settings)
    child = ctrl.get_node(child_id)
    if not child:
        return False, "child missing"

    # Basic HTML assertions
    r_html = (root.html_output or "").lower()
    c_html = (child.html_output or "").lower()
    if not c_html.strip():
        return False, "child html empty"
    if c_html == r_html:
        return False, "child html unchanged from root"
    if "2" not in c_html:
        return False, "child html does not include '2'"

    # Screenshot files exist and should differ between root and child
    if not Path(root.artifacts.screenshot_filename).exists():
        return False, "root screenshot missing"
    if not Path(child.artifacts.screenshot_filename).exists():
        return False, "child screenshot missing"
    if child.artifacts.screenshot_filename == root.artifacts.screenshot_filename:
        return False, "child screenshot equals root (output not re-rendered)"

    # Require a robust direct single-image vision ping to be '2'
    # Small retry to reduce flakiness of remote vision model
    success = False
    last = ""
    for _ in range(5):
        direct = await or_client.vision_single(
            prompt=(
                "Identify the single large number in this image. Respond with exactly one character: 0-9."
            ),
            image=child.artifacts.screenshot_filename,
            temperature=0,
        )
        last = direct
        dt = (direct or "").strip().lower()
        strip_chars = ".,!?:;()[]{}\"'`"
        words = [w.strip(strip_chars) for w in dt.split() if w.strip(strip_chars)]
        alpha = [w for w in words if w]
        if len(alpha) >= 1 and alpha[0] == "2":
            success = True
            break
    if not success:
        return False, f"direct vision on child screenshot not '2': {last!r}"

    return True, "iteration changed number to 2 and vision recognized it"


async def main() -> int:
    project_root = ensure_cwd_project_root()
    inject_project_into_syspath(project_root)

    dotenv_path = project_root / ".env"
    dotenv = parse_dotenv(dotenv_path)
    ok_env, info_env = env_ready(dotenv)
    if not ok_env:
        print(f"[ SKIP ] Iterate Number: {info_env}")
        return 0

    try:
        ok, msg = await run_iterate_number()
    except Exception as exc:
        ok, msg = False, f"error: {exc}"
    print(f"[ {'OK' if ok else 'FAIL'} ] Iterate Number: {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


