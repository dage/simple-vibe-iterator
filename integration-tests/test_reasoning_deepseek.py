from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
import tempfile
from statistics import mean
from typing import Tuple


os.environ.setdefault("OPENROUTER_DISABLE_RETRY", "1")


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


async def test_deepseek_reasoning_vs_plain() -> Tuple[bool, str]:
    """Compare output lengths with and without stored reasoning parameters.

    Some providers emit short reasoning traces even without explicit opts, so the
    assertion is based on aggregate output length rather than strict absence.
    """
    import config as app_config
    import or_client

    # Use a temp params file to avoid cross-test interference
    tmp = tempfile.NamedTemporaryFile(prefix="model_params_", suffix=".json", delete=False)
    os.environ["MODEL_PARAMS_PATH"] = tmp.name

    import importlib
    mp = importlib.import_module('model_params')

    cfg = app_config.get_config()
    preferred_slugs = [
        'qwen/qwen3-next-80b-a3b-thinking',
        'deepseek/deepseek-chat-v3.1:free',
    ]
    if getattr(cfg, "code_model", None):
        preferred_slugs.append(cfg.code_model)
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
            if params and {'include_reasoning', 'reasoning'}.issubset(params):
                slug = candidate
                break
    except Exception:
        pass

    prompt = (
        "Write a concise answer (<= 120 words) explaining how rainbows form."
    )

    # Prepare messages once
    msgs = [{"role": "user", "content": prompt}]

    # Helper to run N trials and return lengths
    async def run_trials(n: int, with_reasoning: bool) -> list[int]:
        # Reset stored params each round to ensure clean state
        if with_reasoning:
            # Store model-specific params in the per-model store
            # Include both 'include_reasoning' and a 'reasoning' payload to ensure activation
            mp.set_params(slug, {
                "include_reasoning": "true",
                "reasoning": json.dumps({"effort": "high"}),
                "max_tokens": "256",
                "temperature": "0.2",
            })
        else:
            mp.set_params(slug, {
                "max_tokens": "256",
                "temperature": "0.2",
            })

        lengths: list[int] = []
        for _ in range(n):
            # Use rich meta to also verify presence/absence of reasoning
            content, meta = await or_client.chat_with_meta(messages=msgs, model=slug)
            content = content or ''
            reasoning = meta.get('reasoning', '') or ''
            # For reasoning-enabled runs, compare content + reasoning; otherwise content only
            total = (content + (reasoning if with_reasoning else '')).strip()
            lengths.append(len(total))
        return lengths

    try:
        plain_lengths = await run_trials(2, with_reasoning=False)
        reasoning_lengths = await run_trials(3, with_reasoning=True)

        avg_plain = int(mean(plain_lengths) if plain_lengths else 0)
        avg_reason = int(mean(reasoning_lengths) if reasoning_lengths else 0)

        ok = (avg_reason > avg_plain) and (avg_reason - avg_plain >= 20)
        details = json.dumps({
            "model": slug,
            "plain": plain_lengths,
            "reasoning": reasoning_lengths,
            "avg_plain": avg_plain,
            "avg_reason": avg_reason,
        })
        return ok, details
    finally:
        # Clean up stored params for the model
        mp.set_params(slug, {})


async def main() -> int:
    ensure_root_cwd()
    inject_src()
    ok, info = await test_deepseek_reasoning_vs_plain()
    print(f"[ {'OK' if ok else 'FAIL'} ] DeepSeek reasoning vs plain length: {info}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
