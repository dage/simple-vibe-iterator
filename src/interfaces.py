# src/interfaces.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol


# ---- Data model for state-machine iterations ----

@dataclass
class TransitionSettings:
    code_model: str
    code_instructions: str
    vision_model: str
    vision_instructions: str
    overall_goal: str
    code_template: str
    vision_template: str


@dataclass
class TransitionArtifacts:
    screenshot_filename: str
    console_logs: List[str]
    vision_output: str


@dataclass
class IterationNode:
    id: str
    parent_id: Optional[str]
    html_input: str
    html_output: str
    settings: TransitionSettings
    artifacts: TransitionArtifacts


# ---- Service protocols (unchanged public surface) ----

class AICodeService(Protocol):
    async def generate_html(self, prompt: str) -> str: ...


class BrowserService(Protocol):
    async def render_and_capture(self, html_code: str) -> tuple[str, List[str]]: ...


class VisionService(Protocol):
    async def analyze_screenshot(self, screenshot_path: str, console_logs: List[str]) -> str: ...


# ---- Iteration event listener for UI ----

class IterationEventListener(Protocol):
    async def on_node_created(self, node: IterationNode) -> None: ...
