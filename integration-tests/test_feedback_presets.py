from __future__ import annotations

import asyncio
import os
from pathlib import Path
from textwrap import dedent
import sys
import tempfile

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TEST_PRESET_ID = "test_single_key"

HTML_SNIPPET = dedent(
    """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="utf-8" />
      <title>Feedback Harness</title>
      <style>
        html, body {
          margin: 0;
          padding: 0;
          width: 100%;
          height: 100%;
          font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #0f172a;
          color: #e2e8f0;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .panel {
          text-align: center;
        }
        #status {
          font-size: 48px;
          font-weight: 700;
          margin-bottom: 16px;
        }
        #log {
          font-size: 18px;
          opacity: 0.8;
        }
      </style>
    </head>
    <body>
      <div class="panel">
        <div id="status">Idle</div>
        <div id="log">Waiting for keypress…</div>
      </div>
      <script>
        const colors = {
          "w": { down: "#f43f5e", up: "#22c55e" },
        };
        const status = document.getElementById("status");
        const log = document.getElementById("log");

        function update(key, phase) {
          const palette = colors[key] || {};
          const color = palette[phase] || "#0f172a";
          document.body.style.background = color;
          status.textContent = key.toUpperCase() + " " + phase.toUpperCase();
          log.textContent = "key " + phase + " detected at " + performance.now().toFixed(1) + "ms";
          console.log(log.textContent);
        }

        window.addEventListener("keydown", (event) => {
          if (colors[event.key]) {
            event.preventDefault();
            update(event.key, "down");
          }
        });

        window.addEventListener("keyup", (event) => {
          if (colors[event.key]) {
            event.preventDefault();
            update(event.key, "up");
          }
        });
      </script>
    </body>
    </html>
    """
).strip()

PRESET_YAML = dedent(
    f"""
    presets:
      - id: {TEST_PRESET_ID}
        label: Test single key
        actions:
          - action: wait
            seconds: 0.2
          - action: keypress
            key: w
            duration_ms: 1500
          - action: wait
            seconds: 0.15
          - action: screenshot
            label: after-w
    """
).strip()


async def run_feedback_preset_test() -> None:
    from src.services import PlaywrightBrowserService
    from src import feedback_presets

    browser = PlaywrightBrowserService()
    tmpdir = tempfile.TemporaryDirectory()
    try:
        preset_path = Path(tmpdir.name) / "feedback_presets.yaml"
        preset_path.write_text(PRESET_YAML, encoding="utf-8")
        os.environ["FEEDBACK_PRESETS_PATH"] = str(preset_path)
        feedback_presets.reset_feedback_presets_cache()
        preset = feedback_presets.get_feedback_preset(TEST_PRESET_ID)
        if preset is None:
            raise AssertionError("Failed to load test preset")

        screenshots, logs, labels = await browser.run_feedback_preset(HTML_SNIPPET, preset, worker="test")
        if len(screenshots) != 1:
            raise AssertionError(f"Expected single screenshot, got {len(screenshots)}")

        image_path = Path(screenshots[0])
        if not image_path.exists():
            raise AssertionError(f"Screenshot missing at {image_path}")

        dominant = _center_pixel(image_path)
        expected = (240, 90, 118)  # observed key-down color near #f43f5e
        if not _color_close(dominant, expected, tolerance=35):
            raise AssertionError(f"Unexpected pixel color {dominant} vs {expected}")

        if not any("key up detected" in entry.lower() for entry in logs):
            raise AssertionError(f"Console logs missing expected entry: {logs}")
    finally:
        tmpdir.cleanup()
        os.environ.pop("FEEDBACK_PRESETS_PATH", None)
        feedback_presets.reset_feedback_presets_cache()


def _center_pixel(path: Path) -> tuple[int, int, int]:
    with Image.open(path) as img:
        rgb = img.convert("RGB")
        width, height = rgb.size
        return rgb.getpixel((width // 2, height // 2))


def _color_close(actual: tuple[int, int, int], expected: tuple[int, int, int], *, tolerance: int) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(actual, expected))


async def main() -> int:
    await run_feedback_preset_test()
    print("feedback preset integration ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
