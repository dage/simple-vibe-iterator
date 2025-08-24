# integration-tests/test_template_rendering.py
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import List, Tuple


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_cwd_project_root() -> Path:
    root = get_project_root()
    os.chdir(root)
    return root


def inject_src_into_syspath(project_root: Path) -> None:
    if str(project_root) not in os.sys.path:
        os.sys.path.insert(0, str(project_root))


def _html_with_scripts(script_lines: list[str]) -> str:
    scripts = "\n".join(script_lines)
    return (
        "<!DOCTYPE html>\n"
        "<html><head><meta charset=\"utf-8\"><title>T</title></head>\n"
        f"<body><div id=\"app\">ok</div><script>{scripts}</script></body></html>"
    )


class RecordingAICodeService:
    def __init__(self, script_lines: list[str] | None = None) -> None:
        self.last_prompt: str = ""
        self._script_lines: list[str] = list(script_lines or [])

    async def generate_html(self, prompt: str) -> str:
        self.last_prompt = prompt
        return _html_with_scripts(self._script_lines)


class RecordingVisionService:
    def __init__(self) -> None:
        self.last_prompt: str = ""

    async def analyze_screenshot(self, prompt: str, screenshot_path: str, console_logs: List[str]) -> str:
        self.last_prompt = prompt
        return "vision: ok"


async def test_template_context_rendering() -> Tuple[bool, str]:
    from src.services import PlaywrightBrowserService
    from src.controller import IterationController
    from src.interfaces import TransitionSettings

    def make_settings() -> TransitionSettings:
        return TransitionSettings(
            code_model="cm-x",
            vision_model="vm-y",
            overall_goal="OG-Z",
            user_steering="US-W",
            code_template=(
                "CODE-TPL\n"
                "code_model={code_model}\n"
                "vision_model={vision_model}\n"
                "overall_goal={overall_goal}\n"
                "user_steering={user_steering}\n"
                "have_html_input={html_input}\n"
                "have_vision_output={vision_output}\n"
                "have_console_logs={console_logs}\n"
                "self_name=CODE-TPL\n"
                "peer_name=VISION-TPL\n"
            ),
            vision_template=(
                "VISION-TPL\n"
                "code_model={code_model}\n"
                "vision_model={vision_model}\n"
                "overall_goal={overall_goal}\n"
                "user_steering={user_steering}\n"
                "have_html_input={html_input}\n"
                "have_console_logs={console_logs}\n"
                "self_name=VISION-TPL\n"
                "peer_name=CODE-TPL\n"
            ),
        )

    async def run_case(script_lines: list[str]) -> tuple[str, str]:
        ai = RecordingAICodeService(script_lines=script_lines)
        vision = RecordingVisionService()
        browser = PlaywrightBrowserService()
        ctrl = IterationController(ai, browser, vision)
        settings = make_settings()

        root_id = await ctrl.create_root(settings)
        child_id = await ctrl.apply_transition(root_id, settings)
        _ = ctrl.get_node(child_id)
        return ai.last_prompt, vision.last_prompt

    # 0 logs
    cp0, vp0 = await run_case([])
    if "have_console_logs=\n" not in cp0:
        return False, "code prompt console_logs not empty for 0 entries"

    # 1 log
    cp1, vp1 = await run_case(["console.log('ONE')"])
    if "[log] ONE" not in cp1:
        return False, "code prompt missing single console log entry"

    # many logs
    cpn, vpn = await run_case(["console.log('A')", "console.log('B')"])
    if "[log] A" not in cpn or "[log] B" not in cpn:
        return False, "code prompt missing multiple console log entries"

    # Common substitutions still validated
    for cp, vp in [(cp0, vp0), (cp1, vp1), (cpn, vpn)]:
        for token in ["{code_model}", "{vision_model}", "{overall_goal}", "{user_steering}"]:
            if token in cp or token in vp:
                return False, f"unsubstituted token present: {token}"

    return True, "console_logs render for 0/1/many and common fields substituted"


async def main() -> int:
    project_root = ensure_cwd_project_root()
    inject_src_into_syspath(project_root)
    try:
        ok, msg = await test_template_context_rendering()
    except Exception as exc:
        ok, msg = False, f"error: {exc}"
    print(f"[ {'OK' if ok else 'FAIL'} ] Template Rendering: {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


