from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.interfaces import TransitionArtifacts
from src.view_utils import extract_vision_summary


def ensure_cwd_project_root() -> Path:
    root = PROJECT_ROOT
    os.chdir(root)
    return root


def make_artifacts(*, vision_output: str, analysis_value: str | None) -> TransitionArtifacts:
    artifacts = TransitionArtifacts(
        screenshot_filename="test.png",
        console_logs=[],
        vision_output=vision_output,
        input_screenshot_filenames=["input.png"],
        input_console_logs=[],
    )
    if analysis_value is not None:
        artifacts.analysis["vision_summary"] = analysis_value
    return artifacts


def test_prefers_direct_vision_output() -> Tuple[bool, str]:
    art = make_artifacts(vision_output="direct summary", analysis_value="fallback")
    text = extract_vision_summary(art)
    if text != "direct summary":
        return False, f"did not prefer direct output, got: {text!r}"
    return True, "prefers direct vision_output when present"


def test_falls_back_to_analysis_summary() -> Tuple[bool, str]:
    art = make_artifacts(vision_output="", analysis_value="stored summary")
    text = extract_vision_summary(art)
    if text != "stored summary":
        return False, f"expected analysis fallback, got: {text!r}"
    return True, "falls back to analysis metadata when direct text is empty"


def test_handles_no_artifacts() -> Tuple[bool, str]:
    text = extract_vision_summary(None)
    if text:
        return False, f"expected empty string for missing artifacts, got: {text!r}"
    return True, "gracefully handles missing artifacts"


async def main() -> int:
    ensure_cwd_project_root()

    checks = [
        ("Prefers direct output", test_prefers_direct_vision_output),
        ("Fallback to analysis", test_falls_back_to_analysis_summary),
        ("Handles missing artifacts", test_handles_no_artifacts),
    ]

    ok_all = True
    for name, fn in checks:
        try:
            ok, msg = fn()
        except Exception as exc:
            ok, msg = False, f"error: {exc}"
        status = "OK" if ok else "FAIL"
        print(f"[ {status} ] {name}: {msg}")
        ok_all = ok_all and ok

    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
