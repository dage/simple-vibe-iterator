# src/or_client.py
"""
Tiny OpenRouter client + stateful Conversation.

- Async-only (no streaming)
- Backoff on 429/5xx/timeout/network; never on 402 (insufficient credits)
- Images: accept local bytes/paths and encode to base64 data: URLs
- Attribution headers required via env (dotenv-based, no Pydantic)

Env (from .env or process):
  OPENROUTER_BASE_URL  - required; OpenRouter API endpoint
  OPENROUTER_API_KEY   - required

Docs: OpenRouter is OpenAI-compatible; images accept base64 data URLs; attribution headers required.
"""

from __future__ import annotations
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union
from pathlib import Path
from string import capwords
import asyncio
import base64
import json
import mimetypes
import os
import random
import sys
import time

from openai import (
    AsyncOpenAI,
    APIStatusError,
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
)
from dotenv import load_dotenv
from dataclasses import dataclass, field
import importlib.util

try:
    from . import context_data, logging as log_utils, op_status
    from .browser_tools_for_agents import BrowserToolProvider
    from .chrome_devtools_service import (
        create_chrome_devtools_service,
        ChromeDevToolsService,
        get_chrome_devtools_session_manager,
        get_current_devtools_agent_id,
    )
except (ImportError, ModuleNotFoundError):  # pragma: no cover - fallback for tooling/tests
    MODULE_DIR = Path(__file__).resolve().parent
    _spec = importlib.util.spec_from_file_location("src.logging_fallback", MODULE_DIR / "logging.py")
    assert _spec and _spec.loader
    log_utils = importlib.util.module_from_spec(_spec)  # type: ignore[assignment]
    sys.modules.setdefault(_spec.name, log_utils)
    _spec.loader.exec_module(log_utils)  # type: ignore[arg-type]
    _ctx_spec = importlib.util.spec_from_file_location("src.context_data_fallback", MODULE_DIR / "context_data.py")
    assert _ctx_spec and _ctx_spec.loader
    context_data = importlib.util.module_from_spec(_ctx_spec)  # type: ignore[assignment]
    sys.modules.setdefault(_ctx_spec.name, context_data)
    _ctx_spec.loader.exec_module(context_data)  # type: ignore[arg-type]
    BrowserToolProvider = None  # type: ignore[assignment]
    ChromeDevToolsService = None  # type: ignore[assignment]

    def create_chrome_devtools_service(*_args: Any, **_kwargs: Any) -> None:  # type: ignore[no-untyped-def]
        return None

    def get_chrome_devtools_session_manager() -> None:  # type: ignore[no-untyped-def]
        return None

    def get_current_devtools_agent_id() -> None:  # type: ignore[no-untyped-def]
        return None

    class _OpStatusStub:
        @staticmethod
        def set_phase(*_: Any, **__: Any) -> None:
            return None

    op_status = _OpStatusStub()  # type: ignore[assignment]

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
    created: int               # Unix timestamp when model was created
    supported_parameters: List[str] = field(default_factory=list)  # Supported parameters as reported by API


