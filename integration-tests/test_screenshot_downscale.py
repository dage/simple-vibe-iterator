from __future__ import annotations

import asyncio
import base64
from io import BytesIO
import os
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple
from unittest.mock import patch

from PIL import Image

PNG_COLOR = (16, 88, 160)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_root_cwd() -> Path:
    root = project_root()
    os.chdir(root)
    return root


def inject_src() -> None:
    root = project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


class StubBrowserService:
    def __init__(self, out_dir: Path, size: tuple[int, int] = (40, 20)) -> None:
        self._out_dir = out_dir
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._size = size
        self._counter = 0
        self.last_capture: Path | None = None

    async def render_and_capture(
        self,
        html_code: str,
        worker: str = "main",
        *,
        capture_count: int = 1,
        interval_seconds: float = 1.0,
    ) -> tuple[List[str], List[str]]:
        del html_code, worker, interval_seconds
        try:
            count = int(capture_count)
        except Exception:
            count = 1
        count = max(1, count)
        outputs: List[str] = []
        for _ in range(count):
            self._counter += 1
            target = self._out_dir / f"downscale_capture_{self._counter}.png"
            Image.new("RGB", self._size, PNG_COLOR).save(target)
            self.last_capture = target
            outputs.append(str(target))
        return outputs, ["[log] stub"]

    async def run_feedback_preset(
        self,
        html_code: str,
        preset: object,
        worker: str = "main",
    ) -> tuple[List[str], List[str], List[str]]:
        del html_code, preset, worker
        return [], [], []


class RecordingCodeService:
    def __init__(self) -> None:
        self.calls: List[List[Dict[str, object]]] = []

    async def generate_html(self, prompt, model: str, worker: str = "main") -> tuple[str, str | None, Dict[str, object]]:
        del worker, model
        if hasattr(prompt, "messages"):
            messages = list(getattr(prompt, "messages", []) or [])
        elif isinstance(prompt, list):
            messages = [dict(p) for p in prompt]
        else:
            messages = [{"role": "user", "content": str(prompt or "")}]
        self.calls.append([dict(msg) for msg in messages])
        html = "<html><body><p>stub</p></body></html>"
        meta: Dict[str, object] = {
            "messages": messages,
            "assistant_response": html,
        }
        return html, "", meta


def decode_data_url_dimensions(data_url: str) -> tuple[int, int]:
    _, payload = data_url.split(",", 1)
    raw = base64.b64decode(payload)
    with Image.open(BytesIO(raw)) as image:
        return image.size


async def test_screenshot_downscale_used_by_llm_contexts() -> Tuple[bool, str]:
    ensure_root_cwd()
    inject_src()

    from src.controller import IterationController
    from src.interfaces import TransitionSettings
    from src.services import OpenRouterVisionService
    from src import or_client
    from src.config import get_config

    artifacts_dir = project_root() / "artifacts" / "test_screenshot_downscale"
    browser = StubBrowserService(artifacts_dir)
    code_service = RecordingCodeService()
    vision_service = OpenRouterVisionService()

    async def fake_support(models: Sequence[str]) -> Dict[str, bool]:
        return {slug: True for slug in models if slug}

    async def fake_chat(*args, **kwargs) -> str:
        return "stub vision"

    encode_calls: List[tuple[int, int]] = []
    original_encode = or_client.encode_image_to_data_url

    def tracking_encode(data, mime=None):
        url = original_encode(data, mime=mime)
        encode_calls.append(decode_data_url_dimensions(url))
        return url

    settings = TransitionSettings(
        code_model="stub/code",
        vision_model="stub/vision",
        overall_goal="Downscale verification",
        user_feedback="",
        code_template="HTML: {html_input}",
        vision_template="Vision: {html_input}",
        input_screenshot_count=1,
        code_system_prompt_template="",
    )

    scale = float(getattr(get_config(), "screenshot_scale", 1.0) or 1.0)

    with patch("src.controller._detect_code_model_image_support", new=fake_support):
        with patch("src.or_client.chat", new=fake_chat):
            with patch("src.or_client.encode_image_to_data_url", new=tracking_encode):
                controller = IterationController(code_service, browser, vision_service)
                root_id = await controller.apply_transition(None, settings)
                # A second transition supplies html_input so the browser captures and attachments are generated.
                await controller.apply_transition(root_id, settings)

    if browser.last_capture is None:
        return False, "browser did not produce a capture"

    with Image.open(browser.last_capture) as original_image:
        orig_width, orig_height = original_image.size

    expected_width = max(1, int(round(orig_width * scale)))
    expected_height = max(1, int(round(orig_height * scale)))

    if scale >= 1 or scale <= 0:
        return False, "test requires config.screenshot_scale to be between 0 and 1"

    if len(encode_calls) != 2:
        return False, f"expected 2 image encodes, got {len(encode_calls)}"

    if any(dim != (expected_width, expected_height) for dim in encode_calls):
        return False, f"scaled dims mismatch: expected {(expected_width, expected_height)}, got {encode_calls}"

    if expected_width >= orig_width or expected_height >= orig_height:
        return False, "computed target size is not smaller than original"

    return True, "downscaled screenshots are used by both vision and code contexts"


async def main() -> int:
    ok, info = await test_screenshot_downscale_used_by_llm_contexts()
    status = "OK" if ok else "FAIL"
    print(f"[ {status} ] Screenshot downscale: {info}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
