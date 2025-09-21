# integration-tests/test_openrouter_integration.py

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple
from PIL import Image, ImageDraw

os.environ.setdefault("OPENROUTER_DISABLE_RETRY", "1")


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_cwd_project_root() -> Path:
    root = get_project_root()
    os.chdir(root)
    return root


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


def inject_src_into_syspath(project_root: Path) -> None:
    src_path = project_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


# (No external image assets needed; we programmatically generate test images)


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


async def validate_api_key_through_models_list():
    import or_client  # imported after sys.path injection

    try:
        client = or_client._client()  # uses cached settings from .env
        # Prefer models.list (no credits). Fallback handled by exception path
        models = await client.models.list()
        # Basic sanity check
        _ = getattr(models, "data", None)
        return True, "models.list() succeeded"
    except Exception as exc:  # broad: surface any setup/network/key error
        return False, f"models.list() failed: {exc}"


async def test_code_model() -> Tuple[bool, str]:
    import or_client

    try:
        reply = await or_client.chat(
            messages=[{"role": "user", "content": "Reply exactly: OK"}],
            temperature=0,
        )
        ok = bool((reply or "").strip())
        return ok, (reply or "(empty reply)")
    except Exception as exc:
        return False, f"error: {exc}"


async def test_vision_model() -> Tuple[bool, str]:
    import or_client

    try:
        # Generate a 256x256 image with a solid black circle in the center
        size = 256
        image = Image.new("RGB", (size, size), color="white")
        draw = ImageDraw.Draw(image)
        r = 48
        bbox = (size//2 - r, size//2 - r, size//2 + r, size//2 + r)
        draw.ellipse(bbox, fill="black")

        # Save to bytes
        from io import BytesIO
        buf = BytesIO()
        image.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        reply = await or_client.vision_single(
            prompt=(
                "Identify the geometric shape in the image. "
                "Respond with exactly one word: circle, square, triangle, or rectangle."
            ),
            image=image_bytes,
            temperature=0,
        )
        text = (reply or "").strip().lower()
        # Strict validation: exactly one word 'circle' (ignoring punctuation)
        strip_chars = ".,!?:;()[]{}\"'`"
        words = [w.strip(strip_chars) for w in text.split() if w.strip(strip_chars)]
        alpha_words = [w for w in words if w.isalpha()]
        ok = len(alpha_words) == 1 and alpha_words[0] == "circle"
        return ok, (reply or "(empty reply)")
    except Exception as exc:
        return False, f"error: {exc}"


async def main() -> int:
    project_root = ensure_cwd_project_root()
    inject_src_into_syspath(project_root)

    dotenv_path = project_root / ".env"
    dotenv = parse_dotenv(dotenv_path)

    # Gather required environment values
    api_key = get_env_value("OPENROUTER_API_KEY", dotenv)
    base_url = get_env_value("OPENROUTER_BASE_URL", dotenv)

    # Check presence of required variables first (YAML provides models)
    presence_ok = bool(api_key and base_url)
    missing = [
        name
        for name, present in [
            ("OPENROUTER_API_KEY", api_key),
            ("OPENROUTER_BASE_URL", base_url),
        ]
        if not present
    ]
    presence_details = "all present" if presence_ok else f"missing: {', '.join(missing)}"
    print(f"[ {'OK' if presence_ok else 'FAIL'} ] API env set: {presence_details}")
    if not presence_ok:
        return 1

    # 1) Validate API key by listing models
    ok_models, info_models = await validate_api_key_through_models_list()
    print(f"[ {'OK' if ok_models else 'FAIL'} ] API key valid via models.list(): {_one_line(info_models)}")

    # 2) Test code model with a minimal chat
    ok_code, info_code = await test_code_model()
    print(f"[ {'OK' if ok_code else 'FAIL'} ] Code model chat returns non-empty reply: reply=\"{_one_line(info_code)}\"")

    # 3) Test vision model with a tiny embedded PNG
    ok_vision, info_vision = await test_vision_model()
    print(f"[ {'OK' if ok_vision else 'FAIL'} ] Vision model identifies circle: reply=\"{_one_line(info_vision)}\"")

    all_ok = ok_models and ok_code and ok_vision
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

