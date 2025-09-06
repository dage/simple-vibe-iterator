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
    # Use free DeepSeek for cost control and stability in tests
    slug = 'deepseek/deepseek-chat-v3.1:free'

    # Start clean
    mp.set_params(slug, {})
    try:
        # Ask for long output and measure size differences
        prompt = (
            "Respond ONLY with the letter X repeated as much as possible. "
            "No spaces, no punctuation, no explanations."
        )

        # Apply tight limit and measure completion tokens via usage
        mp.set_params(slug, {"max_tokens": "16"})
        short_meta = await or_client.chat_with_meta(messages=[{"role": "user", "content": prompt}], model=slug, temperature=0)
        short_ct = int((short_meta.get("usage", {}) or {}).get("completion_tokens") or 0)

        # Apply larger limit
        mp.set_params(slug, {"max_tokens": "256"})
        long_meta = await or_client.chat_with_meta(messages=[{"role": "user", "content": prompt}], model=slug, temperature=0)
        long_ct = int((long_meta.get("usage", {}) or {}).get("completion_tokens") or 0)

        ok = (long_ct > short_ct) and (long_ct >= max(40, short_ct + 10))
        details = json.dumps({
            "short_completion_tokens": short_ct,
            "long_completion_tokens": long_ct,
            "short_preview": (short_meta.get("content", "") or "")[:40]
        })
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
