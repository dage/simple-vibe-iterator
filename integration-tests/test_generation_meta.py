from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
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


async def test_generation_metadata() -> Tuple[bool, str]:
    import or_client

    # Use a low-cost/free model commonly used in other tests
    slug = 'deepseek/deepseek-chat-v3.1:free'
    prompt = 'Reply with the word: hello'

    content, meta = await or_client.chat_with_meta(
        messages=[{"role": "user", "content": prompt}],
        model=slug,
        temperature=0,
        max_tokens=32,
    )

    # Validate content
    ok_content = isinstance(content, str) and len(content.strip()) > 0

    # Validate additional metadata retrieved from GET /generation
    total_cost = meta.get('total_cost', None)
    generation_time = meta.get('generation_time', None)

    ok_cost = (total_cost is None) or (isinstance(total_cost, (int, float)) and total_cost >= 0)
    ok_time = (generation_time is None) or (isinstance(generation_time, (int, float)) and generation_time >= 0)

    ok = ok_content and ok_cost and ok_time
    details = json.dumps({
        'content_preview': (content or '')[:40],
        'total_cost': total_cost,
        'generation_time': generation_time,
    })
    return ok, details


async def main() -> int:
    ensure_root_cwd()
    inject_src()
    ok, info = await test_generation_metadata()
    print(f"[ {'OK' if ok else 'FAIL'} ] OpenRouter generation metadata: {info}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
