# src/or_client.py
"""
Tiny OpenRouter client + stateful Conversation.

- Async-only (no streaming)
- Backoff on 429/5xx/timeout/network; never on 402 (insufficient credits)
- Images: accept local bytes/paths and encode to base64 data: URLs
- Attribution headers required via env (dotenv-based, no Pydantic)

Env (from .env or process):
  OPENROUTER_BASE_URL  - required; OpenRouter API endpoint
  VIBES_API_KEY        - required

Docs: OpenRouter is OpenAI-compatible; images accept base64 data URLs; attribution headers required.
"""

from __future__ import annotations
from functools import lru_cache
from typing import Any, Dict, List, Optional, Union
from pathlib import Path
import base64, mimetypes, asyncio, random, os, re

from openai import (
    AsyncOpenAI,
    APIStatusError,
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
)
from dotenv import load_dotenv
from dataclasses import dataclass

# ---------------- Settings & client ---------------- #

TIMEOUT_SECONDS: float = 120.0


load_dotenv()


@dataclass(frozen=True)
class _Settings:
    api_key: str
    code_model: str
    vision_model: str
    base_url: str
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.strip().lower()).strip("-")


@lru_cache
def _settings() -> _Settings:
    # Models are sourced from YAML config (single source of truth)
    api_key = os.getenv("VIBES_API_KEY")
    base_url = os.getenv("OPENROUTER_BASE_URL")

    # Import here to support both package import (src.or_client) and
    # top-level import (tests add src/ to sys.path). YAML is the single source of truth.
    try:
        try:
            from . import config as app_config  # type: ignore
        except Exception:
            import config as app_config  # type: ignore
        cfg = app_config.get_config()
        code_model = cfg.code_model
        vision_model = cfg.vision_model
    except Exception as exc:
        raise RuntimeError(f"Failed to load models from config.yaml: {exc}")

    missing = [
        name for name, val in [
            ("VIBES_API_KEY", api_key),
            ("OPENROUTER_BASE_URL", base_url),
        ] if not val
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing) + ". Configure them in .env."
        )

    return _Settings(
        api_key=api_key,
        code_model=code_model,
        vision_model=vision_model,
        base_url=base_url,
    )


@lru_cache
def _client() -> AsyncOpenAI:
    s = _settings()
    headers: Dict[str, str] = {
        "X-Title": "simple-vibe-iterator",
        "HTTP-Referer": "https://simple-vibe-iterator.local",
    }
    return AsyncOpenAI(
        api_key=s.api_key,
        base_url=s.base_url,
        timeout=TIMEOUT_SECONDS,
        default_headers=headers,
    )


# -------------- Internal helpers -------------- #

def _guess_mime(path: Union[str, Path]) -> str:
    mt, _ = mimetypes.guess_type(str(path))
    return mt or "image/png"


def encode_image_to_data_url(
    data: Union[bytes, str, Path],
    mime: Optional[str] = None,
) -> str:
    """
    Accepts raw bytes or a filesystem path; returns a data: URL suitable
    for OpenAI/OpenRouter Chat Completions image input.
    """
    if isinstance(data, (str, Path)) and Path(str(data)).exists():
        p = Path(str(data))
        raw = p.read_bytes()
        mime = mime or _guess_mime(p)
    elif isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
        mime = mime or "image/png"
    elif isinstance(data, str) and data.startswith("data:"):
        # already a data URL; pass through
        return data
    else:
        raise ValueError("encode_image_to_data_url expects bytes, data: URL, or existing file path")

    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


async def _retry(coro_fn, max_tries: int = 5, base: float = 0.5):
    """
    Exponential backoff for transient conditions:
      - 429 (rate limit), 408 (timeout), 5xx, network/timeout errors.
    Never retries 402 (insufficient credits).
    """
    for i in range(max_tries):
        try:
            return await coro_fn()
        except (RateLimitError, APITimeoutError, APIConnectionError) as e:
            # always back off for these
            if i == max_tries - 1:
                raise
            await asyncio.sleep(base * (2 ** i) + random.random() * 0.1)
        except APIStatusError as e:
            code = getattr(e, "status_code", None)
            if code == 402:
                # no credits – surface immediately
                raise
            if code in (408, 429, 500, 502, 503, 504):
                if i == max_tries - 1:
                    raise
                await asyncio.sleep(base * (2 ** i) + random.random() * 0.1)
            else:
                raise


# -------------- Public stateless helpers -------------- #

async def chat(
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    **kwargs,
) -> str:
    """
    Stateless chat. Returns the assistant message string.
    """
    s = _settings()

    async def call():
        return await _client().chat.completions.create(
            model=model or s.code_model,
            messages=messages,
            **kwargs,
        )

    res = await _retry(call)
    # Minimal extraction following OpenAI-compatible shape
    try:
        content = res.choices[0].message.content
    except Exception:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: List[str] = []
        for part in content:
            if isinstance(part, dict):
                t = part.get("text")
                if isinstance(t, str):
                    texts.append(t)
        return "\n".join(t for t in texts if t)
    return str(content or "")


async def vision_single(
    prompt: str,
    image: Union[bytes, str, Path],
    model: Optional[str] = None,
    **kwargs,
) -> str:
    """
    Stateless single-image helper. Encodes to data URL and sends one user message
    containing prompt + image. Prefer using Conversation for multi-turn or multi-image.
    """
    s = _settings()
    data_url = encode_image_to_data_url(image)
    msgs = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
    }]
    return await chat(msgs, model=model or s.vision_model, **kwargs)
