# src/controller.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .interfaces import (
    AICodeService,
    BrowserService,
    IterationAsset,
    IterationEventListener,
    IterationNode,
    TransitionArtifacts,
    TransitionSettings,
    VisionService,
    ModelOutput,
    TemplateVariables,
    TemplateVariableSummary,
    TemplateFileVar,
)
from . import op_status
from . import task_registry
from .prompt_builder import PromptPayload, build_code_payload, build_vision_prompt
from .model_capabilities import get_image_limit, get_input_screenshot_interval
from . import feedback_presets
from . import or_client as orc
from .services import encode_file_to_data_url
from .image_downscale import load_scaled_image_bytes


@dataclass
class TransitionContext:
    html_input: str
    input_screenshot_paths: List[str] = field(default_factory=list)
    input_console_logs: List[str] = field(default_factory=list)
    input_limit_note: str = ""
    feedback_preset_id: str = ""
    input_screenshot_labels: List[str] = field(default_factory=list)
    code_model_image_support: Dict[str, bool] = field(default_factory=dict)


@dataclass
class InterpretationResult:
    summary: str = ""
    attachments: List[IterationAsset] = field(default_factory=list)


_DOUBLE_BRACE_PATTERN = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
_SINGLE_BRACE_PATTERN = re.compile(r"\{([A-Z0-9_]+)\}")
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_EXACT = {
    "application/json",
    "application/javascript",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
    "application/x-sh",
    "application/x-python",
    "application/x-shellscript",
}
_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".js",
    ".ts",
    ".css",
    ".html",
    ".htm",
    ".csv",
    ".py",
    ".sh",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".conf",
    ".toml",
    ".sql",
    ".xml",
    ".env",
    ".log",
}


