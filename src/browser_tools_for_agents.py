"""Browser tool definitions for code-generating agents."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class BrowserToolProvider:
    """Supplies Chrome DevTools tool specs for agents."""

    def get_all_tools(self) -> List[Dict[str, Any]]:
        return [
            self.get_load_html_tool(),
            self.get_analyze_screen_tool(),
            self.get_list_console_messages_tool(),
            self.get_list_network_requests_tool(),
            self.get_press_key_tool(),
            self.get_evaluate_script_tool(),
            self.get_wait_for_tool(),
            self.get_performance_start_trace_tool(),
            self.get_performance_stop_trace_tool(),
        ]

    @staticmethod
    def get_analyze_screen_tool() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "analyze_screen",
                "description": (
                    "Capture the current viewport and run a lightweight vision analysis. "
                    "Use this to visually confirm updates, detect rendering issues, or verify "
                    "element positions. It will generate a bullet list of observations in context of the overall goal and user feedback."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Optional question about the current screen. Overrides the "
                                "default prompt for this call."
                            ),
                        }
                    },
                    "required": [],
                },
            },
        }

    @staticmethod
    def get_load_html_tool() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "load_html",
                "description": (
                    "Render a complete HTML document directly in the browser so "
                    "subsequent tooling calls can inspect it."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "html_content": {
                            "type": "string",
                            "description": (
                                "Full HTML document string including <!DOCTYPE html>, "
                                "<html>, and <body>."
                            ),
                        }
                    },
                    "required": ["html_content"],
                },
            },
        }

    @staticmethod
    def get_list_console_messages_tool() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "list_console_messages",
                "description": (
                    "Retrieve console logs (log, info, warn, error) to debug JavaScript behavior. "
                    "Call this after interactions or state changes to inspect new messages."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "level": {
                            "type": "string",
                            "enum": ["error", "warn", "log", "info"],
                            "description": "Optional log level filter",
                        }
                    },
                    "required": [],
                },
            },
        }

    @staticmethod
    def get_list_network_requests_tool() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "list_network_requests",
                "description": "Inspect network traffic for failed resource loads.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filter": {
                            "type": "string",
                            "description": "Optional substring to filter URLs",
                        }
                    },
                    "required": [],
                },
            },
        }

    @staticmethod
    def get_press_key_tool() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "press_key",
                "description": (
                    "Simulate keyboard input (any key, including arrows, WASD, space, etc.) "
                    "to drive in-page interactions and gameplay."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Key identifier"},
                        "duration_ms": {
                            "type": "integer",
                            "description": "Hold duration in milliseconds",
                            "default": 100,
                        },
                    },
                    "required": ["key"],
                },
            },
        }

    @staticmethod
    def get_evaluate_script_tool() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "evaluate_script",
                "description": (
                    "Evaluate a single JavaScript expression in the page context for quick "
                    "state inspection or simple tweaks. Supports property reads and assignments "
                    "like `window.score`, `window.score = 5`, or `document.title`. Do NOT rely "
                    "on it to define or call functions, run IIFEs, or use comma expressions "
                    "that involve functions—those patterns are not reliably supported and may "
                    "throw errors. Avoid semicolon-terminated statements. For multi-step logic "
                    "or event listeners, put the JavaScript directly in your HTML via load_html "
                    "and use evaluate_script only to read or adjust existing state."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "script": {"type": "string", "description": "JavaScript expression"}
                    },
                    "required": ["script"],
                },
            },
        }

    @staticmethod
    def get_wait_for_tool() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "wait_for",
                "description": "Wait for an element to appear (headless reliability).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS selector"},
                        "timeout_ms": {
                            "type": "integer",
                            "description": "Timeout in milliseconds",
                            "default": 5000,
                        },
                    },
                    "required": ["selector"],
                },
            },
        }

    @staticmethod
    def get_performance_start_trace_tool() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "performance_start_trace",
                "description": "Begin recording a performance trace.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }

    @staticmethod
    def get_performance_stop_trace_tool() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "performance_stop_trace",
                "description": "Stop the trace and retrieve metrics.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
