from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
import tempfile
from typing import Tuple


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_root_cwd() -> Path:
    root = project_root()
    os.chdir(root)
    return root


def inject_src() -> None:
    p = project_root() / "src"
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


async def test_param_application() -> Tuple[bool, str]:
    import config as app_config
    import or_client
    # Use a temp params file to avoid interference with parallel tests
    tmp = tempfile.NamedTemporaryFile(prefix="model_params_", suffix=".json", delete=False)
    os.environ["MODEL_PARAMS_PATH"] = tmp.name
    import importlib
    mp = importlib.import_module('model_params')

    cfg = app_config.get_config()
    slug = cfg.code_model

    # Start clean
    mp.set_params(slug, {})
    try:
        # Ask for long output and measure size differences
        prompt = (
            "Respond ONLY with the letter X repeated as much as possible. "
            "No spaces, no punctuation, no explanations."
        )

        # Apply tight limit
        mp.set_params(slug, {"max_tokens": "5"})
        short = await or_client.chat(messages=[{"role": "user", "content": prompt}], model=slug, temperature=0)
        short_len = len((short or "").strip())

        # Apply larger limit
        mp.set_params(slug, {"max_tokens": "128"})
        long = await or_client.chat(messages=[{"role": "user", "content": prompt}], model=slug, temperature=0)
        long_len = len((long or "").strip())

        ok = (long_len > short_len) and (long_len >= 50)
        details = json.dumps({"short_preview": (short or "")[:40], "short_len": short_len, "long_len": long_len})
        return ok, details
    finally:
        # Clean up
        mp.set_params(slug, {})


async def main() -> int:
    ensure_root_cwd()
    inject_src()
    ok, info = await test_param_application()
    print(f"[ {'OK' if ok else 'FAIL'} ] Model params application via max_tokens: {info}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
