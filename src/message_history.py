from __future__ import annotations

import html as _html
import json
from typing import Any, Callable, Dict, List

from nicegui import ui


def _flatten_tool_calls(raw_calls: Any) -> List[Dict[str, Any]]:
    """Flatten tool calls, including nested/parallel tool calls."""
    flattened: List[Dict[str, Any]] = []
    if not isinstance(raw_calls, list):
        return flattened
    for call in raw_calls:
        if not isinstance(call, dict):
            continue
        # Traverse nested calls if present
        nested = call.get("tool_calls")
        if isinstance(nested, list):
            flattened.extend(_flatten_tool_calls(nested))
        if call.get("type") == "function" or call.get("function") or call.get("name"):
            flattened.append(call)
    return flattened


def render_message_history_dialog(
    dialog: ui.dialog,
    messages: List[Dict[str, Any]],
    header_controls: Callable[[ui.row], None] | None = None,
) -> None:
    """Render the Message History dialog using the stored message objects (single source of truth)."""
    dialog.clear()
    with dialog:
        with ui.card().classes('w-[90vw] max-w-[1200px]'):
            with ui.row().classes('items-center justify-between w-full'):
                ui.label('Message History').classes('text-lg font-semibold')
                controls_row = ui.row().classes('items-center gap-2')
                if header_controls:
                    header_controls(controls_row)
                ui.button(icon='close', on_click=dialog.close).props('flat round dense')
            ui.html('''<style>
            .messages-container { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; background: #0b0f17; color: #e5e7eb; border: 1px solid #334155; border-radius: 6px; padding: 16px; max-height: 70vh; overflow: auto; }
            .msg-pre { white-space: pre-wrap; word-break: break-word; background: #0b0f17; color: #e5e7eb; border: 1px solid #334155; border-radius: 6px; padding: 10px; }
            .msg-expansion .q-item__label { border-radius: 9999px; padding: 2px 8px; font-size: 12px; font-weight: 600; display: inline-block; }
            .msg-expansion.chip-system .q-item__label { background: #1f2937; color: #93c5fd; }
            .msg-expansion.chip-user .q-item__label { background: #0f766e; color: #a7f3d0; }
            .msg-expansion.chip-assistant .q-item__label { background: #4c1d95; color: #c4b5fd; }
            .msg-expansion.chip-tool .q-item__label { background: #374151; color: #f59e0b; }
            </style>''')
            msgs = list(messages)
            with ui.column().classes('w-full').style('gap: 10px;'):
                with ui.row().classes('items-center justify-between w-full'):
                    ui.label('Showing stored message objects').classes('text-sm text-gray-400')
                    ui.button(icon='close', on_click=dialog.close).props('flat round dense')

                raw_toggle = ui.checkbox('Raw JSON (all messages)', value=False).props('dense')
                raw_container = ui.column().classes('w-full').style('gap: 10px; display: none;')
                structured_container = ui.column().classes('w-full').style('gap: 10px;')

                with raw_container:
                    try:
                        messages_json = json.dumps(msgs, indent=2, ensure_ascii=False)
                    except Exception:
                        messages_json = str(msgs)
                    escaped_json = _html.escape(messages_json)
                    ui.html(f"<div class='messages-container'><pre class='messages-content'>{escaped_json}</pre></div>")

                with structured_container:
                    for m in msgs:
                        message_dict = m if isinstance(m, dict) else {"role": "unknown", "content": m}
                        role = str(message_dict.get('role', '') or '')
                        has_tool_calls = bool(message_dict.get('tool_calls'))
                        flat_calls = _flatten_tool_calls(message_dict.get('tool_calls'))
                        try:
                            pretty = json.dumps(message_dict, ensure_ascii=False, indent=2)
                            kb = len(pretty.encode('utf-8')) / 1024.0
                            size_label = f"{kb:.2f} KB"
                        except Exception:
                            pretty = str(message_dict)
                            size_label = ''
                        exp = ui.expansion('').classes('msg-expansion ' + (
                            'chip-user' if role == 'user' else ('chip-assistant' if role == 'assistant' else ('chip-system' if role == 'system' else 'chip-tool'))
                        ))
                        with exp.add_slot('header'):
                            with ui.row().classes('items-center justify-between w-full'):
                                role_class = 'chip-user' if role == 'user' else ('chip-assistant' if role == 'assistant' else ('chip-system' if role == 'system' else 'chip-tool'))
                                chip_label = 'Tool response' if role == 'tool' else ('Assistant tool call' if role == 'assistant' and has_tool_calls else (role or 'unknown'))
                                ui.html(f"<span class='msg-chip {role_class}'>{_html.escape(chip_label)}</span>")
                                ui.label(size_label).classes('text-xs text-gray-400')
                        try:
                            exp.set_value(False)
                        except Exception:
                            try:
                                exp.value = False
                            except Exception:
                                pass
                        with exp:
                            if flat_calls:
                                ui.label(f"Tool calls ({len(flat_calls)})").classes('text-xs text-gray-400')
                                try:
                                    ui.html(f"<pre class='msg-pre'>{_html.escape(json.dumps(flat_calls, ensure_ascii=False, indent=2), quote=False)}</pre>")
                                except Exception:
                                    ui.html(f"<pre class='msg-pre'>{_html.escape(str(flat_calls), quote=False)}</pre>")
                            ui.label('Raw').classes('text-xs text-gray-400')
                            escaped_json = _html.escape(pretty, quote=False)
                            ui.html(f"<pre class='msg-pre'>{escaped_json}</pre>")

                def _toggle_raw() -> None:
                    is_raw = bool(getattr(raw_toggle, 'value', False))
                    try:
                        raw_container.style('display: block;' if is_raw else 'display: none;')
                    except Exception:
                        pass
                    try:
                        structured_container.style('display: none;' if is_raw else 'display: block;')
                    except Exception:
                        pass

                raw_toggle.on_value_change(lambda _: _toggle_raw())
                _toggle_raw()
