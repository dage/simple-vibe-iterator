# src/controller.py
from __future__ import annotations

import asyncio
import uuid
from typing import Dict, List, Optional

from .interfaces import (
    AICodeService,
    BrowserService,
    IterationEventListener,
    IterationNode,
    TransitionArtifacts,
    TransitionSettings,
    VisionService,
)


# Pure transition function δ(html_input, settings) -> (html_output, artifacts)
async def δ(
    html_input: str,
    settings: TransitionSettings,
    ai_service: AICodeService,
    browser_service: BrowserService,
    vision_service: VisionService,
) -> tuple[str, TransitionArtifacts]:
    # 1) Render html_input to capture screenshot and console logs for analysis
    in_screenshot_path, in_console_logs = await browser_service.render_and_capture(html_input)

    # 2) Build vision prompt from template for the current state and analyze once
    vision_prompt = settings.vision_template.format(
        html_input=html_input,
        console_logs="\n".join(in_console_logs or []),
        overall_goal=settings.overall_goal,
        user_instructions=settings.vision_instructions,
        vision_instructions=settings.vision_instructions,
    )
    in_vision_output = await vision_service.analyze_screenshot(
        vision_prompt,
        in_screenshot_path,
        in_console_logs,
    )

    # 3) Build code-model prompt from template + analysis of current state
    code_prompt = settings.code_template.format(
        html_input=html_input,
        code_instructions=settings.code_instructions,
        overall_goal=settings.overall_goal,
        vision_output=in_vision_output,
    )

    # 4) Call code model to produce html_output
    html_output = await ai_service.generate_html(code_prompt)

    # 5) Render the NEW html_output to produce the artifacts for this transition (no vision call here)
    out_screenshot_path, out_console_logs = await browser_service.render_and_capture(html_output)

    artifacts = TransitionArtifacts(
        screenshot_filename=out_screenshot_path,
        console_logs=out_console_logs,
        vision_output=in_vision_output,
    )
    return html_output, artifacts


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

    # Root creation: no initial HTML. Generate initial HTML from the overall goal, then capture artifacts.
    async def create_root(self, settings: TransitionSettings) -> str:
        # Default model slugs from environment if missing
        import os
        if not (settings.code_model or "").strip():
            env_code = os.getenv("VIBES_CODE_MODEL")
            if env_code:
                settings.code_model = env_code
        if not (settings.vision_model or "").strip():
            env_vision = os.getenv("VIBES_VISION_MODEL")
            if env_vision:
                settings.vision_model = env_vision
        # Build initial code prompt without html_input/vision_output
        code_prompt = settings.code_template.format(
            html_input="",
            code_instructions=settings.code_instructions,
            overall_goal=settings.overall_goal,
            vision_output="",
        )
        html_output = await self._ai_service.generate_html(code_prompt)
        screenshot_path, console_logs = await self._browser_service.render_and_capture(html_output)
        # For the root, there is no html_input; vision should analyze the initial output using the template
        vision_prompt = settings.vision_template.format(
            html_input="",
            console_logs="\n".join(console_logs or []),
            overall_goal=settings.overall_goal,
            user_instructions=settings.vision_instructions,
            vision_instructions=settings.vision_instructions,
        )
        vision_output = await self._vision_service.analyze_screenshot(vision_prompt, screenshot_path, console_logs)

        node_id = str(uuid.uuid4())
        node = IterationNode(
            id=node_id,
            parent_id=None,
            html_input="",
            html_output=html_output,
            settings=settings,
            artifacts=TransitionArtifacts(
                screenshot_filename=screenshot_path,
                console_logs=console_logs,
                vision_output=vision_output,
            ),
        )
        self._nodes[node_id] = node
        await self._notify_node_created(node)
        return node_id

    # Apply state transition from an existing node id
    async def apply_transition(self, from_node_id: str, settings: TransitionSettings) -> str:
        if from_node_id not in self._nodes:
            raise ValueError(f"Node {from_node_id} not found")

        # 1) Delete all descendants of from_node_id
        self._delete_descendants(from_node_id)

        # 2) Fetch html_input from the target node
        from_node = self._nodes[from_node_id]
        html_input = from_node.html_output or from_node.html_input

        # 3) Call δ to produce new output and artifacts
        html_output, artifacts = await δ(
            html_input=html_input,
            settings=settings,
            ai_service=self._ai_service,
            browser_service=self._browser_service,
            vision_service=self._vision_service,
        )

        # 4) Persist a new IterationNode as child and notify
        node_id = str(uuid.uuid4())
        new_node = IterationNode(
            id=node_id,
            parent_id=from_node_id,
            html_input=html_input,
            html_output=html_output,
            settings=settings,
            artifacts=artifacts,
        )
        self._nodes[node_id] = new_node
        await self._notify_node_created(new_node)
        return node_id

    # Listener notifications
    async def _notify_node_created(self, node: IterationNode) -> None:
        for listener in self._listeners:
            await listener.on_node_created(node)

