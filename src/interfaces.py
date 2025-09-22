# src/interfaces.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import uuid
from typing import Any, Dict, List, Optional, Protocol


# ---- Data model for state-machine iterations ----


class IterationMode(str, Enum):
    VISION_SUMMARY = "vision_summary"
    DIRECT_TO_CODER = "direct_to_coder"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


@dataclass
class TransitionSettings:
    code_model: str
    vision_model: str
    overall_goal: str
    user_steering: str
    code_template: str
    vision_template: str
    mode: IterationMode = IterationMode.VISION_SUMMARY


@dataclass
class TransitionArtifacts:
    screenshot_filename: str
    console_logs: List[str]
    vision_output: str
    input_screenshot_filename: str
    input_console_logs: List[str]
    assets: List["IterationAsset"] = field(default_factory=list)
    analysis: Dict[str, str] = field(default_factory=dict)


@dataclass
class IterationAsset:
    kind: str
    path: str
    role: str = ""
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class ModelOutput:
    html_output: str
    artifacts: TransitionArtifacts
    reasoning_text: str = ""
    total_cost: float | None = None
    generation_time: float | None = None
    messages: Optional[List[Dict[str, Any]]] = None


@dataclass
class IterationNode:
    parent_id: Optional[str]
    html_input: str
    outputs: Dict[str, ModelOutput]
    settings: TransitionSettings
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


# ---- Service protocols (unchanged public surface) ----

class AICodeService(Protocol):
    async def generate_html(self, prompt: Any, model: str, worker: str = "main") -> tuple[str, str | None, dict | None]: ...


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