@lru_cache
def _settings() -> _Settings:
    # Models are sourced from YAML config (single source of truth)
    api_key = os.getenv("OPENROUTER_API_KEY")
    base_url = os.getenv("OPENROUTER_BASE_URL")

    # Import here to support both package import (src.or_client) and
    # top-level import (tests add src/ to sys.path). YAML is the single source of truth.
    try:
        cfg = _import_config()
        code_model = cfg.code_model
        vision_model = cfg.vision_model
    except Exception as exc:
        raise RuntimeError(f"Failed to load models from config.yaml: {exc}")

    missing = [
        name for name, val in [
            ("OPENROUTER_API_KEY", api_key),
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
_FETCH_LOCK = asyncio.Lock()
_BROWSER_TOOL_PROVIDER = BrowserToolProvider() if BrowserToolProvider else None
_BROWSER_TOOL_SPECS: List[Dict[str, Any]] = (
    _BROWSER_TOOL_PROVIDER.get_all_tools() if _BROWSER_TOOL_PROVIDER else []
)
DEFAULT_TOOL_SPECS: List[Dict[str, Any]] = list(_BROWSER_TOOL_SPECS)
_BROWSER_TOOL_NAMES = {
    spec.get("function", {}).get("name")
    for spec in _BROWSER_TOOL_SPECS
    if isinstance(spec, dict) and spec.get("function", {}).get("name")
}
_CHROME_DEVTOOLS_SESSION_MANAGER = (
    get_chrome_devtools_session_manager() if ChromeDevToolsService else None
)
_MODEL_INDEX: Dict[str, ModelInfo] = {}


async def _resolve_devtools_service() -> tuple[Optional[ChromeDevToolsService], bool]:
    manager = _CHROME_DEVTOOLS_SESSION_MANAGER
    if manager is not None:
        agent_id = get_current_devtools_agent_id()
        if agent_id:
            try:
                return await manager.get_session(agent_id), False
            except Exception:
                pass
    if not ChromeDevToolsService:
        return None, False
    return create_chrome_devtools_service(), True


async def _fetch_all_models() -> List[ModelInfo]:
    """Fetch all available models from OpenRouter API using the existing client."""
    
    async def api_call():
        # Use the existing OpenAI client which already has proper headers and auth
        client = _client()
        response = await client.models.list()
        # Convert OpenAI response to dict format for compatibility
        return {"data": [model.model_dump() for model in response.data]}
    
    try:
        data = await _retry(api_call)
        models = [_parse_model_data(m) for m in data.get("data", []) if _parse_model_data(m)]
        return models
    except Exception as e:
        raise RuntimeError(f"Failed to fetch models from OpenRouter API: {e}")


def _remember_models(models: Iterable[ModelInfo]) -> None:
    for model in models:
        if not model or not getattr(model, "id", None):
            continue
        _MODEL_INDEX[model.id] = model


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
        
        # Parse created timestamp (defaults to 0 if not provided)
        created = int(data.get("created", 0))

        # Parse supported parameters if present
        sp = data.get("supported_parameters") or []
        supported_parameters: List[str] = []
        if isinstance(sp, list):
            supported_parameters = [str(x) for x in sp if isinstance(x, (str, int, float))]
        
        return ModelInfo(
            id=model_id,
            name=name,
            has_text_input=has_text_input,
            has_image_input=has_image_input,
            prompt_price=prompt_price,
            completion_price=completion_price,
            created=created,
            supported_parameters=supported_parameters,
        )
    except Exception as e:
        print(f"⚠️  Failed to parse model data: {e}")
        return None


def _describe_tool_phase(name: str, worker: str, *, payload: Optional[Dict[str, Any]] = None) -> str:
    tool = (name or "tool").strip().lower() or "tool"
    target = (worker or "agent").strip() or "agent"
    if tool == "wait_for" and isinstance(payload, dict):
        selector = str(payload.get("selector", "") or "").strip()
        if selector:
            snippet = selector.replace("\n", " ").replace("\r", " ")
            snippet = snippet[:10]
            tool = f"{tool} {snippet}"
    return f"{tool}|{target}"


def _increment_tool_call_count() -> None:
    context_data.increment("tool_call_count")


async def _execute_tool_call(model_slug: str, name: str, arguments: str) -> str:
    try:
        payload = json.loads(arguments or "{}") if arguments else {}
    except json.JSONDecodeError:
        payload = {"code": arguments}

    if name in _BROWSER_TOOL_NAMES:
        worker = model_slug or "agent"
        tool_phase = _describe_tool_phase(name, worker, payload=payload)
        coding_phase = f"Coding|{worker}"
        op_status.set_phase(worker, tool_phase)
        try:
            _increment_tool_call_count()
            response = await _execute_browser_tool(name, payload)
        finally:
            op_status.set_phase(worker, coding_phase)
        response_text = json.dumps(response, ensure_ascii=False)
        try:
            log_utils.log_tool_call(
                model=model_slug,
                tool=name,
                code=json.dumps(payload, ensure_ascii=False),
                output=response_text,
            )
        except Exception:
            pass
        return response_text

    return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)


async def _execute_browser_tool(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    service, close_source = await _resolve_devtools_service()
    if service is None or not getattr(service, "enabled", False):
        return {"error": "chrome_devtools_disabled"}

    try:
        if name == "take_screenshot":
            return {"screenshot": await service.take_screenshot_mcp()}
        if name == "list_console_messages":
            return {"messages": await service.get_console_messages_mcp(level=payload.get("level"))}
        if name == "list_network_requests":
            return {
                "requests": await service.get_network_requests_mcp(filter_url=payload.get("filter"))
            }
        if name == "press_key":
            key = str(payload.get("key", "")).strip()
            if not key:
                return {"error": "missing key"}
            duration = int(payload.get("duration_ms", 100))
            return {"ok": await service.press_key_mcp(key=key, duration_ms=duration)}
        if name == "evaluate_script":
            script = str(payload.get("script", ""))
            if not script.strip():
                return {"error": "missing script"}
            return {"result": await service.evaluate_script_mcp(script)}
        if name == "wait_for":
            selector = str(payload.get("selector", "")).strip()
            if not selector:
                return {"error": "missing selector"}
            return {
                "ok": await service.wait_for_selector_mcp(
                    selector=selector,
                    timeout_ms=int(payload.get("timeout_ms", 5000)),
                )
            }
        if name == "performance_start_trace":
            return {"ok": await service.performance_trace_start_mcp()}
        if name == "performance_stop_trace":
            return {"result": await service.performance_trace_stop_mcp()}
        return {"error": f"unsupported_tool: {name}"}
    except Exception as exc:  # pragma: no cover - defensive logging
        return {"error": f"chrome_devtools_error: {exc}"}
    finally:
        if close_source:
            try:
                await service.aclose()
            except Exception:
                pass


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
    
    # Load models with caching and locking to prevent concurrent fetches
    async with _FETCH_LOCK:
        now = time.monotonic()
        cache_expired = (_CACHE_TIMESTAMP is None or (now - _CACHE_TIMESTAMP) > _CACHE_DURATION)
        
        if force_refresh or _MODELS_CACHE is None or cache_expired:
            print("🔄 Fetching available models from OpenRouter API...")
            try:
                _MODELS_CACHE = await _fetch_all_models()
                _CACHE_TIMESTAMP = now
                _remember_models(_MODELS_CACHE)
                print(f"✅ Successfully loaded {len(_MODELS_CACHE)} available models")
            except Exception as e:
                print(f"❌ Failed to fetch models from API: {e}")
                raise
        elif _MODELS_CACHE:
            _remember_models(_MODELS_CACHE)
    
    models = _MODELS_CACHE or []
    
    # Apply filters
    if vision_only:
        models = [m for m in models if m.has_image_input]
    
    query_lower = query.lower().strip()
    if query_lower:
        models = [m for m in models if query_lower in m.id.lower() or query_lower in m.name.lower()]
    
    return models[:limit]


async def _get_model_info(slug: Optional[str]) -> Optional[ModelInfo]:
    if not slug:
        return None
    info = _MODEL_INDEX.get(slug)
    if info is not None:
        return info
    try:
        await list_models(limit=2000)
    except Exception:
        return None
    return _MODEL_INDEX.get(slug)


def _model_supports_tools(info: Optional[ModelInfo]) -> bool:
    if not info:
        return False
    params = {str(p).strip().lower() for p in (info.supported_parameters or [])}
    tool_keys = {"tools", "function_calling", "function_call", "tool_choice", "parallel_tool_calls"}
    return bool(params.intersection(tool_keys))


# -------------- Internal helpers -------------- #

def _import_config():
    """Import config module with fallback for different import contexts."""
    try:
        from . import config as app_config  # type: ignore
        return app_config.get_config()
    except Exception:
        import config as app_config  # type: ignore
        return app_config.get_config()


def _import_model_params():
    """Import model_params module with fallback for different import contexts."""
    try:
        from . import model_params as mp  # type: ignore
        return mp
    except Exception:
        import model_params as mp  # type: ignore
        return mp


async def _merge_model_params(model: Optional[str], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Merge stored per-model params with kwargs, handling reasoning injection."""
    s = _settings()
    merged_kwargs = dict(kwargs)

    try:
        slug = model or s.code_model
        mp = _import_model_params()

        # Use cached models if present; otherwise fetch one page
        mlist = await list_models(force_refresh=False, limit=2000)
        sp = next((m.supported_parameters for m in mlist if m.id == slug), [])

        # Auto-inject reasoning parameters for models that actually support them
        # But skip if there are conflicting parameters that don't work well with reasoning
        conflicting_params = {"response_format", "tools", "tool_choice", "structured_outputs"}
        has_conflicts = any(param in merged_kwargs for param in conflicting_params)
        skip_reasoning = (
            has_conflicts or
            any(conflict in slug for conflict in ["openai/gpt-5", "google/gemini"])
        )

        if not skip_reasoning:
            if "include_reasoning" in sp and "include_reasoning" not in merged_kwargs:
                merged_kwargs["include_reasoning"] = True
            if "reasoning" in sp and "reasoning" not in merged_kwargs:
                merged_kwargs["reasoning"] = {"effort": "high"}

        stored = mp.get_sanitized_params_for_api(slug, sp)
        # Stored defaults < explicit kwargs
        for k, v in stored.items():
            if k not in merged_kwargs:
                merged_kwargs[k] = v
    except Exception:
        pass

    # Tool availability is managed centrally; ignore external overrides
    merged_kwargs.pop("tools", None)
    merged_kwargs.pop("tool_choice", None)

    return merged_kwargs

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


async def _retry(coro_fn, max_tries: int = 5, base: float = 0.5, retry_on=None):
    """
    Exponential backoff for transient conditions:
      - 429 (rate limit), 408 (timeout), 5xx, network/timeout errors.
    Never retries 402 (insufficient credits).
    """
    disable_retry = os.getenv("OPENROUTER_DISABLE_RETRY", "").strip() != ""
    attempts = 1 if disable_retry else max_tries
    for i in range(attempts):
        try:
            return await coro_fn()
        except (RateLimitError, APITimeoutError, APIConnectionError):
            # always back off for these
            if i == attempts - 1:
                raise
            await asyncio.sleep(base * (2 ** i) + random.random() * 0.1)
        except APIStatusError as e:
            code = getattr(e, "status_code", None)
            if code == 402:
                # no credits – surface immediately
                raise
            if code in (408, 429, 500, 502, 503, 504):
                if i == attempts - 1:
                    raise
                await asyncio.sleep(base * (2 ** i) + random.random() * 0.1)
            else:
                raise
        except Exception as e:
            # Optional predicate for non-OpenAI paths (e.g., httpx)
            should_retry = bool(retry_on(e)) if callable(retry_on) else False
            if not should_retry:
                raise
            if i == attempts - 1:
                raise
            await asyncio.sleep(base * (2 ** i) + random.random() * 0.1)


# -------------- Public stateless helpers -------------- #

async def chat(
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    **kwargs,
) -> str:
    """
    Stateless chat. Returns the assistant message string.
    """
    # Merge stored per-model params (if any), filtered to supported keys.
    # IMPORTANT: We pass all merged params via `extra_body` to avoid the OpenAI
    # Python SDK rejecting unknown provider-specific fields (e.g., "reasoning").
    merged_kwargs = await _merge_model_params(model, kwargs)

    # Use the richer helper and return only textual content for backward compatibility
    content, _meta = await chat_with_meta(messages=messages, model=model, **merged_kwargs)
    return content or ""


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


async def chat_with_meta(
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    **kwargs,
) -> tuple[str, Dict[str, Any]]:
    """
    Rich chat helper that returns both content and provider-specific fields like reasoning.

    Returns a tuple of (content, meta):
    - content: str (assistant message content)
    - meta: dict containing at minimum:
        - reasoning: str (provider-specific reasoning text if present)
        - total_cost: float | None (USD, from GET /generation)
        - generation_time: float | None (seconds, from GET /generation)
    """
    ctx_token = None
    if not context_data.has_context():
        ctx_token = context_data.reset_context({"tool_call_count": 0})
    else:
        context_data.set("tool_call_count", 0)
    try:
        return await _chat_with_meta_impl(messages=messages, model=model, **kwargs)
    finally:
        if ctx_token is not None:
            context_data.restore_context(ctx_token)


async def _chat_with_meta_impl(
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    **kwargs,
) -> tuple[str, Dict[str, Any]]:
    s = _settings()

    # Merge stored per-model params (if any), filtered to supported keys, as in chat()
    merged_kwargs = await _merge_model_params(model, kwargs)

    slug = model or s.code_model
    conversation = [dict(m) for m in messages]
    info = await _get_model_info(slug)
    tool_specs = list(DEFAULT_TOOL_SPECS) if _model_supports_tools(info) else []
    max_tool_hops = 30
    res = None
    last_tool_calls: Sequence[Any] = []
    completed = False

    # The app has no streaming UI, so always request full responses.
    use_streaming = False

    for _ in range(max_tool_hops):
        async def call():
            payload = {
                "model": slug,
                "messages": conversation,
                "stream": use_streaming,
                "extra_body": merged_kwargs or None,
            }
            if tool_specs:
                payload["tools"] = tool_specs
                payload["tool_choice"] = "auto"
            return await _client().chat.completions.create(**payload)

        res = await _retry(call)
        msg = res.choices[0].message
        tool_calls = list(getattr(msg, "tool_calls", []) or [])
        last_tool_calls = tool_calls
        if tool_specs and tool_calls:
            conversation.append(msg.model_dump(exclude_none=True))
            for tc in tool_calls:
                fn = getattr(tc, "function", None)
                if not fn:
                    continue
                output = await _execute_tool_call(slug, fn.name, fn.arguments)
                conversation.append({
                    "role": "tool",
                    "tool_call_id": getattr(tc, "id", ""),
                    "name": fn.name,
                    "content": output,
                    "arguments": getattr(fn, "arguments", None),
                })
            continue
        completed = True
        break

    if not completed and tool_specs and last_tool_calls:
        async def final_call():
            payload = {
                "model": slug,
                "messages": conversation,
                "stream": use_streaming,
                "extra_body": merged_kwargs or None,
            }
            return await _client().chat.completions.create(**payload)

        res = await _retry(final_call)
        msg = res.choices[0].message
        tool_calls = list(getattr(msg, "tool_calls", []) or [])
        if tool_calls:
            raise RuntimeError("Exceeded max tool hops without final completion")
        conversation.append(msg.model_dump(exclude_none=True))

    if res is None:
        raise RuntimeError("Failed to obtain response from OpenRouter")
    content: str = ""
    reasoning: str = ""
    req_id: Optional[str] = None
    try:
        req_id = getattr(res, "id", None)
        msg = res.choices[0].message
        # Extract content (string or list-of-text parts)
        c = getattr(msg, "content", None)
        if isinstance(c, str):
            content = c
        elif isinstance(c, list):
            texts: List[str] = []
            for part in c:
                if isinstance(part, dict):
                    t = part.get("text")
                    if isinstance(t, str):
                        texts.append(t)
            content = "\n".join(t for t in texts if t)
        else:
            content = str(c or "")
        # Extract provider-specific 'reasoning' if present (OpenRouter-specific)
        r = getattr(msg, "reasoning", None)
        if isinstance(r, str):
            reasoning = r
        elif isinstance(r, list):  # some providers may return structured parts
            reasoning = "\n".join(str(x) for x in r if x)
        elif r is not None:
            try:
                # best-effort stringify
                reasoning = str(r)
            except Exception:
                reasoning = ""
    except Exception:
        content = content or ""
        reasoning = reasoning or ""

    # Token usage is intentionally omitted to keep API minimal per product direction
    # Fetch additional generation metadata from OpenRouter's native endpoint
    total_cost: Optional[float] = None
    generation_time: Optional[float] = None
    try:
        if req_id:
            import httpx  # OpenAI SDK depends on httpx, so it's available
            url = f"{s.base_url}/generation"
            headers = {"Authorization": f"Bearer {s.api_key}"}

            async def _get_generation():
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
                    resp = await http.get(url, params={"id": req_id}, headers=headers)
                # Raise for non-2xx to unify handling
                resp.raise_for_status()
                return resp.json() or {}

            def _retry_on(exc: Exception) -> bool:
                try:
                    # Retry on typical httpx network errors and on specific status codes
                    if isinstance(exc, httpx.HTTPStatusError):
                        code = getattr(getattr(exc, "response", None), "status_code", None)
                        return code in (404, 408, 429, 500, 502, 503, 504)
                    if isinstance(exc, httpx.HTTPError):
                        return True
                except Exception:
                    pass
                return False

            data = await _retry(_get_generation, max_tries=6, base=0.25, retry_on=_retry_on)
            d = data.get("data", data) if isinstance(data, dict) else {}
            # According to docs, keys are 'total_cost' (USD) and 'generation_time' (seconds)
            tc = d.get("total_cost")
            gt = d.get("generation_time")
            try:
                total_cost = float(tc) if tc is not None else None
            except Exception:
                total_cost = None
            try:
                gtf = float(gt) if gt is not None else None
                if gtf is not None:
                    # OpenRouter returns generation_time in milliseconds; always convert to seconds
                    generation_time = gtf / 1000.0
            except Exception:
                generation_time = None
    except Exception:
        # Best-effort: do not fail the main call if metadata fetch fails
        pass

    meta: Dict[str, Any] = {
        "reasoning": reasoning or "",
        "total_cost": total_cost,
        "generation_time": generation_time,
    }
    meta["messages"] = conversation
    meta["tool_call_count"] = context_data.get("tool_call_count", 0)
    return (content or "", meta)
