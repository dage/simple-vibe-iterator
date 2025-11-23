from __future__ import annotations

import html as _html
import json
from typing import Any, Callable, Dict, List, Optional

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


def _normalize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize messages to decompose parallel tool calls into sequential Assistant -> Tool pairs.
    This allows unified rendering logic.
    """
    normalized: List[Dict[str, Any]] = []
    
    # Map tool_call_id to tool response messages for quick lookup
    tool_responses_map: Dict[str, Dict[str, Any]] = {}
    for msg in messages:
        if msg.get("role") == "tool" and msg.get("tool_call_id"):
            tool_responses_map[msg["tool_call_id"]] = msg

    consumed_tool_ids = set()

    for msg in messages:
        role = msg.get("role")
        
        # If it's a tool response we've already handled (moved), skip it
        if role == "tool" and msg.get("tool_call_id") in consumed_tool_ids:
            continue

        # If it's an assistant message with multiple tool calls, split it
        if role == "assistant" and msg.get("tool_calls"):
            tool_calls = msg.get("tool_calls", [])
            content = msg.get("content")
            
            # If there's content, add it as a separate message first (or keep if 0 tool calls)
            if content:
                normalized.append({"role": "assistant", "content": content})
            
            if not tool_calls:
                # Assistant message with content only (already added)
                pass
            else:
                # Add each tool call as a separate assistant message, 
                # followed immediately by its tool response (if found)
                for tc in tool_calls:
                    # Create single-tool-call assistant message
                    ass_msg = {
                        "role": "assistant",
                        "tool_calls": [tc],
                        "content": None # Content handled above
                    }
                    normalized.append(ass_msg)
                    
                    # Find matching response
                    tc_id = tc.get("id")
                    if tc_id and tc_id in tool_responses_map:
                        tool_msg = tool_responses_map[tc_id]
                        normalized.append(tool_msg)
                        consumed_tool_ids.add(tc_id)
        else:
            # Pass through other messages
            normalized.append(msg)
            
    return normalized


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
            .msg-expansion .msg-chip { border-radius: 9999px; padding: 2px 8px; font-size: 12px; font-weight: 600; display: inline-block; }
            .msg-expansion.chip-system .msg-chip { background: #1f2937; color: #93c5fd; }
            .msg-expansion.chip-user .msg-chip { background: #0f766e; color: #a7f3d0; }
            .msg-expansion.chip-assistant .msg-chip { background: #4c1d95; color: #c4b5fd; }
            .msg-expansion.chip-tool .msg-chip { background: #374151; color: #f59e0b; }
            </style>''')
            
            # Normalize messages (decompose parallel calls)
            msgs = _normalize_messages(messages)
            
            with ui.column().classes('w-full').style('gap: 10px;'):
                with ui.row().classes('items-center justify-between w-full'):
                    ui.label('Showing stored message objects').classes('text-sm text-gray-400')
                    ui.button(icon='close', on_click=dialog.close).props('flat round dense')

                raw_toggle = ui.checkbox('Raw JSON (all messages)', value=False).props('dense')
                raw_container = ui.column().classes('w-full').style('gap: 10px; display: none;')
                structured_container = ui.column().classes('w-full').style('gap: 10px;')

                with raw_container:
                    try:
                        # Show original messages in raw view, or normalized? 
                        # Usually user wants to see state as passed to model, so original 'messages' is better for raw dump.
                        messages_json = json.dumps(messages, indent=2, ensure_ascii=False)
                    except Exception:
                        messages_json = str(messages)
                    escaped_json = _html.escape(messages_json)
                    ui.html(f"<div class='messages-container'><pre class='messages-content'>{escaped_json}</pre></div>")

                with structured_container:
                    i = 0
                    while i < len(msgs):
                        msg = msgs[i]
                        role = str(msg.get('role', '') or '')
                        
                        # Check for Tool Interaction (Assistant Call + Tool Response)
                        # Condition: Assistant has tool_calls, and next message is Tool Response
                        is_tool_call_msg = role == 'assistant' and bool(msg.get('tool_calls'))
                        next_msg = msgs[i+1] if i + 1 < len(msgs) else None
                        is_next_tool_response = next_msg is not None and next_msg.get('role') == 'tool'
                        
                        # Match IDs if possible to be sure (though normalization ensures order)
                        ids_match = False
                        tool_call_data = None
                        if is_tool_call_msg and is_next_tool_response:
                            tool_calls = msg.get('tool_calls', [])
                            if tool_calls:
                                tool_call_data = tool_calls[0] # Normalized messages have 1 call per message
                                tc_id = tool_call_data.get('id')
                                resp_id = next_msg.get('tool_call_id')
                                if tc_id == resp_id:
                                    ids_match = True

                        if is_tool_call_msg and is_next_tool_response and ids_match:
                            # Render Combined Tool Node
                            func_name = tool_call_data.get('function', {}).get('name', 'Unknown Tool')
                            tool_args = tool_call_data.get('function', {}).get('arguments', '')
                            tool_response = next_msg.get('content', '')
                            
                            # Try to prettify args if JSON
                            try:
                                if isinstance(tool_args, str):
                                    tool_args = json.dumps(json.loads(tool_args), indent=2)
                                else:
                                    tool_args = json.dumps(tool_args, indent=2)
                            except:
                                pass # Keep as is
                                
                            exp = ui.expansion('').classes('msg-expansion chip-tool')
                            with exp.add_slot('header'):
                                with ui.row().classes('items-center gap-2 w-full'):
                                    # Tool Name with Icon inside the text box (chip)
                                    ui.html(f"<span class='msg-chip chip-tool'><i class='material-icons' style='font-size: 14px; vertical-align: text-bottom; margin-right: 4px;'>build</i>{_html.escape(func_name)}</span>")
                            
                            with exp:
                                # Call Section
                                ui.label('Call').classes('text-xs font-bold text-gray-400 mt-2')
                                ui.html(f"<pre class='msg-pre'>{_html.escape(str(tool_args))}</pre>")
                                
                                # Response Section
                                ui.label('Response').classes('text-xs font-bold text-gray-400 mt-2')
                                ui.html(f"<pre class='msg-pre'>{_html.escape(str(tool_response))}</pre>")
                            
                            i += 2 # Skip both messages
                            continue

                        # Standard Message Render
                        content = msg.get('content')
                        if content is None: 
                            content = "" # Handle None content
                            
                        # Determine role class
                        role_class = 'chip-user' if role == 'user' else ('chip-assistant' if role == 'assistant' else ('chip-system' if role == 'system' else 'chip-tool'))
                        
                        # Determine Display Name
                        display_role = role
                        if role == 'system':
                            display_role = 'System'
                        elif role == 'user':
                            display_role = 'User'
                        elif role == 'assistant':
                            display_role = 'Coder'
                        
                        exp = ui.expansion('').classes('msg-expansion ' + role_class)
                        with exp.add_slot('header'):
                            with ui.row().classes('items-center justify-between w-full'):
                                ui.html(f"<span class='msg-chip {role_class}'>{_html.escape(display_role)}</span>")
                                # Size label for content
                                try:
                                    if isinstance(content, str):
                                        kb = len(content.encode('utf-8')) / 1024.0
                                        size_label = f"{kb:.2f} KB"
                                        ui.label(size_label).classes('text-xs text-gray-400')
                                except:
                                    pass
                        
                        with exp:
                            # Just show content
                            if isinstance(content, str):
                                ui.html(f"<pre class='msg-pre'>{_html.escape(content)}</pre>")
                            else:
                                ui.html(f"<pre class='msg-pre'>{_html.escape(str(content))}</pre>")
                        
                        i += 1

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
