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


os.environ.setdefault("OPENROUTER_DISABLE_RETRY", "1")


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

    preferred_slugs = []
    # Prefer whatever the app is configured to use so behavior matches production
    if getattr(cfg, "code_model", None):
        preferred_slugs.append(cfg.code_model)
    # Fallback to a well-behaved free tier model known to support max_tokens
    preferred_slugs.append('x-ai/grok-4-fast')

    slug = preferred_slugs[-1]
    try:
        models = await or_client.list_models(force_refresh=False, limit=2000)
        supports = {
            m.id: {param.lower() for param in (m.supported_parameters or [])}
            for m in models
        }
        for candidate in preferred_slugs:
            params = supports.get(candidate)
            if params and 'max_tokens' in params:
                slug = candidate
                break
    except Exception:
        # Best-effort selection only; keep fallback slug on lookup errors
        pass

    # Start clean
    mp.set_params(slug, {})
    try:
        # Ask for long output and measure size differences
        prompt = (
            "Give a detailed explanation of how rainbows form. "
            "Use as much depth as the token limit allows while staying under 120 words."
        )

        # Apply tight limit and compare content lengths
        mp.set_params(slug, {"max_tokens": "64"})
        short_content, short_meta = await or_client.chat_with_meta(messages=[{"role": "user", "content": prompt}], model=slug, temperature=0)
        short_len = len((short_content or '').strip())

        # Apply larger limit
        mp.set_params(slug, {"max_tokens": "256"})
        long_content, long_meta = await or_client.chat_with_meta(messages=[{"role": "user", "content": prompt}], model=slug, temperature=0)
        long_len = len((long_content or '').strip())

        ok = (
            short_len >= 40 and
            long_len > short_len and
            long_len >= short_len + 40
        )
        details = json.dumps({
            "model": slug,
            "short_len": short_len,
            "long_len": long_len,
            "short_preview": (short_content or "")[:40]
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
