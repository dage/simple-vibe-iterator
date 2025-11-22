from __future__ import annotations

import asyncio
from pathlib import Path

PNG_BYTES = bytes.fromhex(
    "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000A"
    "49444154789C6360000002000100FFFF03000006000557FE0000000049454E44AE426082"
)


class StubBrowserService:
    def __init__(self, out_dir: Path) -> None:
        self._out_dir = out_dir
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self.last_html: str = ""

    async def render_and_capture(self, html_code: str, worker: str = "main", *, capture_count: int = 1, interval_seconds: float = 1.0):
        self.last_html = html_code
        target = self._out_dir / "capture.png"
        target.write_bytes(PNG_BYTES)
        return [str(target)], []


class NoopVisionService:
    async def analyze_screenshot(self, *args, **kwargs) -> str:  # noqa: ANN401
        return "- ok"


class StaticAICodeService:
    def __init__(self, html_template: str) -> None:
        self.html = html_template

    async def generate_html(self, *args, **kwargs):  # noqa: ANN401
        return self.html, "", {}


def default_settings():
    from src.interfaces import TransitionSettings

    return TransitionSettings(
        code_model="stub/code",
        vision_model="stub/vision",
        overall_goal="Reproduce template variable placeholder",
        user_feedback="",
        code_template="HTML: {html_input}",
        vision_template="",
    )


async def reproduce(single_brace: bool = False) -> str:
    from src.controller import IterationController

    artifacts_dir = Path("artifacts/repro_template_placeholder")
    ai = StaticAICodeService(
        "<!DOCTYPE html><html><body><div>{key}</div></body></html>".format(
            key="{TEST}" if single_brace else "{{TEST}}"
        )
    )
    browser = StubBrowserService(artifacts_dir)
    vision = NoopVisionService()
    controller = IterationController(ai, browser, vision)
    controller.set_template_text_variable("TEST", "Injected via script")
    settings = default_settings()
    node_id = await controller.apply_transition(None, settings)
    node = controller.get_node(node_id)
    first_output = next(iter(node.outputs.values()))
    return first_output.html_output


async def main() -> None:
    double_html = await reproduce(single_brace=False)
    single_html = await reproduce(single_brace=True)
    print("Double brace output:\n", double_html)
    print("\nSingle brace output:\n", single_html)


if __name__ == "__main__":
    asyncio.run(main())
