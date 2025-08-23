# integration-tests/test_state_machine.py
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Tuple
import os
import sys


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_cwd_project_root() -> Path:
    root = get_project_root()
    os.chdir(root)
    return root


def inject_src_into_syspath(project_root: Path) -> None:
    # Ensure project root is on sys.path so we can import the src package
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


async def build_controller():
    from src.services import StubAICodeService, PlaywrightBrowserService, StubVisionService
    from src.controller import IterationController

    ai = StubAICodeService()
    browser = PlaywrightBrowserService()
    vision = StubVisionService()
    return IterationController(ai, browser, vision)


def default_settings(overall_goal: str = ""):
    from src.interfaces import TransitionSettings

    code_model = os.getenv("VIBES_CODE_MODEL", "code-model")
    vision_model = os.getenv("VIBES_VISION_MODEL", "vision-model")
    return TransitionSettings(
        code_model=code_model,
        code_instructions="",
        vision_model=vision_model,
        vision_instructions="",
        overall_goal=overall_goal,
        code_template=(
            "Improve the following HTML while adhering to the goal.\n"
            "Goal: {overall_goal}\n"
            "Vision analysis: {vision_output}\n"
            "Instructions: {code_instructions}\n"
            "HTML:\n{html_input}\n"
        ),
        vision_template=(
            "Analyze the HTML and its rendering to provide guidance.\n"
            "Goal: {overall_goal}\n"
            "Instructions: {vision_instructions}\n"
            "HTML:\n{html_input}\n"
        ),
    )


async def test_linear_chain() -> Tuple[bool, str]:
    from src.interfaces import IterationNode

    ctrl = await build_controller()
    root_id = await ctrl.create_root(default_settings("Goal A"))
    child1_id = await ctrl.apply_transition(root_id, default_settings("Goal A"))
    child2_id = await ctrl.apply_transition(child1_id, default_settings("Goal A"))

    # Validate chain length and parent-child links
    root_node = ctrl.get_node(root_id)
    child1_node = ctrl.get_node(child1_id)
    child2_node = ctrl.get_node(child2_id)
    if not (root_node and child1_node and child2_node):
        return False, "Missing nodes in chain"
    if child1_node.parent_id != root_id:
        return False, "child1 parent link incorrect"
    if child2_node.parent_id != child1_id:
        return False, "child2 parent link incorrect"
    return True, "linear chain ok"


async def test_rerun_mid_chain() -> Tuple[bool, str]:
    ctrl = await build_controller()
    root_id = await ctrl.create_root(default_settings("Goal B"))
    child1_id = await ctrl.apply_transition(root_id, default_settings("Goal B"))
    child2_id = await ctrl.apply_transition(child1_id, default_settings("Goal B"))

    # Re-run from child1 with a modified code_model
    s = default_settings("Goal B")
    s.code_model = "modified-model"
    new_child_id = await ctrl.apply_transition(child1_id, s)

    # The old child2 should be deleted
    if ctrl.get_node(child2_id) is not None:
        return False, "descendant was not deleted on re-run"

    # New child should exist and be linked to child1
    new_child = ctrl.get_node(new_child_id)
    if not new_child or new_child.parent_id != child1_id:
        return False, "new child link incorrect"

    # Settings propagation: code_model equals edited value
    if new_child.settings.code_model != "modified-model":
        return False, "settings change did not propagate"
    return True, "re-run mid-chain ok"


async def test_artifacts_presence() -> Tuple[bool, str]:
    from src.interfaces import IterationNode

    ctrl = await build_controller()
    root_id = await ctrl.create_root(default_settings("Artifacts"))
    c1_id = await ctrl.apply_transition(root_id, default_settings("Artifacts"))

    for nid in [root_id, c1_id]:
        node = ctrl.get_node(nid)
        if not node:
            return False, f"node {nid} missing"
        p = Path(node.artifacts.screenshot_filename)
        if not p.exists():
            return False, f"screenshot missing for node {nid}: {p}"
        if not node.artifacts.vision_output.strip():
            return False, f"vision_output empty for node {nid}"
        if not isinstance(node.artifacts.console_logs, list):
            return False, f"console_logs not a list for node {nid}"
    return True, "artifacts present"


async def test_prompt_placeholders() -> Tuple[bool, str]:
    # Recording AI service to capture the prompt sent by δ
    from src.controller import IterationController
    from src.interfaces import AICodeService, TransitionSettings
    from src.services import PlaywrightBrowserService, StubVisionService

    class RecordingAICodeService(AICodeService):
        def __init__(self) -> None:
            self.last_prompt: str = ""
        async def generate_html(self, prompt: str) -> str:
            self.last_prompt = prompt
            safe = (prompt or "").strip()[:200]
            return (
                "<!DOCTYPE html>\n"
                "<html><head><meta charset=\"utf-8\"><title>Generated Page</title>\n"
                "<style>body{font-family:sans-serif;padding:24px} .box{padding:16px;border:1px solid #ccc;border-radius:8px}</style>\n"
                "</head><body>\n"
                f"<h1>Generated from prompt</h1><div class=\"box\"><pre>{safe}</pre></div>\n"
                "<script>console.log('Page loaded');</script>\n"
                "</body></html>"
            )

    ai = RecordingAICodeService()
    browser = PlaywrightBrowserService()
    vision = StubVisionService()
    ctrl = IterationController(ai, browser, vision)

    settings = default_settings("Ensure placeholders")

    # Create root (no initial html_input). Then apply one transition; δ should
    # render root.html_output, compute vision_output, and build a code prompt
    # that includes all placeholders.
    root_id = await ctrl.create_root(settings)
    child_id = await ctrl.apply_transition(root_id, settings)
    root = ctrl.get_node(root_id)
    child = ctrl.get_node(child_id)
    if not (root and child):
        return False, "nodes missing"

    expected_prompt = settings.code_template.format(
        html_input=root.html_output,
        code_instructions=settings.code_instructions,
        overall_goal=settings.overall_goal,
        vision_output=child.artifacts.vision_output,
    )

    if ai.last_prompt != expected_prompt:
        return False, "code prompt did not include expected placeholder substitutions"

    return True, "prompt placeholders substituted in code template"


async def main() -> int:
    project_root = ensure_cwd_project_root()
    inject_src_into_syspath(project_root)

    checks = [
        ("Linear Chain", test_linear_chain),
        ("Re-run Mid-Chain", test_rerun_mid_chain),
        ("Artifact Presence", test_artifacts_presence),
        ("Prompt Placeholders", test_prompt_placeholders),
    ]

    ok_all = True
    for name, fn in checks:
        try:
            ok, msg = await fn()
        except Exception as exc:
            ok, msg = False, f"error: {exc}"
        status = "OK" if ok else "FAIL"
        print(f"[ {status} ] {name}: {msg}")
        ok_all = ok_all and ok

    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
