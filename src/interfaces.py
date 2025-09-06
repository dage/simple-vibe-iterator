# src/interfaces.py
from __future__ import annotations

from dataclasses import dataclass, field
import uuid
from typing import Dict, List, Optional, Protocol


# ---- Data model for state-machine iterations ----

@dataclass
class TransitionSettings:
    code_model: str
    vision_model: str
    overall_goal: str
    user_steering: str
    code_template: str
    vision_template: str


@dataclass
class TransitionArtifacts:
    screenshot_filename: str
    console_logs: List[str]
    vision_output: str
    input_screenshot_filename: str
    input_console_logs: List[str]


@dataclass
class ModelOutput:
    html_output: str
    artifacts: TransitionArtifacts
    reasoning_text: str = ""


@dataclass
class IterationNode:
    parent_id: Optional[str]
    html_input: str
    outputs: Dict[str, ModelOutput]
    settings: TransitionSettings
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


# ---- Service protocols (unchanged public surface) ----

class AICodeService(Protocol):
    async def generate_html(self, prompt: str, model: str, worker: str = "main") -> tuple[str, str | None]: ...


class BrowserService(Protocol):
    async def render_and_capture(self, html_code: str, worker: str = "main") -> tuple[str, List[str]]: ...


class VisionService(Protocol):
    async def analyze_screenshot(
        self,
        prompt: str,
        screenshot_path: str,
        console_logs: List[str],
        model: str,
        worker: str = "main",
    ) -> str: ...


# ---- Iteration event listener for UI ----

class IterationEventListener(Protocol):
    async def on_node_created(self, node: IterationNode) -> None: ...
