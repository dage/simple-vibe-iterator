# src/controller.py
from __future__ import annotations

import asyncio
import difflib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .interfaces import (
    AICodeService,
    BrowserService,
    IterationAsset,
    IterationEventListener,
    IterationNode,
    IterationMode,
    TransitionArtifacts,
    TransitionSettings,
    VisionService,
    ModelOutput,
)
from . import op_status
from .prompt_builder import build_code_payload, build_vision_prompt
from .model_capabilities import get_image_limit, get_input_screenshot_interval

try:  # pragma: no cover - import differs during tests
    from . import or_client as orc
except Exception:  # pragma: no cover
    import or_client as orc  # type: ignore



def _compute_html_diff(html_input: str, html_output: str) -> str:
    """Compute a unified diff between html_input and html_output."""
    if not html_input.strip() and not html_output.strip():
        return ""
    if not html_input.strip():
        return f"+ Added {len(html_output.splitlines())} lines"
    if not html_output.strip():
        return f"- Removed {len(html_input.splitlines())} lines"
    
    input_lines = html_input.splitlines(keepends=True)
    output_lines = html_output.splitlines(keepends=True)
    
    diff = list(difflib.unified_diff(
        input_lines, 
        output_lines, 
        fromfile="previous", 
        tofile="current",
        n=3
    ))
    
    return "".join(diff)


@dataclass
class TransitionContext:
    html_input: str
    html_diff: str
    input_screenshot_paths: List[str] = field(default_factory=list)
    input_console_logs: List[str] = field(default_factory=list)
    input_limit_note: str = ""


@dataclass
class InterpretationResult:
    summary: str = ""
    attachments: List[IterationAsset] = field(default_factory=list)
async def δ(
    html_input: str,
    settings: TransitionSettings,
    models: List[str],
    ai_service: AICodeService,
    browser_service: BrowserService,
    vision_service: VisionService,
    html_diff: str = "",
    message_history: List[Dict[str, Any]] | None = None,
) -> Dict[str, Tuple[str, str, dict | None, TransitionArtifacts]]:
    context = TransitionContext(html_input=html_input or "", html_diff=html_diff or "")

    requested_shots = 1
    try:
        requested_shots = int(getattr(settings, "input_screenshot_count", 1) or 1)
    except Exception:
        requested_shots = 1
    requested_shots = max(1, requested_shots)
    effective_shots, limit_note = _resolve_input_screenshot_plan(settings, requested_shots)

    if (html_input or "").strip() and effective_shots > 0:
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

    interpretation = await _interpret_input(settings, context, vision_service)

    async def _worker(model: str) -> Tuple[str, str, str, dict | None, TransitionArtifacts]:
        payload = build_code_payload(
            html_input=context.html_input,
            settings=settings,
            interpretation_summary=interpretation.summary,
            console_logs=context.input_console_logs,
            html_diff=context.html_diff,
            attachments=interpretation.attachments,
            message_history=message_history,
        )
        html_output, reasoning, meta = await ai_service.generate_html(payload, model, worker=model)
        out_screenshots, out_console_logs = await browser_service.render_and_capture(html_output, worker=model)
        out_screenshot_path = out_screenshots[0] if out_screenshots else ""
        artifacts = _create_artifacts(
            model=model,
            context=context,
            interpretation=interpretation,
            screenshot_path=out_screenshot_path,
            console_logs=out_console_logs,
            vision_output=interpretation.summary,
        )
        return model, html_output, (reasoning or ""), (meta or None), artifacts

    tasks = [_worker(m) for m in models]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)

    results: Dict[str, Tuple[str, str, dict | None, TransitionArtifacts]] = {}
    failed_models: List[Tuple[str, Exception]] = []

    for i, result in enumerate(gathered):
        model = models[i]
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
        model_names = [name for name, _ in failed_models]
        raise RuntimeError(f"All models failed: {', '.join(model_names)}")

    return results