def _format_bytes(num: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(max(0, num))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _looks_like_text(data: bytes) -> bool:
    if not data:
        return True
    sample = data[:4096]
    text_count = sum(1 for b in sample if 32 <= b <= 126 or b in (9, 10, 13))
    ratio = text_count / len(sample)
    return ratio >= 0.9


def _should_inject_file_as_text(file_entry: TemplateFileVar) -> bool:
    mime = (file_entry.mime_type or "").strip().lower()
    if any(mime.startswith(prefix) for prefix in _TEXT_MIME_PREFIXES):
        return True
    if mime in _TEXT_MIME_EXACT:
        return True
    suffix = Path(file_entry.filename or "").suffix.lower()
    if suffix in _TEXT_EXTENSIONS:
        return True
    if not mime and _looks_like_text(file_entry.data):
        return True
    return False


def _render_file_value(file_entry: TemplateFileVar) -> str:
    if _should_inject_file_as_text(file_entry):
        try:
            return file_entry.data.decode("utf-8")
        except UnicodeDecodeError:
            return file_entry.data.decode("utf-8", errors="replace")
    return encode_file_to_data_url(file_entry.data, file_entry.mime_type)


def _summaries_for_template_vars(template_vars: TemplateVariables) -> List[TemplateVariableSummary]:
    summaries: List[TemplateVariableSummary] = []
    for key, data in sorted(template_vars.file_vars.items()):
        injects_as_text = _should_inject_file_as_text(data)
        note = "text" if injects_as_text else "data-url"
        summaries.append(
            TemplateVariableSummary(
                key=key,
                kind="file",
                description=f"{data.mime_type} · {_format_bytes(data.size_bytes)}",
                size_bytes=data.size_bytes,
                mime_type=data.mime_type,
                filename=data.filename,
                notes=note,
            )
        )
    for key, text in sorted(template_vars.text_vars.items()):
        text_value = text or ""
        previews = text_value.strip().splitlines()
        head = previews[0] if previews else text_value[:80]
        preview = head[:80]
        if len(text_value) > len(preview):
            preview = preview.rstrip() + "…"
        summaries.append(
            TemplateVariableSummary(
                key=key,
                kind="text",
                description=preview,
                char_length=len(text_value),
            )
        )
    return sorted(summaries, key=lambda entry: entry.key)


def _summaries_to_prompt_text(entries: Sequence[TemplateVariableSummary]) -> str:
    if not entries:
        return "None"
    lines: List[str] = []
    for entry in entries:
        if entry.kind == "file":
            bits: List[str] = []
            if entry.mime_type:
                bits.append(entry.mime_type)
            bits.append(_format_bytes(entry.size_bytes))
            if entry.filename:
                bits.append(entry.filename)
            if entry.notes:
                bits.append(f"injected as {entry.notes}")
            detail = ", ".join(bits)
            lines.append(f"- {entry.key}: file ({detail})")
        else:
            preview = entry.description or ""
            lines.append(f"- {entry.key}: text ({entry.char_length} chars) preview=\"{preview}\"")
    return "\n".join(lines)


def _inject_template_variables(html: str, template_vars: TemplateVariables | None) -> tuple[str, set[str]]:
    if not html or template_vars is None:
        return html, set()

    files = template_vars.file_vars
    texts = template_vars.text_vars
    missing: set[str] = set()

    def _resolve(key: str, original: str) -> str:
        if key in files:
            try:
                file_entry = files[key]
                return _render_file_value(file_entry)
            except Exception:
                missing.add(key)
                return original
        if key in texts:
            return texts[key]
        missing.add(key)
        return original

    injected = _DOUBLE_BRACE_PATTERN.sub(lambda match: _resolve(match.group(1), match.group(0)), html)
    injected = _SINGLE_BRACE_PATTERN.sub(lambda match: _resolve(match.group(1), match.group(0)), injected)
    return injected, missing
async def _capture_input_context(
    html_input: str,
    settings: TransitionSettings,
    models: List[str],
    browser_service: BrowserService,
    vision_service: VisionService,
    *,
    template_vars_summary: str = "",
) -> tuple[TransitionContext, InterpretationResult, str]:
    context = TransitionContext(html_input=html_input or "")

    normalized_models = [slug for slug in models if slug]
    if not (html_input or "").strip():
        interpretation = InterpretationResult()
        return context, interpretation, ""

    preset_id = (getattr(settings, "feedback_preset_id", "") or "").strip()
    preset = feedback_presets.get_feedback_preset(preset_id) if preset_id else None
    model_image_support = await _detect_code_model_image_support(normalized_models) if normalized_models else {}
    context.code_model_image_support = dict(model_image_support)
    image_enabled_models = [slug for slug, supported in model_image_support.items() if supported]

    if preset:
        screenshots, console_logs, labels = await browser_service.run_feedback_preset(
            html_input,
            preset,
            worker="input",
        )
        context.input_screenshot_paths = list(screenshots)
        context.input_console_logs = list(console_logs)
        context.feedback_preset_id = preset.id
        context.input_screenshot_labels = list(labels)
    else:
        requested_shots = 1
        try:
            requested_shots = int(getattr(settings, "input_screenshot_count", 1) or 1)
        except Exception:
            requested_shots = 1
        requested_shots = max(1, requested_shots)
        effective_shots, limit_note = _resolve_input_screenshot_plan(
            settings,
            requested_shots,
            code_models_with_images=image_enabled_models,
        )

        if effective_shots > 0:
            screenshots, console_logs = await browser_service.render_and_capture(
                html_input,
                worker="input",
                capture_count=effective_shots,
                interval_seconds=get_input_screenshot_interval(),
            )
            context.input_screenshot_paths = list(screenshots)
            context.input_console_logs = list(console_logs)
            if limit_note:
                context.input_limit_note = limit_note
                try:
                    op_status.enqueue_notification(limit_note, color='warning', timeout=5000, close_button=True)
                except Exception:
                    pass

    interpretation = await _interpret_input(settings, context, vision_service, template_vars_summary=template_vars_summary)
    auto_feedback = _format_auto_feedback(context)
    return context, interpretation, auto_feedback


def _build_input_artifacts(
    context: TransitionContext,
    interpretation: InterpretationResult,
) -> TransitionArtifacts:
    assets: List[IterationAsset] = [
        IterationAsset(
            kind=asset.kind,
            path=asset.path,
            role=asset.role,
            metadata=dict(asset.metadata),
        )
        for asset in interpretation.attachments
        if asset.path
    ]

    analysis: Dict[str, str] = {}
    if interpretation.summary.strip():
        analysis["vision_summary"] = interpretation.summary
    if context.input_screenshot_paths:
        analysis["input_screenshot_count"] = str(len(context.input_screenshot_paths))
    if context.input_limit_note:
        analysis["input_screenshot_limit"] = context.input_limit_note
    if context.feedback_preset_id:
        analysis["feedback_preset_id"] = context.feedback_preset_id
    if context.input_screenshot_labels:
        try:
            analysis["input_screenshot_labels"] = json.dumps(context.input_screenshot_labels)
        except Exception:
            analysis["input_screenshot_labels"] = ",".join(context.input_screenshot_labels)

    return TransitionArtifacts(
        screenshot_filename="",
        console_logs=[],
        vision_output=interpretation.summary,
        input_screenshot_filenames=list(context.input_screenshot_paths),
        input_console_logs=list(context.input_console_logs),
        assets=assets,
        analysis=analysis,
    )


async def δ(
    html_input: str,
    settings: TransitionSettings,
    models: List[str],
    ai_service: AICodeService,
    browser_service: BrowserService,
    vision_service: VisionService,
    message_history: List[Dict[str, Any]] | None = None,
    context: TransitionContext | None = None,
    interpretation: InterpretationResult | None = None,
    auto_feedback: str | None = None,
    *,
    template_vars: TemplateVariables | None = None,
    template_vars_summary: str = "",
) -> tuple[Dict[str, Tuple[str, str, dict | None, TransitionArtifacts]], TransitionArtifacts | None, TransitionContext, InterpretationResult]:
    if not template_vars_summary and template_vars is not None:
        # Recompute a descriptive summary if the caller forgot to pass one
        template_vars_summary = _summaries_to_prompt_text(_summaries_for_template_vars(template_vars))
    if context is None or interpretation is None or auto_feedback is None:
        context, interpretation, auto_feedback = await _capture_input_context(
            html_input,
            settings,
            models,
            browser_service,
            vision_service,
            template_vars_summary=template_vars_summary,
        )

    async def _worker(model: str) -> Tuple[str, str, str, dict | None, TransitionArtifacts]:
        try:
            payload, template_context = build_code_payload(
                html_input=context.html_input,
                settings=settings,
                interpretation_summary=interpretation.summary,
                console_logs=context.input_console_logs,
                auto_feedback=auto_feedback,
                message_history=message_history,
                template_vars_summary=template_vars_summary,
            )
            prompt_payload = PromptPayload(_attach_input_screenshots(payload.messages, context, model))
            html_output, reasoning, meta = await ai_service.generate_html(
                prompt_payload,
                model,
                worker=model,
                template_context=template_context,
            )
            final_html, missing_keys = _inject_template_variables(html_output, template_vars)
            if missing_keys:
                try:
                    joined = ", ".join(sorted(missing_keys))
                    op_status.enqueue_notification(
                        f"Missing template variables: {joined}",
                        color="warning",
                        timeout=6000,
                        close_button=True,
                    )
                except Exception:
                    pass
            out_screenshots, out_console_logs = await browser_service.render_and_capture(final_html, worker=model)
            out_screenshot_path = out_screenshots[0] if out_screenshots else ""
            artifacts = _create_artifacts(
                model=model,
                context=context,
                interpretation=interpretation,
                screenshot_path=out_screenshot_path,
                console_logs=out_console_logs,
                vision_output=interpretation.summary,
            )
            return model, final_html, (reasoning or ""), (meta or None), artifacts
        finally:
            try:
                op_status.clear_phase(model)
            except Exception:
                pass

    task_entries: List[Tuple[str, asyncio.Task[Tuple[str, str, str, dict | None, TransitionArtifacts]]]] = []
    for model in models:
        task = asyncio.create_task(_worker(model))
        task_registry.register_task(model, task)
        task_entries.append((model, task))
    try:
        gathered = await asyncio.gather(*(task for _, task in task_entries), return_exceptions=True)
    finally:
        for model, _ in task_entries:
            task_registry.remove_task(model)

    results: Dict[str, Tuple[str, str, dict | None, TransitionArtifacts]] = {}
    failed_models: List[Tuple[str, Exception]] = []
    cancelled_models: List[str] = []

    for index, result in enumerate(gathered):
        model = task_entries[index][0]
        if isinstance(result, asyncio.CancelledError):
            cancelled_models.append(model)
            continue
        if isinstance(result, Exception):
            failed_models.append((model, result))
            print(f"❌ Model '{model}' failed: {type(result).__name__}: {result}")
            try:
                op_status.enqueue_notification(
                    f"Model '{model}' failed: {type(result).__name__}: {result}",
                    color='negative',
                    timeout=0,
                    close_button=True,
                )
            except Exception:
                pass
        else:
            model_name, html_output, reasoning_text, meta, artifacts = result
            results[model_name] = (html_output, reasoning_text, meta, artifacts)

    if not results:
        if cancelled_models and not failed_models:
            raise asyncio.CancelledError()
        model_names = [name for name, _ in failed_models]
        if cancelled_models:
            model_names.extend(cancelled_models)
        raise RuntimeError(f"All models failed: {', '.join(model_names)}")

    input_artifacts = None
    if (html_input or "").strip():
        input_artifacts = _build_input_artifacts(context, interpretation)

    return results, input_artifacts, context, interpretation


def _resolve_input_screenshot_plan(
    settings: TransitionSettings,
    requested: int,
    *,
    code_models_with_images: Sequence[str],
) -> tuple[int, str | None]:
    effective = max(1, requested)
    notes: List[str] = []

    relevant_models: List[str] = [slug.strip() for slug in code_models_with_images if slug.strip()]
    if settings.vision_model:
        relevant_models.append(settings.vision_model.strip())

    seen: set[str] = set()
    for slug in relevant_models:
        normalized = slug.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        limit = get_image_limit(normalized)
        if limit is None:
            continue
        if effective > limit:
            effective = min(effective, limit)
        if requested > limit:
            notes.append(f"{normalized} (max {limit})")

    if effective < 1:
        effective = 1

    note_text: str | None = None
    if notes and effective < requested:
        deduped: List[str] = []
        seen: set[str] = set()
        for entry in notes:
            if entry in seen:
                continue
            deduped.append(entry)
            seen.add(entry)
        joined = ", ".join(deduped)
        note_text = (
            "Input screenshots limited: "
            f"requested {requested} → using {effective} (limited by {joined})"
        )

    return effective, note_text


async def _interpret_input(
    settings: TransitionSettings,
    context: TransitionContext,
    vision_service: VisionService,
    *,
    template_vars_summary: str = "",
) -> InterpretationResult:
    result = InterpretationResult()

    input_paths = [p for p in context.input_screenshot_paths if (p or "").strip()]
    if not input_paths:
        return result

    for idx, path in enumerate(input_paths):
        result.attachments.append(
            IterationAsset(
                kind="image",
                path=path,
                role="input",
                metadata={
                    "stage": "before",
                    "index": str(idx),
                    "label": context.input_screenshot_labels[idx] if idx < len(context.input_screenshot_labels) else "",
                },
            )
        )

    # Run vision analysis for every iteration so code models get textual feedback
    # alongside any shared screenshots.

    auto_feedback = _format_auto_feedback(context)

    vision_prompt = build_vision_prompt(
        html_input=context.html_input,
        settings=settings,
        console_logs=context.input_console_logs,
        auto_feedback=auto_feedback,
        template_vars_summary=template_vars_summary,
    )
    analysis = await vision_service.analyze_screenshot(
        vision_prompt,
        input_paths,
        context.input_console_logs,
        settings.vision_model,
        worker="vision",
    )
    result.summary = analysis or ""
    return result


def _create_artifacts(
    *,
    model: str,
    context: TransitionContext,
    interpretation: InterpretationResult,
    screenshot_path: str,
    console_logs: List[str],
    vision_output: str,
) -> TransitionArtifacts:
    assets: List[IterationAsset] = [
        IterationAsset(
            kind=asset.kind,
            path=asset.path,
            role=asset.role,
            metadata=dict(asset.metadata),
        )
        for asset in interpretation.attachments
        if asset.path
    ]

    if screenshot_path:
        assets.append(
            IterationAsset(
                kind="image",
                path=screenshot_path,
                role="output",
                metadata={"model": model},
            )
        )

    analysis: Dict[str, str] = {}
    if vision_output.strip():
        analysis["vision_summary"] = vision_output

    if context.input_screenshot_paths:
        analysis["input_screenshot_count"] = str(len(context.input_screenshot_paths))
        if context.input_limit_note:
            analysis["input_screenshot_limit"] = context.input_limit_note
    if context.feedback_preset_id:
        analysis["feedback_preset_id"] = context.feedback_preset_id
    if context.input_screenshot_labels:
        try:
            analysis["input_screenshot_labels"] = json.dumps(context.input_screenshot_labels)
        except Exception:
            analysis["input_screenshot_labels"] = ",".join(context.input_screenshot_labels)

    return TransitionArtifacts(
        screenshot_filename=screenshot_path,
        console_logs=list(console_logs),
        vision_output=vision_output,
        input_screenshot_filenames=list(context.input_screenshot_paths),
        input_console_logs=list(context.input_console_logs),
        assets=assets,
        analysis=analysis,
    )


def _format_auto_feedback(context: TransitionContext) -> str:
    labels = [str(label).strip() for label in context.input_screenshot_labels if str(label).strip()]
    if not labels:
        return ""
    parts = [f"#{idx + 1}: {label}" for idx, label in enumerate(labels)]
    if context.feedback_preset_id:
        return f"Preset {context.feedback_preset_id} steps → " + ", ".join(parts)
    return "Auto feedback → " + ", ".join(parts)


def _attach_input_screenshots(
    messages: List[Dict[str, Any]],
    context: TransitionContext,
    model: str,
) -> List[Dict[str, Any]]:
    """Append input screenshots as image parts to the final user message when supported."""
    supports_images = bool(context.code_model_image_support.get(model))
    if not supports_images or not context.input_screenshot_paths or not messages:
        return messages

    updated: List[Dict[str, Any]] = list(messages)
    last = dict(updated[-1])
    content = last.get("content")

    if isinstance(content, list):
        parts = list(content)
    elif content is None:
        parts = []
    else:
        parts = [{"type": "text", "text": str(content)}]

    for path in context.input_screenshot_paths:
        if not (path or "").strip():
            continue
        scaled_bytes = load_scaled_image_bytes(path)
        data_source = scaled_bytes if scaled_bytes is not None else path
        try:
            data_url = orc.encode_image_to_data_url(data_source)
        except Exception:
            continue
        parts.append({"type": "image_url", "image_url": {"url": data_url}})

    if not parts:
        return messages

    last["content"] = parts
    updated[-1] = last
    return updated


async def _detect_code_model_image_support(models: Sequence[str]) -> Dict[str, bool]:
    normalized = [slug.strip() for slug in models if slug.strip()]
    if not normalized:
        return {}
    try:
        available = await orc.list_models(vision_only=True, limit=2000, force_refresh=False)
    except Exception:
        available = []
    image_ids = {getattr(m, "id", "") for m in available}
    return {slug: slug in image_ids for slug in normalized}


class IterationController:
    def __init__(
        self,
        ai_service: AICodeService,
        browser_service: BrowserService,
        vision_service: VisionService,
    ) -> None:
        self._ai_service = ai_service
        self._browser_service = browser_service
        self._vision_service = vision_service
        self._nodes: Dict[str, IterationNode] = {}
        self._listeners: List[IterationEventListener] = []
        self._template_vars = TemplateVariables()

    def add_listener(self, listener: IterationEventListener) -> None:
        self._listeners.append(listener)

    # Template variable management

    def normalize_template_key(self, raw_key: str) -> str:
        if raw_key is None:
            raise ValueError("Template variable key is required")
        cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(raw_key)).strip("_").upper()
        if not cleaned:
            raise ValueError("Template variable key must contain letters or numbers")
        if len(cleaned) > 120:
            cleaned = cleaned[:120]
        return cleaned

    def set_template_text_variable(self, key: str, value: str) -> TemplateVariableSummary:
        normalized = self.normalize_template_key(key)
        if not (value or "").strip():
            raise ValueError("Text value cannot be empty")
        self._template_vars.text_vars[normalized] = value
        self._template_vars.file_vars.pop(normalized, None)
        return self._summary_for_key(normalized)

    def set_template_file_variable(
        self,
        key: str,
        data: bytes,
        *,
        mime_type: str,
        filename: str = "",
    ) -> TemplateVariableSummary:
        normalized = self.normalize_template_key(key)
        if not data:
            raise ValueError("File content is empty")
        entry = TemplateFileVar(data=bytes(data), mime_type=(mime_type or "application/octet-stream"), filename=filename)
        self._template_vars.file_vars[normalized] = entry
        self._template_vars.text_vars.pop(normalized, None)
        return self._summary_for_key(normalized)

    def remove_template_variable(self, key: str) -> None:
        normalized = self.normalize_template_key(key)
        removed = False
        if normalized in self._template_vars.file_vars:
            self._template_vars.file_vars.pop(normalized, None)
            removed = True
        if normalized in self._template_vars.text_vars:
            self._template_vars.text_vars.pop(normalized, None)
            removed = True
        if not removed:
            raise ValueError(f"Template variable '{normalized}' not found")

    def rename_template_variable(self, current_key: str, new_key: str) -> TemplateVariableSummary:
        source = self.normalize_template_key(current_key)
        target = self.normalize_template_key(new_key)
        if source == target:
            return self._summary_for_key(source)
        if target in self._template_vars.file_vars or target in self._template_vars.text_vars:
            raise ValueError(f"Template variable '{target}' already exists")
        if source in self._template_vars.file_vars:
            entry = self._template_vars.file_vars.pop(source)
            self._template_vars.file_vars[target] = entry
        elif source in self._template_vars.text_vars:
            entry = self._template_vars.text_vars.pop(source)
            self._template_vars.text_vars[target] = entry
        else:
            raise ValueError(f"Template variable '{source}' not found")
        return self._summary_for_key(target)

    def list_template_variables(self) -> List[TemplateVariableSummary]:
        return _summaries_for_template_vars(self._template_vars)

    def template_vars_prompt_text(self) -> str:
        summaries = self.list_template_variables()
        return _summaries_to_prompt_text(summaries)

    def get_template_variables_snapshot(self) -> TemplateVariables:
        return TemplateVariables(
            file_vars=dict(self._template_vars.file_vars),
            text_vars=dict(self._template_vars.text_vars),
        )

    def _summary_for_key(self, key: str) -> TemplateVariableSummary:
        entries = _summaries_for_template_vars(self._template_vars)
        for entry in entries:
            if entry.key == key:
                return entry
        raise ValueError(f"Template variable '{key}' not found")

    def clear_template_variables(self) -> None:
        self._template_vars = TemplateVariables()

    # Data accessors
    def get_node(self, node_id: str) -> Optional[IterationNode]:
        return self._nodes.get(node_id)

    def get_children(self, node_id: str) -> List[IterationNode]:
        return [n for n in self._nodes.values() if n.parent_id == node_id]

    def get_root(self) -> Optional[IterationNode]:
        for node in self._nodes.values():
            if node.parent_id is None:
                return node
        return None

    def has_nodes(self) -> bool:
        return bool(self._nodes)

    def reset(self) -> None:
        self._nodes.clear()

    def _delete_descendants(self, node_id: str) -> None:
        # Gather descendants via BFS
        queue: List[str] = [node_id]
        to_delete: List[str] = []
        while queue:
            current = queue.pop(0)
            for child in self.get_children(current):
                to_delete.append(child.id)
                queue.append(child.id)
        for nid in to_delete:
            self._nodes.pop(nid, None)

    def _collect_message_history(self, node_id: str, model_slug: str) -> List[Dict[str, Any]]:
        """Collect cumulative message history from root to the given node."""
        chain: List[IterationNode] = []
        cur = self.get_node(node_id)
        while cur is not None:
            chain.append(cur)
            cur = self.get_node(cur.parent_id) if cur.parent_id else None
        chain.reverse()

        history: List[Dict[str, Any]] = []

        for node in chain:
            output = node.outputs.get(model_slug)
            if output is None and node.outputs:
                output = next(iter(node.outputs.values()))

            if output is None:
                continue

            snapshot = list(output.messages or [])
            if snapshot:
                prefix_len = 0
                max_compare = min(len(history), len(snapshot))
                while prefix_len < max_compare and history[prefix_len] == snapshot[prefix_len]:
                    prefix_len += 1
                if prefix_len < len(snapshot):
                    history.extend(snapshot[prefix_len:])
            assistant_content = output.assistant_response
            if assistant_content:
                assistant_message = {"role": "assistant", "content": assistant_content}
                if not history or history[-1] != assistant_message:
                    history.append(assistant_message)

        return history

    def _results_to_model_outputs(
        self,
        results: Dict[str, Tuple[str, str, dict | None, TransitionArtifacts]],
    ) -> Dict[str, ModelOutput]:
        outputs_dict: Dict[str, ModelOutput] = {}
        for m, triple in results.items():
            html, reasoning_text, meta, art = triple
            total_cost = None
            generation_time = None
            messages = None
            assistant_response = ""
            tool_call_count: int | None = None
            try:
                if isinstance(meta, dict):
                    tc = meta.get('total_cost')
                    gt = meta.get('generation_time')
                    total_cost = float(tc) if tc is not None else None
                    generation_time = float(gt) if gt is not None else None
                    messages = meta.get('messages')
                    assistant_response = str(meta.get('assistant_response', ''))
                    raw_tool_calls = meta.get('tool_call_count')
                    if raw_tool_calls is not None:
                        try:
                            tool_call_count = int(raw_tool_calls)
                        except Exception:
                            tool_call_count = None
            except Exception:
                total_cost = None
                generation_time = None
                messages = None
                assistant_response = ""
            outputs_dict[m] = ModelOutput(
                html_output=html,
                artifacts=art,
                reasoning_text=reasoning_text or "",
                total_cost=total_cost,
                generation_time=generation_time,
                messages=messages,
                assistant_response=assistant_response,
                tool_call_count=tool_call_count,
            )
        return outputs_dict

    # Unified apply: if from_node_id is None, create a root; otherwise iterate from given node
    async def apply_transition(self, from_node_id: str | None, settings: TransitionSettings, from_model_slug: str | None = None) -> str:
        # Compute parent id and html_input
        parent_id: str | None
        html_input: str
        models = [m.strip() for m in settings.code_model.split(',') if m.strip()]
        if not models:
            raise ValueError("No code model specified")

        base_model = models[0]
        if from_node_id is None:
            parent_id = None
            html_input = ""
        elif from_node_id not in self._nodes:
            raise ValueError(f"Node {from_node_id} not found")
        else:
            parent_id = from_node_id
            from_node = self._nodes[from_node_id]
            # Use specific model output if from_model_slug is provided, otherwise use base_model
            target_model = from_model_slug or base_model
            prev = from_node.outputs.get(target_model)
            if prev is None:
                prev = next(iter(from_node.outputs.values()))
            html_input = prev.html_output or from_node.html_input

        # Delete descendants only when iterating from an existing node
        if parent_id is not None:
            self._delete_descendants(parent_id)

        # Collect message history (single source of truth)
        message_history = self._collect_message_history(parent_id, base_model) if parent_id is not None else None

        template_vars_summary = self.template_vars_prompt_text()
        template_vars = self.get_template_variables_snapshot()

        # Run transition
        results, input_artifacts, ctx, interpretation = await δ(
            html_input=html_input,
            settings=settings,
            models=models,
            ai_service=self._ai_service,
            browser_service=self._browser_service,
            vision_service=self._vision_service,
            message_history=message_history,
            template_vars=template_vars,
            template_vars_summary=template_vars_summary,
        )

        auto_feedback_text = _format_auto_feedback(ctx)
        outputs_dict = self._results_to_model_outputs(results)
        node = IterationNode(
            parent_id=parent_id,
            html_input=html_input,
            outputs=outputs_dict,
            settings=settings,
            input_artifacts=input_artifacts,
            context=ctx,
            interpretation=interpretation,
            auto_feedback=auto_feedback_text,
        )
        self._nodes[node.id] = node
        await self._notify_node_created(node)
        return node.id

    async def rerun_node(self, node_id: str, settings: TransitionSettings) -> str:
        node = self._nodes.get(node_id)
        if node is None:
            raise ValueError(f"Node {node_id} not found")

        models = [m.strip() for m in settings.code_model.split(',') if m.strip()]
        if not models:
            raise ValueError("No code model specified")

        base_model = models[0]
        parent_id = node.parent_id
        html_input = node.html_input or ""

        self._delete_descendants(node_id)
        message_history = self._collect_message_history(parent_id, base_model) if parent_id is not None else None

        template_vars_summary = self.template_vars_prompt_text()
        template_vars = self.get_template_variables_snapshot()

        results, input_artifacts, ctx, interpretation = await δ(
            html_input=html_input,
            settings=settings,
            models=models,
            ai_service=self._ai_service,
            browser_service=self._browser_service,
            vision_service=self._vision_service,
            message_history=message_history,
            context=node.context,
            interpretation=node.interpretation,
            auto_feedback=node.auto_feedback,
            template_vars=template_vars,
            template_vars_summary=template_vars_summary,
        )

        node.outputs = self._results_to_model_outputs(results)
        node.input_artifacts = input_artifacts
        node.context = ctx or node.context
        node.interpretation = interpretation or node.interpretation
        node.settings = settings
        await self._notify_node_created(node)
        return node.id

    async def start_new_tree(self, settings: TransitionSettings) -> str:
        self.reset()
        node = IterationNode(
            parent_id=None,
            html_input="",
            outputs={},
            settings=settings,
            input_artifacts=None,
        )
        self._nodes[node.id] = node
        await self._notify_node_created(node)
        return node.id

    async def select_model(self, node_id: str, settings: TransitionSettings, source_model_slug: str) -> str:
        parent = self._nodes.get(node_id)
        if parent is None:
            raise ValueError(f"Node {node_id} not found")
        if not source_model_slug:
            raise ValueError("Source model slug required")

        prev = parent.outputs.get(source_model_slug)
        if prev is None and parent.outputs:
            prev = next(iter(parent.outputs.values()))
        if prev is None:
            raise ValueError(f"Model '{source_model_slug}' output unavailable")

        html_input = prev.html_output or parent.html_input or ""
        self._delete_descendants(node_id)

        models = [m.strip() for m in settings.code_model.split(',') if m.strip()]

        template_vars_summary = self.template_vars_prompt_text()

        context, interpretation, auto_feedback = await _capture_input_context(
            html_input,
            settings,
            models,
            self._browser_service,
            self._vision_service,
            template_vars_summary=template_vars_summary,
        )
        input_artifacts = _build_input_artifacts(context, interpretation) if context and interpretation else None

        child = IterationNode(
            parent_id=node_id,
            html_input=html_input,
            outputs={},
            settings=settings,
            input_artifacts=input_artifacts,
            source_model_slug=source_model_slug,
            context=context,
            interpretation=interpretation,
            auto_feedback=auto_feedback,
        )
        self._nodes[child.id] = child
        await self._notify_node_created(child)
        return child.id

    # Listener notifications
    async def _notify_node_created(self, node: IterationNode) -> None:
        for listener in self._listeners:
            await listener.on_node_created(node)
