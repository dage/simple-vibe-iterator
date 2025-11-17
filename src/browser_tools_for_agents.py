"""Browser tool definitions for code-generating agents."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class BrowserToolProvider:
    """Supplies Chrome DevTools tool specs for agents."""

    def get_all_tools(self) -> List[Dict[str, Any]]:
        return [
            self.get_take_screenshot_tool(),
            self.get_list_console_messages_tool(),
            self.get_list_network_requests_tool(),
            self.get_press_key_tool(),
            self.get_evaluate_script_tool(),
            self.get_wait_for_tool(),
            self.get_performance_start_trace_tool(),
            self.get_performance_stop_trace_tool(),
        ]

    @staticmethod
    def get_take_screenshot_tool() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "take_screenshot",
                "description": "Capture the current browser viewport as a PNG (base64 data URL).",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }

    @staticmethod
    def get_list_console_messages_tool() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "list_console_messages",
                "description": "Retrieve console logs for debugging JavaScript errors.",
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
                "description": "Simulate keyboard input (e.g., WASD or space).",
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
                "description": "Execute JavaScript inside the browser context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "script": {"type": "string", "description": "JavaScript snippet"}
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
