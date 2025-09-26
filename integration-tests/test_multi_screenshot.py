from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

os.environ.setdefault("OPENROUTER_DISABLE_RETRY", "1")


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_cwd_project_root() -> Path:
    root = get_project_root()
    os.chdir(root)
    return root


def inject_project_into_syspath(project_root: Path) -> None:
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


@dataclass
class _Case:
    name: str
    code_model: str
    vision_model: str
    requested: int
    expected: int
    limit_source: str | None


class _RecordingBrowserService:
    def __init__(self, tmp_dir: Path) -> None:
        self.tmp_dir = tmp_dir
        self.calls: List[Tuple[str, int]] = []

    async def render_and_capture(
        self,
        html_code: str,
        worker: str = "main",
        *,
        capture_count: int = 1,
        interval_seconds: float = 1.0,
    ) -> tuple[List[str], List[str]]:
        self.calls.append((worker, capture_count))
        files: List[str] = []
        base = f"{worker.replace('/', '_')}_{len(self.calls)}"
        for idx in range(capture_count):
            shot_path = self.tmp_dir / f"{base}_{idx}.png"
            shot_path.write_bytes(b"fake-png")
            files.append(str(shot_path))
        logs = [f"[{worker}] capture {i}" for i in range(capture_count)]
        return files, logs


class _LimitingVisionService:
    def __init__(self, limits: Dict[str, int]) -> None:
        self.limits = limits
        self.calls: List[Tuple[str, Sequence[str]]] = []

    async def analyze_screenshot(
        self,
        prompt: str,
        screenshot_paths: Sequence[str],
        console_logs: List[str],
        model: str,
        worker: str = "main",
    ) -> str:
        self.calls.append((model, tuple(screenshot_paths)))
        limit = self.limits.get(model)
        if limit is not None and len(screenshot_paths) > limit:
            raise RuntimeError(f"model {model} supports {limit} images; got {len(screenshot_paths)}")
        return f"vision({model}):{len(screenshot_paths)}"


class _StubAICodeService:
    def __init__(self, limits: Dict[str, int]) -> None:
        self.limits = limits
        self.calls: List[Tuple[str, Sequence[str]]] = []

    async def generate_html(self, prompt, model: str, worker: str = "main") -> tuple[str, str | None, dict | None]:
        # The prompt payload may attach image URLs; we only record counts.
        try:
            if hasattr(prompt, "messages"):
                messages = list(getattr(prompt, "messages"))
            elif isinstance(prompt, list):
                messages = list(prompt)
            else:
                messages = [{"role": "user", "content": prompt}]
            contents = messages[-1]["content"] if messages else []
            if not isinstance(contents, list):
                images: Sequence[str] = []
            else:
                images = tuple(
                    part.get("image_url", {}).get("url", "")
                    for part in contents
                    if isinstance(part, dict) and part.get("type") == "image_url"
                )
        except Exception:
            images = ()
        self.calls.append((model, images))
        limit = self.limits.get(model)
        if limit is not None and len(images) > limit:
            raise RuntimeError(f"code model {model} supports {limit} images; got {len(images)}")
        html = (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>Stub</title></head>"
            "<body><div id=\"content\">stub output</div></body></html>"
        )
        meta = {
            "messages": messages,
            "assistant_response": html,
        }
        return html, "", meta


async def _run_case(case: _Case, tmp_dir: Path) -> Tuple[bool, str]:
    from src.controller import IterationController
    from src.interfaces import IterationMode, TransitionSettings

    browser = _RecordingBrowserService(tmp_dir)
    vision_limits = {
        "google/gemini-2.5-flash-preview-09-2025": 12,
        "qwen/qwen3-vl-235b-a22b-thinking": 5,
    }
    ai_limits = {
        "google/gemini-2.5-flash-preview-09-2025": 8,
        "x-ai/grok-4-fast:free": 4,
    }
    vision = _LimitingVisionService(vision_limits)
    ai = _StubAICodeService(ai_limits)
    controller = IterationController(ai, browser, vision)

    settings = TransitionSettings(
        code_model=case.code_model,
        vision_model=case.vision_model,
        overall_goal="Test multi-screenshot capture",
        user_steering="",
        code_template="Return HTML",
        vision_template="Describe",
        mode=IterationMode.DIRECT_TO_CODER,
        keep_history=False,
        input_screenshot_count=case.requested,
    )

    try:
        root_id = await controller.apply_transition(None, settings)
        node_id = await controller.apply_transition(root_id, settings)
    except Exception as exc:
        return False, f"{case.name}: transition failed: {exc}"

    node = controller.get_node(node_id)
    if not node:
        return False, f"{case.name}: node missing"

    output = next(iter(node.outputs.values()))
    artifacts = output.artifacts
    shots = list(getattr(artifacts, "input_screenshot_filenames", []))
    if len(shots) != case.expected:
        return False, f"{case.name}: expected {case.expected} screenshots, got {len(shots)}"

    # Ensure attachments exist for each screenshot in order with metadata indexes.
    indices = [asset.metadata.get("index") for asset in artifacts.assets if asset.role == "input"]
    if indices != [str(i) for i in range(case.expected)]:
        return False, f"{case.name}: unexpected attachment indices {indices}"

    analysis = dict(getattr(artifacts, "analysis", {}))
    if str(case.expected) != analysis.get("input_screenshot_count"):
        return False, f"{case.name}: analysis count missing ({analysis})"

    note = analysis.get("input_screenshot_limit")
    if case.limit_source is None:
        if note:
            return False, f"{case.name}: unexpected limit note {note}"
    else:
        if not note or case.limit_source not in note:
            return False, f"{case.name}: missing limit note for {case.limit_source}: {note}"

    # Ensure services were invoked with the effective count
    input_call_counts = [count for worker, count in browser.calls if worker == 'input']
    if not input_call_counts:
        return False, f"{case.name}: browser missing input capture"
    browser_count = input_call_counts[-1]
    if browser_count != case.expected:
        return False, f"{case.name}: browser captured {browser_count}"

    if not vision.calls:
        return False, f"{case.name}: vision not called"
    vision_count = len(vision.calls[-1][1])
    if vision_count != case.expected:
        return False, f"{case.name}: vision saw {vision_count}"

    if not ai.calls:
        return False, f"{case.name}: ai not called"
    ai_count = len(ai.calls[-1][1])
    if ai_count != case.expected:
        return False, f"{case.name}: ai saw {ai_count}"

    return True, f"{case.name}: ok"


async def main() -> int:
    project_root = ensure_cwd_project_root()
    inject_project_into_syspath(project_root)

    tmp_dir = project_root / "artifacts" / "test-multi"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    cases = [
        _Case(
            name="gemini-ok",
            code_model="google/gemini-2.5-flash-preview-09-2025",
            vision_model="google/gemini-2.5-flash-preview-09-2025",
            requested=3,
            expected=3,
            limit_source=None,
        ),
        _Case(
            name="grok-clamped",
            code_model="x-ai/grok-4-fast:free",
            vision_model="qwen/qwen3-vl-235b-a22b-thinking",
            requested=6,
            expected=4,
            limit_source="x-ai/grok-4-fast:free",
        ),
    ]

    all_ok = True
    for case in cases:
        ok, msg = await _run_case(case, tmp_dir)
        status = "OK" if ok else "FAIL"
        print(f"[ {status} ] {msg}")
        all_ok = all_ok and ok

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
