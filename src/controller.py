# src/controller.py
from __future__ import annotations

import asyncio
import difflib
import uuid
from typing import Dict, List, Optional, Tuple

from .interfaces import (
    AICodeService,
    BrowserService,
    IterationEventListener,
    IterationNode,
    TransitionArtifacts,
    TransitionSettings,
    VisionService,
    ModelOutput,
)


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


# Pure transition function δ(html_input, settings) -> mapping of model -> (html_output, artifacts)
def _build_template_context(
    html_input: str,
    settings: TransitionSettings,
    vision_output: str = "",
    console_logs: list[str] | None = None,
    html_diff: str = "",
) -> dict:
    # Prepare a unified mapping for both templates, exposing all settings fields
    # plus common dynamic fields.
    from dataclasses import asdict

    raw = asdict(settings).copy()
    # Do not expose template strings themselves to avoid nested, unsubstituted placeholders
    raw.pop("code_template", None)
    raw.pop("vision_template", None)
    ctx = raw
    ctx.update({
        "html_input": html_input or "",
        "vision_output": vision_output or "",
        "console_logs": "\n".join(console_logs or []),
        "html_diff": html_diff or "",
    })
    return ctx
async def δ(
    html_input: str,
    settings: TransitionSettings,
    models: List[str],
    ai_service: AICodeService,
    browser_service: BrowserService,
    vision_service: VisionService,
    html_diff: str = "",
) -> Dict[str, Tuple[str, TransitionArtifacts]]:
    # Prepare defaults
    in_console_logs: list[str] = []
    in_vision_output: str = ""
    in_screenshot_path: str = ""
    
    # Optional input render + vision when html_input is present
    if (html_input or "").strip():
        in_screenshot_path, in_console_logs = await browser_service.render_and_capture(html_input, worker="input")
        vision_ctx = _build_template_context(html_input=html_input, settings=settings, vision_output="", console_logs=in_console_logs, html_diff=html_diff)
        vision_prompt = settings.vision_template.format(**vision_ctx)
        in_vision_output = await vision_service.analyze_screenshot(
            vision_prompt,
            in_screenshot_path,
            in_console_logs,
            settings.vision_model,
        )

    # Build code-model prompt once
    code_ctx = _build_template_context(html_input=html_input, settings=settings, vision_output=in_vision_output, console_logs=in_console_logs, html_diff=html_diff)
    code_prompt = settings.code_template.format(**code_ctx)

    async def _run_model(model: str) -> tuple[str, Tuple[str, TransitionArtifacts]]:
        html_output = await ai_service.generate_html(code_prompt, model, worker=model)
        out_screenshot_path, out_console_logs = await browser_service.render_and_capture(html_output, worker=model)
        artifacts = TransitionArtifacts(
            screenshot_filename=out_screenshot_path,
            console_logs=out_console_logs,
            vision_output=in_vision_output,
            input_screenshot_filename=in_screenshot_path,
            input_console_logs=in_console_logs,
        )
        return model, (html_output, artifacts)

    pairs = await asyncio.gather(*[_run_model(m) for m in models])
    return {m: res for m, res in pairs}


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

    # Unified apply: if from_node_id is None, create a root; otherwise iterate from given node
    async def apply_transition(self, from_node_id: str | None, settings: TransitionSettings) -> str:
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
            html_diff = ""
        elif from_node_id not in self._nodes:
            raise ValueError(f"Node {from_node_id} not found")
        else:
            parent_id = from_node_id
            from_node = self._nodes[from_node_id]
            prev = from_node.outputs.get(base_model)
            if prev is None:
                prev = next(iter(from_node.outputs.values()))
            html_input = prev.html_output or from_node.html_input
            html_diff = _compute_html_diff(from_node.html_input, prev.html_output)

        # Delete descendants only when iterating from an existing node
        if parent_id is not None:
            self._delete_descendants(parent_id)

        # Run transition
        results = await δ(
            html_input=html_input,
            settings=settings,
            models=models,
            ai_service=self._ai_service,
            browser_service=self._browser_service,
            vision_service=self._vision_service,
            html_diff=html_diff,
        )

        # Create and store node
        outputs_dict: Dict[str, ModelOutput] = {
            m: ModelOutput(html_output=html, artifacts=art)
            for m, (html, art) in results.items()
        }
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

