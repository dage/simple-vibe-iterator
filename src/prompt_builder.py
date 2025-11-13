from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable, List

from .interfaces import IterationAsset, IterationMode, TransitionSettings

try:
    from . import or_client as orc
except Exception:  # pragma: no cover
    import or_client as orc  # type: ignore


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
    html_diff: str = "",
    auto_feedback: str = "",
) -> Dict[str, Any]:
    """Shared context used for both code and vision templates."""
    raw = asdict(settings)
    # Templates themselves should not appear in the context to avoid recursive formatting issues.
    raw.pop("code_template", None)
    raw.pop("code_system_prompt_template", None)
    raw.pop("code_non_cumulative_template", None)
    raw.pop("vision_template", None)
    ctx = raw
    ctx.update(
        {
            "html_input": html_input or "",
            "vision_output": interpretation_summary or "",
            "console_logs": "\n".join(console_logs or []),
            "html_diff": html_diff or "",
            "auto_feedback": auto_feedback or "",
        }
    )
    return ctx


def _format_template(template: str, ctx: Dict[str, Any]) -> str:
    if not template:
        return ""
    try:
        return template.format(**ctx)
    except Exception:
        return template


def _build_user_message(
    mode: IterationMode,
    prompt_text: str,
    attachments: List[IterationAsset],
) -> Dict[str, Any]:
    if mode == IterationMode.DIRECT_TO_CODER:
        parts: List[Dict[str, Any]] = []
        if prompt_text.strip():
            parts.append({"type": "text", "text": prompt_text})
        for asset in attachments:
            if asset.kind != "image" or not asset.path:
                continue
            try:
                data_url = orc.encode_image_to_data_url(asset.path)
            except Exception:
                continue
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
        if not parts:
            parts = [{"type": "text", "text": prompt_text}]
        return {"role": "user", "content": parts}

    return {"role": "user", "content": prompt_text}


def build_vision_prompt(
    html_input: str,
    settings: TransitionSettings,
    console_logs: List[str] | None,
    html_diff: str,
    auto_feedback: str = "",
) -> str:
    ctx = _build_template_context(
        html_input=html_input,
        settings=settings,
        interpretation_summary="",
        console_logs=console_logs,
        html_diff=html_diff,
        auto_feedback=auto_feedback,
    )
    return settings.vision_template.format(**ctx)


def build_code_payload(
    html_input: str,
    settings: TransitionSettings,
    interpretation_summary: str,
    console_logs: List[str] | None,
    html_diff: str,
    attachments: Iterable[IterationAsset],
    message_history: List[Dict[str, Any]] | None = None,
    auto_feedback: str = "",
) -> PromptPayload:
    attachments = list(attachments)
    ctx = _build_template_context(
        html_input=html_input,
        settings=settings,
        interpretation_summary=interpretation_summary,
        console_logs=console_logs,
        html_diff=html_diff,
        auto_feedback=auto_feedback,
    )
    base_iteration_prompt = _format_template(settings.code_template, ctx)
    system_template = getattr(settings, "code_system_prompt_template", "") or settings.code_template
    system_prompt = _format_template(system_template, ctx)
    non_cumulative_template = getattr(settings, "code_non_cumulative_template", "") or settings.code_template
    non_cumulative_prompt = _format_template(non_cumulative_template, ctx)

    if settings.keep_history:
        messages = list(message_history or [])
        if not messages or str(messages[0].get("role")) != "system":
            if system_prompt.strip():
                messages.insert(0, {"role": "system", "content": system_prompt})
        user_message = _build_user_message(settings.mode, base_iteration_prompt, attachments)
        messages.append(user_message)
        return PromptPayload(messages)

    user_message = _build_user_message(settings.mode, non_cumulative_prompt, attachments)
    return PromptPayload([user_message])


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
