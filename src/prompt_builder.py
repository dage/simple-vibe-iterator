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
) -> Dict[str, Any]:
    """Shared context used for both code and vision templates."""
    raw = asdict(settings)
    # Templates themselves should not appear in the context to avoid recursive formatting issues.
    raw.pop("code_template", None)
    raw.pop("vision_template", None)
    ctx = raw
    ctx.update(
        {
            "html_input": html_input or "",
            "vision_output": interpretation_summary or "",
            "console_logs": "\n".join(console_logs or []),
            "html_diff": html_diff or "",
        }
    )
    return ctx


def build_vision_prompt(
    html_input: str,
    settings: TransitionSettings,
    console_logs: List[str] | None,
    html_diff: str,
) -> str:
    ctx = _build_template_context(
        html_input=html_input,
        settings=settings,
        interpretation_summary="",
        console_logs=console_logs,
        html_diff=html_diff,
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
) -> PromptPayload:
    ctx = _build_template_context(
        html_input=html_input,
        settings=settings,
        interpretation_summary=interpretation_summary,
        console_logs=console_logs,
        html_diff=html_diff,
    )
    code_prompt = settings.code_template.format(**ctx)

    # If keep_history is enabled and we have message history, use it
    if settings.keep_history and message_history:
        # Start with the cumulative message history
        messages = list(message_history)

        # Add the current iteration's user message
        if settings.mode == IterationMode.DIRECT_TO_CODER:
            # In direct mode, we still include the computed vision summary in the
            # textual prompt, but we also attach the screenshot so the coder can
            # reason directly over pixels. Keep the text intact.
            # Attach any provided images to the user message; coder must rely on raw pixels.
            parts: List[Dict[str, Any]] = []
            if code_prompt.strip():
                parts.append({"type": "text", "text": code_prompt})
            for asset in attachments:
                if asset.kind != "image" or not asset.path:
                    continue
                try:
                    data_url = orc.encode_image_to_data_url(asset.path)
                except Exception:
                    continue
                parts.append({"type": "image_url", "image_url": {"url": data_url}})
            if not parts:
                # Fallback to plain text to avoid empty prompt edge case.
                parts = [{"type": "text", "text": code_prompt}]
            messages.append({"role": "user", "content": parts})
        else:
            messages.append({"role": "user", "content": code_prompt})

        return PromptPayload(messages)

    # Original behavior when keep_history is disabled or no history available
    if settings.mode == IterationMode.DIRECT_TO_CODER:
        # Include the vision summary in the textual prompt and attach the image
        # so the coder has both sources of signal.
        # Attach any provided images to the user message; coder must rely on raw pixels.
        parts: List[Dict[str, Any]] = []
        if code_prompt.strip():
            parts.append({"type": "text", "text": code_prompt})
        for asset in attachments:
            if asset.kind != "image" or not asset.path:
                continue
            try:
                data_url = orc.encode_image_to_data_url(asset.path)
            except Exception:
                continue
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
        if not parts:
            # Fallback to plain text to avoid empty prompt edge case.
            parts = [{"type": "text", "text": code_prompt}]
        return PromptPayload([{"role": "user", "content": parts}])

    # Default: plain text prompt identical to legacy behavior.
    return PromptPayload([{"role": "user", "content": code_prompt}])


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
