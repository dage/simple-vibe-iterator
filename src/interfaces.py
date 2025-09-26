# src/interfaces.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import uuid
from typing import Any, Dict, List, Optional, Protocol, Sequence


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
    input_screenshot_count: int = 1
    mode: IterationMode = IterationMode.VISION_SUMMARY
    keep_history: bool = False


@dataclass
class TransitionArtifacts:
    screenshot_filename: str
    console_logs: List[str]
    vision_output: str
    input_screenshot_filenames: List[str]
    input_console_logs: List[str]
    assets: List["IterationAsset"] = field(default_factory=list)
    analysis: Dict[str, str] = field(default_factory=dict)

    @property
    def input_screenshot_filename(self) -> str:
        return self.input_screenshot_filenames[0] if self.input_screenshot_filenames else ""


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
    assistant_response: str = ""


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
    async def render_and_capture(
        self,
        html_code: str,
        worker: str = "main",
        *,
        capture_count: int = 1,
        interval_seconds: float = 1.0,
    ) -> tuple[List[str], List[str]]: ...


class VisionService(Protocol):
    async def analyze_screenshot(
        self,
        prompt: str,
        screenshot_paths: Sequence[str],
        console_logs: List[str],
        model: str,
        worker: str = "main",
    ) -> str: ...


# ---- Iteration event listener for UI ----

class IterationEventListener(Protocol):
    async def on_node_created(self, node: IterationNode) -> None: ...
