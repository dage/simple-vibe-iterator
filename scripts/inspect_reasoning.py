from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def guess_format(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "empty"
    # Heuristic HTML: presence of tags like <p>, <div>, <ul>, <ol>, <li>, <h1>, <code>, etc.
    if re.search(r"<\s*(p|div|ul|ol|li|h[1-6]|code|pre|span|br|strong|em)[^>]*>", t, re.I):
        return "html"
    # Heuristic Markdown: headings, lists, fenced code, bold/italic markers in typical patterns
    if re.search(r"^\s{0,3}#\s+", t, re.M):
        return "markdown"
    if re.search(r"^\s*[-*+]\s+\w", t, re.M):
        return "markdown"
    if re.search(r"```[a-zA-Z0-9_\-]*\n", t):
        return "markdown"
    if re.search(r"\*\*[^\n]+\*\*", t):
        return "markdown"
    return "plain"


async def run_once(slug: str, prompt: str) -> dict:
    import or_client  # type: ignore

    # Ask for visible reasoning with a high effort hint.
    # We send via kwargs to avoid mutating stored per-model params.
    content, meta = await or_client.chat_with_meta(
        messages=[{"role": "user", "content": prompt}],
        model=slug,
        include_reasoning=True,
        reasoning={"effort": "high"},
        temperature=0.2,
        max_tokens=512,
    )
    content = content or ""
    reasoning = meta.get("reasoning", "") or ""
    fmt = guess_format(reasoning)
    return {
        "model": slug,
        "format": fmt,
        "content_len": len(content.strip()),
        "reasoning_len": len(reasoning.strip()),
        "reasoning": reasoning,
    }


async def main() -> int:
    # Prefer free tier where available for DeepSeek
    models = [
        "openai/gpt-5-mini",
        "x-ai/grok-code-fast-1",
        "deepseek/deepseek-chat-v3.1:free",
        "qwen/qwen3-30b-a3b-thinking-2507",
    ]

    prompt = (
        "Provide a concise 2-3 step plan to build a minimal static webpage with "
        "a title, a header, and a short paragraph."
    )

    print("Inspecting reasoning output for models...", flush=True)
    results = []
    for slug in models:
        try:
            print(f"\n--- {slug} ---", flush=True)
            r = await run_once(slug, prompt)
            results.append(r)
            print(json.dumps({k: v for k, v in r.items() if k != 'reasoning'}, ensure_ascii=False, indent=2))
            print("\nRAW REASONING:\n" + (r["reasoning"] or "<empty>"))
        except Exception as e:
            print(f"[ERROR] {slug}: {e}")
            results.append({"model": slug, "error": str(e)})

    # Summary table
    print("\nSummary:")
    print("model,format,reasoning_len,content_len,error")
    for r in results:
        if "error" in r:
            print(f"{r['model']},error,0,0,{r['error']}")
        else:
            print(f"{r['model']},{r['format']},{r['reasoning_len']},{r['content_len']},")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
