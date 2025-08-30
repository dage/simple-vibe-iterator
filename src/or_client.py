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
import base64, mimetypes, asyncio, random, os

from openai import (
    AsyncOpenAI,
    APIStatusError,
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
)
from dotenv import load_dotenv
from dataclasses import dataclass
import time

# ---------------- Settings & client ---------------- #

TIMEOUT_SECONDS: float = 120.0


load_dotenv()


@dataclass(frozen=True)
class _Settings:
    api_key: str
    code_model: str
    vision_model: str
    base_url: str


@dataclass(frozen=True)
class ModelInfo:
    """Information about an OpenRouter programming model."""
    id: str                    # Model slug (e.g., "anthropic/claude-sonnet-4")
    name: str                  # Display name (e.g., "Anthropic: Claude Sonnet 4")
    has_text_input: bool       # Supports text input
    has_image_input: bool      # Supports image input (vision capability)
    prompt_price: float        # Price per million input tokens ($)
    completion_price: float    # Price per million output tokens ($)


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


# -------------- Model management -------------- #

# Global cache for all models
_MODELS_CACHE: Optional[List[ModelInfo]] = None
_CACHE_TIMESTAMP: Optional[float] = None
_CACHE_DURATION = 3600.0  # 1 hour


async def _fetch_all_models() -> List[ModelInfo]:
    """Fetch all available models from OpenRouter API using the existing client."""
    print("🔄 Fetching available models from OpenRouter API...")
    
    async def api_call():
        # Use the existing OpenAI client which already has proper headers and auth
        client = _client()
        response = await client.models.list()
        # Convert OpenAI response to dict format for compatibility
        return {"data": [model.model_dump() for model in response.data]}
    
    try:
        data = await _retry(api_call)
        models = [_parse_model_data(m) for m in data.get("data", []) if _parse_model_data(m)]
        print(f"✅ Successfully loaded {len(models)} available models")
        return models
    except Exception as e:
        print(f"❌ Failed to fetch models from API: {e}")
        raise RuntimeError(f"Failed to fetch models from OpenRouter API: {e}")


def _parse_model_data(data: Dict[str, Any]) -> Optional[ModelInfo]:
    """Parse raw model data from OpenRouter API into ModelInfo."""
    try:
        model_id = data.get("id", "")
        if not model_id:
            return None
            
        name = data.get("name", model_id)
        
        # Parse input modalities
        architecture = data.get("architecture", {})
        input_modalities = architecture.get("input_modalities", [])
        has_text_input = "text" in input_modalities
        has_image_input = "image" in input_modalities
        
        # Parse pricing (convert to $ per million tokens)
        pricing = data.get("pricing", {})
        prompt_price = float(pricing.get("prompt", "0")) * 1_000_000
        completion_price = float(pricing.get("completion", "0")) * 1_000_000
        
        return ModelInfo(
            id=model_id,
            name=name,
            has_text_input=has_text_input,
            has_image_input=has_image_input,
            prompt_price=prompt_price,
            completion_price=completion_price,
        )
    except Exception as e:
        print(f"⚠️  Failed to parse model data: {e}")
        return None


async def list_models(query: str = "", vision_only: bool = False, limit: int = 20, force_refresh: bool = False) -> List[ModelInfo]:
    """List available models with filtering and 1-hour caching.
    
    Args:
        query: Search query (matches model ID and name). Empty string returns all models.
        vision_only: If True, only return models with image input capability
        limit: Maximum number of results to return
        force_refresh: If True, bypass cache and fetch fresh data
        
    Returns:
        Filtered list of ModelInfo objects
    """
    global _MODELS_CACHE, _CACHE_TIMESTAMP
    
    # Load models with caching
    now = time.monotonic()
    cache_expired = (_CACHE_TIMESTAMP is None or (now - _CACHE_TIMESTAMP) > _CACHE_DURATION)
    
    if force_refresh or _MODELS_CACHE is None or cache_expired:
        _MODELS_CACHE = await _fetch_all_models()
        _CACHE_TIMESTAMP = now
    
    models = _MODELS_CACHE or []
    
    # Apply filters
    if vision_only:
        models = [m for m in models if m.has_image_input]
    
    query_lower = query.lower().strip()
    if query_lower:
        models = [m for m in models if query_lower in m.id.lower() or query_lower in m.name.lower()]
    
    return models[:limit]


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
