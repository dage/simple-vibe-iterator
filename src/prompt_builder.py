from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable, List

from .interfaces import IterationAsset, TransitionSettings
from .image_downscale import load_scaled_image_bytes
from . import or_client as orc


class PromptPayload:
    """Simple wrapper around OpenRouter/OpenAI-style chat messages."""

    def __init__(self, messages: Iterable[Dict[str, Any]]):
        self._messages = [dict(m) for m in messages]

    @property
    def messages(self) -> List[Dict[str, Any]]:
        return list(self._messages)

    def __iter__(self):  # pragma: no cover - convenience only
        return iter(self._messages)


def _build_template_context(
    html_input: str,
    settings: TransitionSettings,
    interpretation_summary: str = "",
    console_logs: List[str] | None = None,
    auto_feedback: str = "",
) -> Dict[str, Any]:
    """Shared context used for both code and vision templates."""
    raw = asdict(settings)
    # Templates themselves should not appear in the context to avoid recursive formatting issues.
    raw.pop("code_template", None)
    raw.pop("code_system_prompt_template", None)
    raw.pop("code_first_prompt_template", None)
    raw.pop("vision_template", None)
    ctx = raw
    ctx.update(
        {
            "html_input": html_input or "",
            "vision_output": interpretation_summary or "",
            "console_logs": "\n".join(console_logs or []),
            "auto_feedback": auto_feedback or "",
        }
    )
    return ctx


def _strip_images_from_history(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            cleaned.append(dict(msg))
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            cleaned.append(dict(msg))
            continue
        filtered = [part for part in content if not (isinstance(part, dict) and part.get("type") == "image_url")]
        if not filtered:
            continue
        new_msg = dict(msg)
        new_msg["content"] = filtered
        cleaned.append(new_msg)
    return cleaned


def _format_template(template: str, ctx: Dict[str, Any]) -> str:
    if not template:
        return ""
    try:
        return template.format(**ctx)
    except Exception:
        return template


def _build_user_message(
    prompt_text: str,
    attachments: List[IterationAsset],
    include_images: bool,
) -> Dict[str, Any]:
    if include_images:
        parts: List[Dict[str, Any]] = []
        if prompt_text.strip():
            parts.append({"type": "text", "text": prompt_text})
        for asset in attachments:
            if asset.kind != "image" or not asset.path:
                continue
            scaled_bytes = load_scaled_image_bytes(asset.path)
            data_source = scaled_bytes if scaled_bytes is not None else asset.path
            try:
                data_url = orc.encode_image_to_data_url(data_source)
            except Exception:
                continue
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
        if parts:
            return {"role": "user", "content": parts}
    return {"role": "user", "content": prompt_text}


def build_vision_prompt(
    html_input: str,
    settings: TransitionSettings,
    console_logs: List[str] | None,
    auto_feedback: str = "",
) -> str:
    ctx = _build_template_context(
        html_input=html_input,
        settings=settings,
        interpretation_summary="",
        console_logs=console_logs,
        auto_feedback=auto_feedback,
    )
    return settings.vision_template.format(**ctx)


def build_code_payload(
    html_input: str,
    settings: TransitionSettings,
    interpretation_summary: str,
    console_logs: List[str] | None,
    attachments: Iterable[IterationAsset],
    message_history: List[Dict[str, Any]] | None = None,
    auto_feedback: str = "",
    allow_attachments: bool = False,
) -> tuple[PromptPayload, Dict[str, Any]]:
    attachments = list(attachments if allow_attachments else [])
    ctx = _build_template_context(
        html_input=html_input,
        settings=settings,
        interpretation_summary=interpretation_summary,
        console_logs=console_logs,
        auto_feedback=auto_feedback,
    )
    starting_from_blank = not (html_input or "").strip()
    is_first_message = (not message_history) and starting_from_blank
    base_iteration_prompt: str
    if is_first_message:
        first_prompt_template = getattr(settings, "code_first_prompt_template", "") or ""
        first_prompt = _format_template(first_prompt_template, ctx)
        base_iteration_prompt = first_prompt if first_prompt.strip() else _format_template(settings.code_template, ctx)
    else:
        base_iteration_prompt = _format_template(settings.code_template, ctx)
    system_template = getattr(settings, "code_system_prompt_template", "") or settings.code_template
    system_prompt = _format_template(system_template, ctx)

    messages = list(message_history or [])
    if messages and not allow_attachments:
        messages = _strip_images_from_history(messages)
    if not messages or str(messages[0].get("role")) != "system":
        if system_prompt.strip():
            messages.insert(0, {"role": "system", "content": system_prompt})
    user_message = _build_user_message(base_iteration_prompt, attachments, bool(attachments))
    messages.append(user_message)
    template_context = {
        "vision_template": settings.vision_template,
        "template_vars": ctx,
        "vision_model": settings.vision_model,
    }
    return PromptPayload(messages), template_context


def _strip_vision_mentions(prompt: str) -> str:
    lines = []
    for line in prompt.splitlines():
        if 'vision' in line.lower():
            continue
        lines.append(line)
    # Collapse excessive blank lines that may result from removals
    cleaned: List[str] = []
    blank_pending = False
    for line in lines:
        if line.strip():
            cleaned.append(line)
            blank_pending = False
        else:
            if not blank_pending:
                cleaned.append(line)
            blank_pending = True
    return "\n".join(cleaned)
