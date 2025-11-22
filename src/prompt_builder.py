from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable, List

from .interfaces import TransitionSettings


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
    template_vars_summary: str = "",
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
            "template_vars_list": (template_vars_summary or "None"),
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
) -> Dict[str, Any]:
    return {"role": "user", "content": prompt_text}


def build_vision_prompt(
    html_input: str,
    settings: TransitionSettings,
    console_logs: List[str] | None,
    auto_feedback: str = "",
    template_vars_summary: str = "",
) -> str:
    ctx = _build_template_context(
        html_input=html_input,
        settings=settings,
        interpretation_summary="",
        console_logs=console_logs,
        auto_feedback=auto_feedback,
        template_vars_summary=template_vars_summary,
    )
    return settings.vision_template.format(**ctx)


def build_code_payload(
    html_input: str,
    settings: TransitionSettings,
    interpretation_summary: str,
    console_logs: List[str] | None,
    message_history: List[Dict[str, Any]] | None = None,
    auto_feedback: str = "",
    template_vars_summary: str = "",
) -> tuple[PromptPayload, Dict[str, Any]]:
    ctx = _build_template_context(
        html_input=html_input,
        settings=settings,
        interpretation_summary=interpretation_summary,
        console_logs=console_logs,
        auto_feedback=auto_feedback,
        template_vars_summary=template_vars_summary,
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
    if messages:
        messages = _strip_images_from_history(messages)
    if not messages or str(messages[0].get("role")) != "system":
        if system_prompt.strip():
            messages.insert(0, {"role": "system", "content": system_prompt})
    user_message = _build_user_message(base_iteration_prompt)
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