def _resolve_input_screenshot_plan(settings: TransitionSettings, requested: int) -> tuple[int, str | None]:
    effective = max(1, requested)
    notes: List[str] = []

    try:
        mode = settings.mode if isinstance(settings.mode, IterationMode) else IterationMode(str(settings.mode))
    except Exception:
        mode = IterationMode.VISION_SUMMARY

    relevant_models: List[str] = []
    if mode == IterationMode.DIRECT_TO_CODER:
        code_models = [slug.strip() for slug in (settings.code_model or "").split(',') if slug.strip()]
        relevant_models.extend(code_models)

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
                metadata={"stage": "before", "index": str(idx)},
            )
        )

    # Run vision analysis for all modes when a screenshot is available so that
    # direct-to-coder can also leverage a textual summary alongside raw pixels.

    vision_prompt = build_vision_prompt(
        html_input=context.html_input,
        settings=settings,
        console_logs=context.input_console_logs,
        html_diff=context.html_diff,
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

    return TransitionArtifacts(
        screenshot_filename=screenshot_path,
        console_logs=list(console_logs),
        vision_output=vision_output,
        input_screenshot_filenames=list(context.input_screenshot_paths),
        input_console_logs=list(context.input_console_logs),
        assets=assets,
        analysis=analysis,
    )


async def _ensure_models_support_mode(models: List[str], mode: IterationMode) -> None:
    if mode != IterationMode.DIRECT_TO_CODER:
        return

    try:
        available = await orc.list_models(force_refresh=False, limit=2000)
    except Exception as exc:  # pragma: no cover - surfaces to UI
        raise RuntimeError(f"Failed to validate models for direct screenshot mode: {exc}")

    missing: List[str] = []
    lookup = {m.id: m for m in available}
    for slug in models:
        info = lookup.get(slug)
        if info is None or not getattr(info, "has_image_input", False):
            missing.append(slug)

    if missing:
        raise ValueError(
            "Direct screenshot mode requires code models with image input support: "
            + ", ".join(missing)
        )


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

    def add_listener(self, listener: IterationEventListener) -> None:
        self._listeners.append(listener)

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
        # Build chain from root to current node
        chain: List[IterationNode] = []
        cur = self.get_node(node_id)
        while cur is not None:
            chain.append(cur)
            cur = self.get_node(cur.parent_id) if cur.parent_id else None
        chain.reverse()

        messages: List[Dict[str, Any]] = []

        for node in chain:
            # Add user message from this node
            # Get the user messages from the first available model output
            first_output = next(iter(node.outputs.values())) if node.outputs else None
            if first_output and first_output.messages:
                # Extract user messages (assuming last message is the user message for this iteration)
                user_messages = [msg for msg in first_output.messages if msg.get("role") == "user"]
                if user_messages:
                    messages.extend(user_messages)

            # Add assistant response from the requested model (or fallback to first available)
            output = node.outputs.get(model_slug)
            if output is None and node.outputs:
                output = next(iter(node.outputs.values()))

            if output and output.assistant_response:
                messages.append({
                    "role": "assistant",
                    "content": output.assistant_response
                })

        return messages

    # Unified apply: if from_node_id is None, create a root; otherwise iterate from given node
    async def apply_transition(self, from_node_id: str | None, settings: TransitionSettings, from_model_slug: str | None = None) -> str:
        # Compute parent id and html_input
        parent_id: str | None
        html_input: str
        models = [m.strip() for m in settings.code_model.split(',') if m.strip()]
        if not models:
            raise ValueError("No code model specified")

        if not isinstance(settings.mode, IterationMode):
            settings.mode = IterationMode(str(settings.mode))

        await _ensure_models_support_mode(models, settings.mode)

        base_model = models[0]
        if from_node_id is None:
            parent_id = None
            html_input = ""
            html_diff = ""
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
            html_diff = _compute_html_diff(from_node.html_input, prev.html_output)

        # Delete descendants only when iterating from an existing node
        if parent_id is not None:
            self._delete_descendants(parent_id)

        # Import settings here to avoid circular imports
        from .settings import get_settings
        settings_manager = get_settings()

        # Collect message history if keep_history is enabled (single source of truth)
        message_history = None
        if settings_manager.keep_history and parent_id is not None:
            message_history = self._collect_message_history(parent_id, base_model)

        # Run transition
        results = await δ(
            html_input=html_input,
            settings=settings,
            models=models,
            ai_service=self._ai_service,
            browser_service=self._browser_service,
            vision_service=self._vision_service,
            html_diff=html_diff,
            message_history=message_history,
        )

        # Create and store node
        outputs_dict: Dict[str, ModelOutput] = {}
        for m, triple in results.items():
            html, reasoning_text, meta, art = triple
            total_cost = None
            generation_time = None
            messages = None
            assistant_response = ""
            try:
                if isinstance(meta, dict):
                    tc = meta.get('total_cost')
                    gt = meta.get('generation_time')
                    total_cost = float(tc) if tc is not None else None
                    generation_time = float(gt) if gt is not None else None
                    messages = meta.get('messages')
                    assistant_response = str(meta.get('assistant_response', ''))
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
            )
        node = IterationNode(
            parent_id=parent_id,
            html_input=html_input,
            outputs=outputs_dict,
            settings=settings,
        )
        self._nodes[node.id] = node
        await self._notify_node_created(node)
        return node.id

    # Listener notifications
    async def _notify_node_created(self, node: IterationNode) -> None:
        for listener in self._listeners:
            await listener.on_node_created(node)
