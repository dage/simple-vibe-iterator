# src/view.py
from __future__ import annotations

import asyncio
import json
import base64
from typing import Any, Dict, List, Callable
from types import SimpleNamespace
import time
import re
from pathlib import Path

from nicegui import ui
from diff_match_patch import diff_match_patch
import html as _html

from .controller import IterationController
from .interfaces import IterationEventListener, IterationNode, TransitionSettings, TemplateVariableSummary
from . import op_status
from . import task_registry
from . import feedback_presets
from .model_selector import ModelSelector
from .settings import get_settings
from .ui_theme import apply_theme
from .view_utils import extract_vision_summary, format_html_size
from .node_summary_dialog import create_node_summary_dialog
from .status_panel import StatusPanel
from . import or_client as orc
from .services import detect_mime_type
from .message_history import render_message_history_dialog

GOAL_SUMMARY_MODEL = "x-ai/grok-4-fast"
GOAL_SUMMARY_CHAR_LIMIT = 280
GOAL_SUMMARY_WORD_LIMIT = 55
GOAL_SUMMARY_LINE_LIMIT = 4
GOAL_SUMMARY_MAX_OUTPUT_WORDS = 16
TEMPLATE_VAR_MAX_FILE_SIZE = 10 * 1024 * 1024


class NiceGUIView(IterationEventListener):
    def __init__(self, controller: IterationController):
        self.controller = controller
        self.controller.add_listener(self)
        self.node_panels: Dict[str, ui.element] = {}
        self.chat_container: ui.element | None = None
        self.goal_panel: ui.element | None = None
        self.scroll_area: ui.scroll_area | None = None
        self.initial_goal_input: ui.textarea | None = None
        self._goal_status_label: ui.element | None = None
        self._original_goal_button: ui.element | None = None
        self._original_goal_text: str = ""
        # --- Operation status & lock ---
        self._op_busy: bool = False
        self._status_timer: ui.timer | None = None
        self._notif_timer: ui.timer | None = None
        self._status_panel: StatusPanel | None = None
        self._persistent_selectors: List[ModelSelector] = []
        self._ephemeral_selectors: List[ModelSelector] = []
        self._shutdown_called: bool = False
        self._status_refresh_interval: float = 1.0
        self._last_status_refresh: float = 0.0
        self._goal_heading_label: ui.element | None = None
        self._current_overall_goal: str = ""
        self._template_var_list: ui.column | None = None
        self._template_var_badge: ui.element | None = None
        self._template_var_items: List[TemplateVariableSummary] = []
        self._text_var_key_input: ui.input | None = None
        self._text_var_value_input: ui.textarea | None = None
        self._template_var_empty_label: ui.element | None = None
        self._prompt_examples: List[Dict[str, Any]] = []

        # Set some default styling
        apply_theme()

    def render(self) -> None:
        self._stop_timers()
        # Scoped CSS: Make the default CLOSE button text black on error notifications
        with ui.column().classes('w-full h-screen p-4 gap-3'):
            ui.label('Simple Vibe Iterator').classes('text-2xl font-bold')
            async def _open_original_goal(*_: Any) -> None:
                await self._show_original_goal()

            with ui.row().classes('w-full items-center gap-3'):
                self._goal_heading_label = ui.label('').classes('text-lg font-semibold text-primary-600')
                button = ui.button('View original goal', on_click=_open_original_goal).props('flat text-sm').classes('ml-auto text-primary-500 hover:text-primary-400').style('visibility:hidden')
                self._original_goal_button = button
            self._goal_status_label = ui.label('').classes('text-sm font-medium text-amber-200/90 mt-1')
            self._set_overall_goal_heading(self._current_overall_goal)
            self._set_goal_status('')

            # Container for worker status boxes
            self._status_panel = StatusPanel(on_cancel=self._cancel_worker)
            self._status_panel.build()
            self._status_timer = ui.timer(0.25, lambda: self._refresh_phase())
            # Drain background notifications in UI context
            self._notif_timer = ui.timer(0.25, self._flush_notifications)
            self._refresh_phase(force=True)

            self.goal_panel = self._create_goal_panel()

            with ui.scroll_area().classes('flex-grow w-full') as scroll:
                self.scroll_area = scroll
                with ui.column().classes('w-full gap-4'):
                    # Iteration chain container
                    self.chat_container = ui.column().classes('w-full gap-4')

    def _create_goal_panel(self) -> ui.element:
        with ui.card().classes('w-full bg-gradient-to-br from-slate-900 to-slate-800 text-white shadow-md p-4') as panel:
            with ui.column().classes('w-full gap-3'):
                ui.label('What should we build?').classes('text-xl font-semibold')
                ui.label('Describe the overall vibe or experience you want.').classes('text-sm text-slate-200')

                self.initial_goal_input = ui.textarea(
                    value=self._current_overall_goal,
                ).props('filled type=textarea autogrow input-style="min-height: 200px; max-height: 400px;"') \
                 .classes('w-full text-base placeholder-transparent').style('white-space: pre-wrap;')

                # Add example prompt selector
                prompt_dir = Path('prompt-examples')
                self._prompt_examples = self._load_prompt_examples(prompt_dir)
                prompt_labels = [entry['label'] for entry in self._prompt_examples]

                async def _apply_prompt_example(label: str) -> None:
                    entry = next((p for p in self._prompt_examples if p['label'] == label), None)
                    if entry is None:
                        return
                    try:
                        await _set_goal_text(entry.get('goal', ''))
                        self.controller.clear_template_variables()
                        text_vars: Dict[str, str] = entry.get('text_vars', {})
                        for key, value in text_vars.items():
                            self.controller.set_template_text_variable(key, value)
                        file_vars: List[Dict[str, Any]] = entry.get('file_vars', [])
                        for f in file_vars:
                            raw_b64 = str(f.get('data_base64', '') or '')
                            if not raw_b64:
                                continue
                            try:
                                file_bytes = base64.b64decode(raw_b64)
                            except Exception:
                                ui.notify(f"Could not decode file for {f.get('key','')}", color='warning', timeout=4000)
                                continue
                            key = str(f.get('key') or '').strip() or str(f.get('filename') or 'ASSET')
                            mime_type = str(f.get('mime_type') or detect_mime_type(str(f.get('filename') or 'asset.bin')))
                            filename = str(f.get('filename') or '')
                            self.controller.set_template_file_variable(
                                key,
                                file_bytes,
                                mime_type=mime_type,
                                filename=filename,
                            )
                        self._refresh_template_vars_ui()
                        ui.notify(f"Loaded preset '{label}' with {len(text_vars) + len(file_vars)} template variables.", color='positive', timeout=2500)
                    except Exception as exc:
                        ui.notify(f"Failed to load preset '{label}': {exc}", color='negative', timeout=0, close_button=True)

                async def _on_prompt_select(event):
                    if event.value:
                        await _apply_prompt_example(str(event.value))

                if prompt_labels:
                    ui.select(
                        options=prompt_labels,
                        label='Presets',
                        on_change=_on_prompt_select
                    ).props('outlined dense clearable').classes('w-full')

                async def _set_goal_text(value: str) -> None:
                    target = self.initial_goal_input
                    if target is None:
                        return
                    setter = getattr(target, 'set_value', None)
                    if asyncio.iscoroutinefunction(setter):
                        await setter(value)  # type: ignore[arg-type]
                        return
                    if setter is not None:
                        setter(value)  # type: ignore[misc]
                        return
                    setattr(target, 'value', value)
                    try:
                        target.update()
                    except Exception:
                        pass

                async def _submit_goal(*_, goal_override: str | None = None) -> None:
                    og_source = goal_override if goal_override is not None else getattr(self.initial_goal_input, 'value', '')
                    og = (og_source or '').strip()
                    await _set_goal_text(og)
                    if not og:
                        ui.notify('Please enter an overall goal', color='negative', timeout=0, close_button=True)
                        return
                    self._original_goal_text = ''
                    self._set_original_goal_button_visible(False)
                    display_goal = og
                    if self._should_summarize_goal(og):
                        self._set_goal_status(f"Please wait while {GOAL_SUMMARY_MODEL} is summarizing the goal...")
                        self._original_goal_text = og
                        self._set_original_goal_button_visible(True)
                        await asyncio.sleep(0)  # yield so status text renders before summarization starts
                        try:
                            summary = await self._summarize_goal_text(og)
                            if summary:
                                display_goal = summary
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            print(f"[view] goal summarization failed: {exc}")
                        finally:
                            self._set_goal_status('')
                    if not self._begin_operation('Start'):
                        return
                    try:
                        settings_manager = get_settings()
                        settings = self._default_settings(overall_goal=og)
                        settings_manager.save_settings(settings)
                        await self.controller.start_new_tree(settings)
                        self._set_overall_goal_heading(display_goal)
                        self._initial_goal_complete()
                    except asyncio.CancelledError:
                        ui.notify('Operation cancelled', color='warning', timeout=2000)
                    except Exception as exc:
                        ui.notify(f'Start failed: {exc}', color='negative', timeout=0, close_button=True)
                        print(f"[view] start_new_tree failed: {exc}")
                    finally:
                        self._end_operation()

                with ui.expansion('Template Variables', value=False).classes('w-full bg-slate-950/60 rounded-lg text-white border border-slate-800/80'):
                    with ui.column().classes('w-full gap-3 p-1'):
                        self._build_template_variable_section()

                ui.button('Start Building', on_click=_submit_goal).props('unelevated size=lg').classes('bg-amber-300 text-slate-900 font-semibold w-full hover:bg-amber-200 shadow-md rounded-lg')

        return panel

    def _build_template_variable_section(self) -> None:
        ui.label('Upload assets or store snippets that you can reference as {{KEY}} inside prompts and HTML. Files are injected as data URLs after the AI responds.').classes('text-sm text-slate-200')
        tabs = ui.tabs(value='files').classes('w-full').props('dense')
        with tabs:
            ui.tab('files', 'File Uploads')
            ui.tab('text', 'Text Snippets')
        with ui.tab_panels(tabs, value='files').classes('w-full bg-slate-950/60 rounded-lg p-3'):
            with ui.tab_panel('files'):
                ui.label('Drag and drop assets under 10 MB. Keys are generated from filenames.').classes('text-xs text-slate-300 mb-2')
                ui.upload(
                    label='Upload files',
                    multiple=True,
                    on_upload=self._handle_template_file_upload,
                ).classes('w-full border border-dashed border-slate-600 rounded-lg px-4 py-6 bg-slate-900')
            with ui.tab_panel('text'):
                self._text_var_key_input = ui.input(label='Variable key (e.g. API_DOCS)').props('outlined dense clearable').classes('w-full text-black dark:text-white')
                self._text_var_value_input = ui.textarea(label='Text content').props('filled autogrow').classes('w-full text-black dark:text-white')
                ui.button('Add text variable', on_click=self._handle_template_text_submit).props('unelevated color=primary')

        badge_row = ui.row().classes('w-full items-center mt-2')
        with badge_row:
            self._template_var_badge = ui.label('No variables').classes('text-xs text-amber-200')
        self._template_var_list = ui.column().classes('w-full gap-2')
        self._template_var_empty_label = ui.label('No template variables configured yet.').classes('text-sm text-slate-300')
        self._refresh_template_vars_ui()

    def _initial_goal_complete(self) -> None:
        if self.goal_panel is not None:
            try:
                self.goal_panel.clear()
            except Exception:
                pass
            self.goal_panel = None
        self.initial_goal_input = None

    async def _handle_template_file_upload(self, event) -> None:
        try:
            content = getattr(event, 'content', None)
            if content is None:
                raise ValueError('No file content provided')
            if hasattr(content, 'seek'):
                try:
                    content.seek(0)
                except Exception:
                    pass
            if hasattr(content, 'read'):
                raw = content.read()
            else:
                raw = content if isinstance(content, (bytes, bytearray)) else b''
            file_bytes = bytes(raw or b'')
            size_hint = getattr(event, 'size', None)
            size_bytes = int(size_hint) if isinstance(size_hint, (int, float)) else len(file_bytes)
            if not file_bytes:
                size_bytes = 0
            if size_bytes > TEMPLATE_VAR_MAX_FILE_SIZE:
                ui.notify('File exceeds 10 MB limit for template variables.', color='warning', timeout=5000)
                return
            filename = getattr(event, 'name', '') or 'asset.bin'
            raw_type = str(getattr(event, 'type', '') or '').strip()
            mime_type = raw_type or detect_mime_type(filename)
            key = self._suggest_file_key(filename)
            summary = self.controller.set_template_file_variable(key, file_bytes, mime_type=mime_type, filename=filename)
            ui.notify(f"Added {summary.key}", color='positive', timeout=2000)
            self._refresh_template_vars_ui()
        except Exception as exc:
            ui.notify(f"File upload failed: {exc}", color='negative', timeout=0, close_button=True)

    async def _handle_template_text_submit(self, *_: Any) -> None:
        key_input = self._text_var_key_input
        value_input = self._text_var_value_input
        if key_input is None or value_input is None:
            return
        raw_key = (getattr(key_input, 'value', '') or '').strip()
        raw_text = getattr(value_input, 'value', '') or ''
        if not raw_key:
            ui.notify('Please enter a key name for the text variable.', color='warning', timeout=3000)
            return
        if not raw_text.strip():
            ui.notify('Please enter the text content.', color='warning', timeout=3000)
            return
        try:
            summary = self.controller.set_template_text_variable(raw_key, raw_text)
            ui.notify(f"Saved {summary.key}", color='positive', timeout=2500)
            self._refresh_template_vars_ui()
            await self._set_control_value(key_input, '')
            await self._set_control_value(value_input, '')
        except Exception as exc:
            ui.notify(f"Text variable failed: {exc}", color='negative', timeout=0, close_button=True)

    async def _handle_template_var_remove(self, key: str) -> None:
        try:
            self.controller.remove_template_variable(key)
            ui.notify(f"Removed {key}", color='warning', timeout=2000)
            self._refresh_template_vars_ui()
        except Exception as exc:
            ui.notify(f"Remove failed: {exc}", color='negative', timeout=0, close_button=True)

    def _refresh_template_vars_ui(self) -> None:
        container = self._template_var_list
        if container is None:
            return
        summaries = self.controller.list_template_variables()
        self._template_var_items = summaries
        container.clear()
        for summary in summaries:
            self._render_template_variable_entry(summary)
        show_empty = not summaries
        empty_label = self._template_var_empty_label
        if empty_label is not None:
            try:
                empty_label.style('display:block' if show_empty else 'display:none')
            except Exception:
                pass
        self._update_template_var_badge(len(summaries))

    def _render_template_variable_entry(self, summary: TemplateVariableSummary) -> None:
        container = self._template_var_list
        if container is None:
            return
        with container:
            with ui.row().classes('w-full items-center gap-3 bg-slate-950/70 border border-slate-800 rounded-lg px-3 py-2 text-sm flex-wrap'):
                ui.label(summary.key).classes('font-semibold text-white min-w-[140px]')
                badge_text = 'File' if summary.kind == 'file' else 'Text'
                ui.chip(badge_text).props('outline dense size=sm').classes('text-xs text-slate-100')
                detail = summary.description or (summary.mime_type if summary.kind == 'file' else '')
                if summary.kind == 'file' and summary.notes:
                    detail = f"{detail} · {summary.notes}"
                if summary.kind == 'file' and summary.filename:
                    detail = f"{summary.filename} · {detail}"
                elif summary.kind == 'text':
                    detail = detail or '(empty)'
                ui.label(detail).classes('text-xs text-slate-200 flex-grow')

                async def _on_remove(_, key=summary.key):
                    await self._handle_template_var_remove(key)

                ui.button('Remove', on_click=_on_remove).props('size=sm flat color=negative').classes('text-xs text-red-200')

    def _update_template_var_badge(self, count: int) -> None:
        badge = self._template_var_badge
        if badge is None:
            return
        text = f"{count} configured" if count else 'No variables'
        try:
            badge.text = text
        except Exception:
            try:
                badge.set_text(text)
            except Exception:
                pass

    def _suggest_file_key(self, filename: str) -> str:
        base = Path(filename or '').stem or 'ASSET'
        cleaned = re.sub(r'[^A-Za-z0-9]+', '_', base).strip('_') or 'ASSET'
        candidate = cleaned.upper()
        if not candidate.endswith('_DATA_URL'):
            candidate = f"{candidate}_DATA_URL"
        try:
            normalized = self.controller.normalize_template_key(candidate)
        except Exception:
            normalized = 'ASSET_DATA_URL'
        existing = {entry.key for entry in self.controller.list_template_variables()}
        unique = normalized
        suffix = 2
        while unique in existing:
            try:
                unique = self.controller.normalize_template_key(f"{normalized}_{suffix}")
            except Exception:
                unique = f"{normalized}_{suffix}"
            suffix += 1
        return unique

    async def _set_control_value(self, control: ui.element | None, value: str) -> None:
        if control is None:
            return
        setter = getattr(control, 'set_value', None)
        if asyncio.iscoroutinefunction(setter):
            await setter(value)  # type: ignore[arg-type]
            return
        if callable(setter):
            setter(value)
        else:
            setattr(control, 'value', value)
        try:
            control.update()
        except Exception:
            pass

    def _default_settings(self, overall_goal: str) -> TransitionSettings:
        return get_settings().load_settings(overall_goal=overall_goal)

    def _set_overall_goal_heading(self, goal: str) -> None:
        self._current_overall_goal = goal.strip()
        label = self._goal_heading_label
        if label is None:
            return
        text = self._current_overall_goal
        try:
            label.text = text
        except Exception:
            try:
                label.set_text(text)  # type: ignore[attr-defined]
            except Exception:
                pass

    def _set_goal_status(self, text: str) -> None:
        label = self._goal_status_label
        if label is None:
            return
        try:
            label.text = text
        except Exception:
            try:
                label.set_text(text)  # type: ignore[attr-defined]
            except Exception:
                pass

    def _set_original_goal_button_visible(self, visible: bool) -> None:
        button = self._original_goal_button
        if button is None:
            return
        try:
            button.style('visibility:visible' if visible else 'visibility:hidden')
        except Exception:
            pass

    async def _show_original_goal(self) -> None:
        goal = (self._original_goal_text or '').strip()
        if not goal:
            ui.notify('No overall goal captured yet.', color='info', timeout=2000)
            return
        with ui.dialog() as dialog:
            with ui.card().classes('max-w-xl w-[min(95vw,600px)] p-4 gap-3'):
                ui.label('Original goal').classes('text-base font-semibold')
                ui.textarea(
                    value=goal,
                ).props('filled autogrow readonly').classes('w-full text-sm').style('white-space: pre-wrap;')
                ui.button('Close', on_click=dialog.close).props('outlined')
        dialog.open()

    def _should_summarize_goal(self, goal: str) -> bool:
        stripped = goal.strip()
        if not stripped:
            return False
        # Long or multi-line goals can overwhelm the heading, so summarize them.
        if len(stripped) > GOAL_SUMMARY_CHAR_LIMIT:
            return True
        if len(stripped.split()) > GOAL_SUMMARY_WORD_LIMIT:
            return True
        lines = [line for line in stripped.splitlines() if line.strip()]
        if len(lines) > GOAL_SUMMARY_LINE_LIMIT:
            return True
        if any(len(line) > (GOAL_SUMMARY_CHAR_LIMIT // 2) for line in lines):
            return True
        return False

    def _load_prompt_examples(self, prompt_dir: Path) -> List[Dict[str, Any]]:
        examples: List[Dict[str, Any]] = []
        if not prompt_dir.exists():
            return examples

        def _normalize_label(raw: str) -> str:
            return re.sub(r'\s+', ' ', raw).strip()

        # Prefer JSON presets with embedded template variables
        for path in sorted(prompt_dir.glob('*.json')):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                label = _normalize_label(str(data.get('name') or path.stem))
                goal = str(data.get('goal') or "").strip()
                user_feedback = str(data.get('user_feedback') or "")
                tmpl = data.get('template_variables') or {}
                text_vars = tmpl.get('text') or {}
                file_vars = tmpl.get('files') or []
                examples.append(
                    {
                        "label": label,
                        "goal": goal,
                        "user_feedback": user_feedback,
                        "text_vars": text_vars,
                        "file_vars": file_vars,
                    }
                )
            except Exception as exc:
                print(f"[prompt_examples] Failed to parse {path}: {exc}")

        # Fallback to legacy .txt prompts if no JSON found
        if not examples:
            for path in sorted(prompt_dir.glob('*.txt')):
                try:
                    content = path.read_text(encoding='utf-8').strip()
                    label = _normalize_label(path.stem)
                    examples.append({"label": label, "goal": content, "user_feedback": "", "text_vars": {}, "file_vars": []})
                except Exception as exc:
                    print(f"[prompt_examples] Failed to read {path}: {exc}")
        return examples

    async def _summarize_goal_text(self, goal: str) -> str:
        stripped = goal.strip()
        if not stripped:
            return stripped
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a concise summarizer. Return a single sentence that captures the "
                    "user's main goal experience. Keep it very brief—at most "
                    f"{GOAL_SUMMARY_MAX_OUTPUT_WORDS} words—and aim to make it roughly 50% shorter "
                    "than the source text while staying descriptive."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Summarize the following overall goal quickly:\n"
                    f"{stripped}"
                ),
            },
        ]
        summary = await orc.chat(
            messages,
            model=GOAL_SUMMARY_MODEL,
            temperature=0.3,
            max_tokens=120,
        )
        normalized = self._normalize_summary_text(summary)
        if not normalized:
            return stripped
        return self._enforce_summary_word_limit(normalized) or stripped

    def _enforce_summary_word_limit(self, summary: str) -> str:
        words = summary.split()
        if not words:
            return ''
        if len(words) <= GOAL_SUMMARY_MAX_OUTPUT_WORDS:
            return summary
        return ' '.join(words[:GOAL_SUMMARY_MAX_OUTPUT_WORDS])

    @staticmethod
    def _normalize_summary_text(text: str) -> str:
        if not text:
            return ''
        return ' '.join(text.split())

    # IterationEventListener
    async def on_node_created(self, node: IterationNode) -> None:
        await self._rebuild_chain(node.id)
        await asyncio.sleep(0.05)
        if self.scroll_area:
            self.scroll_area.scroll_to(percent=1.0)

    async def _rebuild_chain(self, leaf_id: str) -> None:
        self._dispose_ephemeral_selectors()
        if self.chat_container is None:
            return
        # Build linear chain from root -> leaf by following parents
        chain: List[IterationNode] = []
        cur = self.controller.get_node(leaf_id)
        while cur is not None:
            chain.append(cur)
            cur = self.controller.get_node(cur.parent_id) if cur.parent_id else None
        chain.reverse()

        self.chat_container.clear()
        self.node_panels.clear()
        total = len(chain)
        with self.chat_container:
            for idx, node in enumerate(chain, start=1):
                panel = self._create_node_panel(idx, node, expanded=(idx == total))
                self.node_panels[node.id] = panel

    def _feedback_preset_context(
        self, initial: TransitionSettings
    ) -> tuple[str, Dict[str, str], Dict[str, feedback_presets.FeedbackPreset]]:
        settings_manager = get_settings()
        preset_entries = list(feedback_presets.list_enabled_presets())
        preset_lookup = {p.id: p for p in preset_entries}
        preset_label_to_id = {preset.label: preset.id for preset in preset_entries}
        preset_id_to_label = {preset.id: preset.label for preset in preset_entries}
        first_preset_label = next(iter(preset_label_to_id.keys()), '')
        initial_preset_id = (
            (getattr(initial, 'feedback_preset_id', None) or settings_manager.get_feedback_preset_id()).strip()
        )
        initial_preset_label = preset_id_to_label.get(initial_preset_id, first_preset_label)
        return initial_preset_label, preset_label_to_id, preset_lookup

    def _create_feedback_preset_placeholder(self, initial: TransitionSettings) -> SimpleNamespace:
        initial_label, label_to_id, _ = self._feedback_preset_context(initial)
        placeholder = SimpleNamespace(value=initial_label)
        placeholder._preset_value_map = label_to_id  # type: ignore[attr-defined]
        return placeholder

    def _create_feedback_preset_controls(self, initial: TransitionSettings) -> ui.select:
        """Render auto feedback preset selector and summary, returning the selector element."""
        initial_preset_label, preset_label_to_id, preset_lookup = self._feedback_preset_context(initial)
        feedback_preset_select = ui.select(
            options=list(preset_label_to_id.keys()),
            value=initial_preset_label,
            label='Auto feedback',
        ).props('dense outlined').classes('w-full')
        feedback_preset_select._preset_value_map = preset_label_to_id  # type: ignore[attr-defined]
        preset_summary_label = ui.label('').classes('text-xs text-gray-500 self-start')

        def _summarize_preset(label: str) -> str:
            preset_id = preset_label_to_id.get(label, '')
            preset = preset_lookup.get(preset_id)
            if not preset:
                return 'Fallback to classic screenshot cadence.'
            fragments: List[str] = []
            for action in preset.actions:
                if action.kind == 'wait':
                    fragments.append(f"wait {action.seconds:.1f}s")
                elif action.kind == 'keypress':
                    fragments.append(f"key {action.key} ({action.duration_ms}ms)")
                elif action.kind == 'screenshot':
                    fragments.append(f'shot "{action.label}"')
            summary = ', '.join(fragments[:6])
            if len(fragments) > 6:
                summary += f", +{len(fragments) - 6} more"
            desc = preset.description or ''
            if desc and summary:
                return f"{desc} · Auto feedback: {summary}"
            if desc:
                return desc
            return f"Auto feedback: {summary}" if summary else 'Auto feedback ready.'

        def _update_preset_summary() -> None:
            try:
                current_label = str(getattr(feedback_preset_select, 'value', initial_preset_label) or initial_preset_label)
            except Exception:
                current_label = initial_preset_label
            summary = _summarize_preset(current_label)
            try:
                preset_summary_label.text = summary
            except Exception:
                try:
                    preset_summary_label.set_text(summary)  # type: ignore[attr-defined]
                except Exception:
                    pass

        _update_preset_summary()
        feedback_preset_select.on_value_change(lambda _: _update_preset_summary())
        feedback_preset_select.on('update:model-value', lambda _: _update_preset_summary())
        return feedback_preset_select

    def _render_settings_editor(
        self,
        initial: TransitionSettings,
        *,
        persistent_selectors: bool = False,
        show_user_feedback: bool = True,
        allow_overall_goal_edit: bool = True,
    ) -> Dict[str, ui.element]:
        """Settings editor used in iteration cards."""

        if allow_overall_goal_edit:
            overall_goal = ui.textarea(label='Overall goal', value=initial.overall_goal).classes('w-full')
        else:
            overall_goal = SimpleNamespace(value=initial.overall_goal or '')
        if show_user_feedback:
            user_feedback = ui.textarea(label='User feedback', value=initial.user_feedback).classes('w-full')
        else:
            user_feedback = SimpleNamespace(value=initial.user_feedback or '')

        with ui.column().classes('w-full gap-2'):
            ui.label('Coding models').classes('w-full text-base font-medium')
            code_selector = self._register_selector(ModelSelector(
                initial_value=initial.code_model,
                vision_only=False,
                label='model',
                on_change=lambda v: None,
            ), persistent=persistent_selectors)
            code_model = code_selector.input

        with ui.column().classes('w-full gap-2 pt-2'):
            ui.label('Vision model').classes('w-full text-base font-medium')
            vision_selector = self._register_selector(ModelSelector(
                initial_value=initial.vision_model,
                vision_only=True,
                label='model',
                on_change=lambda v: None,
                single_selection=True,
            ), persistent=persistent_selectors)
            vision_model = vision_selector.input
            ui.label('This model will also be used in the analyze_screen agent tool.').classes(
                'text-xs text-gray-500 self-start'
            )

        return {
            'user_feedback': user_feedback,
            'overall_goal': overall_goal,
            'code_model': code_model,
            'vision_model': vision_model,
        }

    def _create_node_panel(self, index: int, node: IterationNode, *, expanded: bool) -> ui.element:
        with ui.expansion(f'Iteration {index}', value=expanded).classes(
            'w-full shadow-sm rounded-lg border border-gray-200/70 dark:border-gray-700/50'
        ) as panel:
            self._build_node_card(index, node, show_heading=False)

        try:
            panel.value = expanded
        except Exception:
            pass

        return panel

    def _build_node_card(self, index: int, node: IterationNode, *, show_heading: bool = True) -> ui.card:
        with ui.card().classes('w-full p-4') as card:
            if show_heading:
                    with ui.row().classes('items-center justify-between w-full'):
                        ui.label(f'Iteration {index}').classes('text-lg font-semibold')

            first_output = next(iter(node.outputs.values())) if node.outputs else None
            first_output_slug = next(iter(node.outputs.keys())) if node.outputs else None
            preferred_artifacts = node.input_artifacts or (first_output.artifacts if first_output else None)
            analysis_map: Dict[str, Any] = {}
            if preferred_artifacts:
                try:
                    analysis_map = dict(getattr(preferred_artifacts, 'analysis', {}) or {})
                except Exception:
                    analysis_map = {}

            input_messages = self._get_input_messages(node, first_output_slug)

            messages_dialog = None
            open_messages_handler = None
            if input_messages:
                messages_dialog = ui.dialog()
                messages_dialog.props('persistent')
                msgs_snapshot = list(input_messages)

                def _open_messages_dialog(msgs=msgs_snapshot) -> None:
                    render_message_history_dialog(messages_dialog, list(msgs))
                    messages_dialog.open()

                messages_dialog.on('hide', lambda _: messages_dialog.clear())
                open_messages_handler = _open_messages_dialog

            summary_dialog, summary_button_label, summary_disabled = create_node_summary_dialog(node)

            meta_rendered = False

            def _render_meta_controls() -> None:
                nonlocal meta_rendered
                if meta_rendered:
                    return
                with ui.row().classes('w-full items-center gap-2 mb-2'):
                    if open_messages_handler:
                        ui.button('📋 Messages', on_click=open_messages_handler).props('flat dense').classes('text-sm p-0 min-h-0 self-start')
                meta_rendered = True

            show_input_column = index > 1
            allow_meta = index > 1
            operation_basis = 'basis-1/3' if show_input_column else 'basis-1/2'
            output_basis = 'basis-1/3' if show_input_column else 'basis-1/2'
            column_header_class = 'text-[11px] uppercase tracking-[0.4em] text-gray-400 dark:text-gray-500'

            with ui.row().classes('w-full items-start gap-4 flex-nowrap'):
                if show_input_column:
                    with ui.column().classes('basis-1/3 min-w-0 gap-4 input-column'):
                        ui.label('INPUT').classes(column_header_class)
                        if allow_meta:
                            _render_meta_controls()
                        asset_label_map = {}
                        artifacts = preferred_artifacts
                        try:
                            assets = list(getattr(artifacts, 'assets', []) or [])
                            for asset in assets:
                                if asset.role == 'input':
                                    asset_label_map[asset.path] = str(asset.metadata.get('label', '') or '').strip()
                        except Exception:
                            asset_label_map = {}

                        if node.source_model_slug:
                            ui.label(f'{node.source_model_slug}').classes('text-xs uppercase tracking-wide text-gray-500')

                        ui.label('AUTO FEEDBACK').classes('text-sm font-semibold')
                        input_entries: List[tuple[int, str, str, str]] = []
                        primary_html_url = ''
                        limit_note = ''
                        try:
                            from pathlib import Path as _P
                            raw_paths = list(getattr(artifacts, 'input_screenshot_filenames', []) or [])
                            for idx, raw_path in enumerate(raw_paths):
                                if not (raw_path or '').strip():
                                    continue
                                p = _P(raw_path)
                                artifact_url = f"/artifacts/{p.name}" if p.exists() else ''
                                html_candidate = p.with_suffix('.html')
                                html_url = f"/artifacts/{html_candidate.name}" if html_candidate.exists() else ''
                                input_entries.append((idx, raw_path, artifact_url, html_url))
                                if not primary_html_url and html_url:
                                    primary_html_url = html_url
                            limit_note = analysis_map.get('input_screenshot_limit', '') if isinstance(analysis_map, dict) else ''
                        except Exception:
                            input_entries = []
                            limit_note = ''
                            primary_html_url = ''

                        if artifacts is None:
                            ui.label('Input analysis pending. Select an output to continue.').classes('text-sm text-gray-500')
                        elif input_entries:
                            if limit_note:
                                ui.label(limit_note).classes('text-xs text-amber-300')
                            with ui.row().classes('w-full gap-2 flex-wrap'):
                                for idx, raw_path, artifact_url, html_url in input_entries:
                                    with ui.column().classes('gap-1 w-[120px]'):
                                        target_link = artifact_url or raw_path
                                        with ui.link('', target_link, new_tab=True).classes('block no-underline'):
                                            ui.image(raw_path).classes('w-[120px] h-[80px] object-cover border border-gray-600 rounded hover:border-blue-400 transition-colors duration-150')
                                        with ui.row().classes('items-center justify-between w-full'):
                                            label_text = asset_label_map.get(raw_path) or f'#{idx + 1}'
                                            ui.label(label_text).classes('text-xs text-gray-400')
                            size = format_html_size(node.html_input)
                            with ui.row().classes('items-center gap-2 mt-1'):
                                ui.icon('content_copy').classes('text-sm cursor-pointer').on('click', lambda html=node.html_input: self._copy_to_clipboard(html))
                                ui.label('HTML').classes('text-sm')
                                ui.label(f'({size})').classes('text-sm text-gray-600 dark:text-gray-400')
                                if primary_html_url:
                                    ui.link('Open', primary_html_url, new_tab=True).classes('text-sm')
                        else:
                            ui.label('(no input screenshots yet)').classes('text-sm text-gray-500')

                        in_logs = list(getattr(artifacts, 'input_console_logs', []) or []) if artifacts else []
                        in_title = f"Console logs ({'empty' if len(in_logs) == 0 else len(in_logs)})"
                        with ui.expansion(in_title):
                            if in_logs:
                                in_logs_text = '\n\n'.join(in_logs)
                                ui.markdown(in_logs_text)
                            else:
                                ui.label('(no console logs)')

                        _va_raw = extract_vision_summary(artifacts)
                        _va_lines = [line for line in _va_raw.splitlines() if line.strip()]
                        va_title = f"Vision Analysis ({'empty' if len(_va_lines) == 0 else len(_va_lines)})"
                        with ui.expansion(va_title):
                            va_text = _va_raw
                            has_inputs = bool(getattr(artifacts, 'input_screenshot_filenames', []) if artifacts else [])
                            if not has_inputs:
                                va_text = '(no input screenshot)'
                            elif not va_text.strip():
                                va_text = '(pending)'
                            else:
                                va_text = va_text.replace('\n', '\n\n')
                            ui.markdown(va_text)

                with ui.column().classes(f'{operation_basis} min-w-0 gap-3 operation-column'):
                    if allow_meta and not show_input_column:
                        _render_meta_controls()
                    ui.label('OPERATION').classes(column_header_class)
                    inputs = self._render_settings_editor(
                        node.settings,
                        allow_overall_goal_edit=False,
                        show_user_feedback=(index > 1),
                    )

                def _collect_transition_settings(fallback_code_slug: str = '', *, user_feedback_override: str | None = None) -> TransitionSettings:
                    settings_manager = get_settings()
                    feedback_preset_id = self._extract_preset_id(inputs['feedback_preset'])
                    code_template = settings_manager.get_code_template()
                    code_first_prompt_template = settings_manager.get_code_first_prompt_template()
                    vision_template = settings_manager.get_vision_template()
                    iter_shots = settings_manager.get_input_screenshot_count()
                    selected_model = inputs['code_model'].value or fallback_code_slug
                    feedback_value = user_feedback_override if user_feedback_override is not None else (inputs['user_feedback'].value or '')
                    updated = TransitionSettings(
                        code_model=selected_model,
                        vision_model=inputs['vision_model'].value or '',
                        overall_goal=node.settings.overall_goal,
                        user_feedback=feedback_value,
                        code_template=code_template,
                        code_system_prompt_template=settings_manager.get_code_system_prompt_template(),
                        code_first_prompt_template=code_first_prompt_template,
                        vision_template=vision_template,
                        input_screenshot_count=iter_shots,
                        feedback_preset_id=feedback_preset_id,
                    )
                    settings_manager.save_settings(updated)
                    return updated

                async def _transform_current_node() -> None:
                    if not self._begin_operation('Transform'):
                        return
                    try:
                        updated = _collect_transition_settings()
                        await self.controller.rerun_node(node.id, updated)
                    except asyncio.CancelledError:
                        ui.notify('Transform cancelled', color='warning', timeout=2000)
                    except Exception as exc:
                        op_status.enqueue_notification(f'Transform failed: {exc}', color='negative', timeout=0, close_button=True)
                    finally:
                        self._end_operation()

                with ui.column().classes('w-[64px] shrink-0 items-center justify-center h-full transform-column'):
                    ui.button('', icon='arrow_forward', on_click=lambda: asyncio.create_task(_transform_current_node())).props('unelevated size=lg').classes('w-16 h-36 bg-primary-500 text-white shadow-lg rounded-xl mt-4')

                with ui.column().classes(f'{output_basis} min-w-0 output-column'):
                    if allow_meta and not show_input_column:
                        _render_meta_controls()
                    output_messages_dialog = ui.dialog()
                    output_messages_dialog.props('persistent')

                    def _open_output_messages_dialog() -> None:
                        if not node.outputs:
                            ui.notify('No messages available for this iteration yet.', color='info', timeout=2000)
                            return
                        message_slugs = list(node.outputs.keys())

                        def _render_for(slug: str) -> None:
                            output = node.outputs.get(slug)
                            if not output:
                                ui.notify(f'Messages missing for {slug}', color='warning', timeout=2000)
                                return
                            history = list(output.messages or [])
                            if output.assistant_response:
                                history.append({"role": "assistant", "content": output.assistant_response})
                            if not history:
                                history = [{"role": "system", "content": "(no message history captured)"}]

                            def _header_controls(row: ui.row, current_slug: str = slug) -> None:
                                with row:
                                    selector = ui.select(
                                        options=message_slugs,
                                        value=current_slug,
                                    ).props('dense outlined hide-dropdown-icon').classes('w-40 text-xs text-gray-500')

                                    def _on_change(e: Any) -> None:
                                        value = getattr(e, 'value', None)
                                        if not value:
                                            args = getattr(e, 'args', None)
                                            if isinstance(args, dict):
                                                value = args.get('value')
                                            elif args:
                                                value = args
                                        if value is not None:
                                            # Handle case where NiceGUI emits index instead of value
                                            try:
                                                # Check if value is an integer (index) or string representation of integer
                                                if isinstance(value, int):
                                                    idx = value
                                                elif isinstance(value, str) and value.isdigit():
                                                    idx = int(value)
                                                else:
                                                    idx = None

                                                if idx is not None and 0 <= idx < len(message_slugs):
                                                    # Use index to get the actual slug
                                                    slug = message_slugs[idx]
                                                else:
                                                    # Use value directly as slug
                                                    slug = str(value)
                                            except (ValueError, IndexError):
                                                slug = str(value)

                                            _render_for(slug)

                                    selector.on('update:model-value', _on_change)

                            render_message_history_dialog(output_messages_dialog, history, header_controls=_header_controls)

                        _render_for(message_slugs[0])
                        output_messages_dialog.open()

                    with ui.row().classes('w-full items-center justify-between'):
                        ui.label('OUTPUT').classes(column_header_class)
                        if node.outputs:
                            with ui.row().classes('items-center gap-2'):
                                ui.button('📋 Messages', on_click=_open_output_messages_dialog).props('flat dense').classes('text-xs font-semibold tracking-wide uppercase text-gray-500')
                                summary_handler = summary_dialog.open if not summary_disabled else (lambda: None)
                                summary_btn = ui.button('Summary', on_click=summary_handler).props('flat dense').classes('text-xs font-semibold tracking-wide uppercase text-gray-500')
                                if summary_disabled:
                                    summary_btn.props('disable')

                    if node.outputs:
                        with ui.column().classes('w-full gap-1 -mt-2 output-auto-feedback'):
                            feedback_preset_select = self._create_feedback_preset_controls(node.settings)
                    else:
                        feedback_preset_select = self._create_feedback_preset_placeholder(node.settings)
                    inputs['feedback_preset'] = feedback_preset_select

                    for model_slug, out in node.outputs.items():
                        with ui.column().classes('w-full min-w-0 gap-2 border rounded p-2'):
                            ui.label(f'{model_slug}').classes('text-sm font-semibold')
                            try:
                                cost = getattr(out, 'total_cost', None)
                                time_s = getattr(out, 'generation_time', None)
                                cost_str = (f"${cost:.6f}" if isinstance(cost, (int, float)) else "$—")
                                time_str = (f"{float(time_s):.1f}s" if isinstance(time_s, (int, float)) else "—")
                                calls_value = getattr(out, 'tool_call_count', None)
                                calls_number = 0
                                if calls_value is not None:
                                    try:
                                        calls_number = int(calls_value)
                                    except Exception:
                                        calls_number = 0
                                ui.label(f"{cost_str} · {time_str} · {calls_number} tool calls").classes('text-xs text-gray-500 dark:text-gray-400 leading-tight')
                            except Exception:
                                ui.label("$— · — · 0 tool calls").classes('text-xs text-gray-500 dark:text-gray-400 leading-tight')
                            out_png = out.artifacts.screenshot_filename
                            if out_png:
                                ui.image(out_png).classes('w-full h-auto max-w-full rounded border border-gray-600')
                            from pathlib import Path as _OutputPath
                            out_html_url = ''
                            try:
                                snap_path = _OutputPath(out_png)
                                if snap_path.exists():
                                    html_path = snap_path.with_suffix('.html')
                                    if html_path.exists():
                                        out_html_url = f"/artifacts/{html_path.name}"
                            except Exception:
                                out_html_url = ''
                            diff_dialog = ui.dialog()
                            with diff_dialog, ui.card().classes('w-[90vw] max-w-[900px]'):
                                with ui.row().classes('items-center justify-between w-full'):
                                    ui.label('Diff vs input').classes('text-lg font-semibold')
                                    ui.button(icon='close', on_click=diff_dialog.close).props('flat round dense')
                                diff_html = self._create_visual_diff(node.html_input, out.html_output)
                                ui.html(f'<div class="border rounded p-4 diff-body">{diff_html}</div>')
                            size = format_html_size(out.html_output)
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('content_copy').classes('text-sm cursor-pointer').on('click', lambda html=out.html_output: self._copy_to_clipboard(html))
                                ui.label('HTML').classes('text-sm')
                                ui.label(f'({size})').classes('text-sm text-gray-600 dark:text-gray-400')
                                ui.label(':').classes('text-sm')
                                ui.link('Open', out_html_url, new_tab=True).classes('text-sm')
                                ui.button('Diff', on_click=diff_dialog.open).props('flat dense').classes('text-sm p-0 min-h-0')
                                if (out.reasoning_text or '').strip():
                                    with ui.dialog() as reasoning_dialog:
                                        reasoning_dialog.props('persistent')
                                        with ui.card().classes('w-[90vw] max-w-[900px]'):
                                            with ui.row().classes('items-center justify-between w-full'):
                                                ui.label('Model Reasoning').classes('text-lg font-semibold')
                                                ui.button(icon='close', on_click=reasoning_dialog.close).props('flat round dense')
                                            raw_reasoning = (out.reasoning_text or '')
                                            safe_reasoning = _html.escape(raw_reasoning, quote=False)
                                            ui.markdown(safe_reasoning)
                                    ui.icon('psychology').classes('text-gray-500 cursor-pointer').on('click', reasoning_dialog.open)
                            out_logs = list(out.artifacts.console_logs or [])
                            out_title = f"Console logs ({'empty' if len(out_logs) == 0 else len(out_logs)})"
                            with ui.expansion(out_title):
                                if out_logs:
                                    out_logs_text = '\n\n'.join(out_logs)
                                    ui.markdown(out_logs_text)
                                else:
                                    ui.label('(no console logs)')

                            async def _select_model(slug: str = model_slug) -> None:
                                if not self._begin_operation('Select'):
                                    return
                                try:
                                    updated = _collect_transition_settings(slug, user_feedback_override='')
                                    await self.controller.select_model(node.id, updated, slug)
                                except asyncio.CancelledError:
                                    ui.notify(f'Select cancelled for {slug}', color='warning', timeout=2000)
                                except Exception as exc:
                                    op_status.enqueue_notification(f'Select failed: {exc}', color='negative', timeout=0, close_button=True)
                                finally:
                                    self._end_operation()

                            ui.button('Select', on_click=(lambda slug=model_slug: lambda: asyncio.create_task(_select_model(slug)))(model_slug)).classes('w-full')
        return card

    def _get_input_messages(self, node: IterationNode, preferred_slug: str | None) -> List[Dict[str, Any]]:
        parent_id = node.parent_id
        if parent_id:
            parent = self.controller.get_node(parent_id)
            if parent:
                parent_output = parent.outputs.get(preferred_slug) if preferred_slug else None
                if parent_output is None and parent.outputs:
                    parent_output = next(iter(parent.outputs.values()))
                if parent_output:
                    history = list(parent_output.messages or [])
                    if parent_output.assistant_response:
                        history.append({"role": "assistant", "content": parent_output.assistant_response})
                    return history

        current_output = node.outputs.get(preferred_slug) if preferred_slug else None
        if current_output is None and node.outputs:
            current_output = next(iter(node.outputs.values()))
        if current_output and current_output.messages:
            return list(current_output.messages)
        return []

    # --- Operation status helpers ---
    def _begin_operation(self, title: str) -> bool:
        if self._op_busy:
            op_status.enqueue_notification('Another operation is running. Please wait until it finishes.', color='warning')
            return False
        self._op_busy = True
        op_status.clear_all()
        task_registry.clear_all_tasks()
        self._refresh_phase(force=True)
        return True

    def _end_operation(self) -> None:
        self._op_busy = False
        # Ensure UI resets cleanly on success or error
        try:
            op_status.clear_all()
            task_registry.clear_all_tasks()
        except Exception:
            pass
        self._refresh_phase(force=True)

    def _refresh_phase(self, *, force: bool = False) -> None:
        if self._status_panel is None:
            return
        now = time.monotonic()
        if not force and (now - self._last_status_refresh) < self._status_refresh_interval:
            return
        self._last_status_refresh = now

        phases = op_status.get_all_phases()
        self._status_panel.update(phases, busy=self._op_busy)

    def _cancel_worker(self, worker: str) -> None:
        phases = op_status.get_all_phases()
        phase_info = phases.get(worker)
        is_coding = False
        if phase_info is not None:
            raw_phase = phase_info[0] if isinstance(phase_info, (tuple, list)) and phase_info else phase_info
            try:
                text = str(raw_phase or '')
            except Exception:
                text = ''
            if '|' in text:
                head = text.split('|', 1)[0].strip().lower()
                is_coding = head == 'coding'
            else:
                is_coding = text.lower().startswith('coding')
        if not is_coding:
            ui.notify('Cancellation available only during coding phase', color='info', timeout=2000)
            return
        success = False
        try:
            success = task_registry.cancel_task(worker)
        except Exception:
            success = False
        if success:
            op_status.clear_phase(worker)
            ui.notify(f'Cancelled {worker}', color='warning', timeout=2000)
        else:
            ui.notify(f'Worker {worker} already completed or not found', color='info', timeout=2000)


    # --- Utilities ---
    def _extract_preset_id(self, element: ui.element) -> str:
        try:
            raw = getattr(element, 'value', '')
            value_map = getattr(element, '_preset_value_map', {}) or {}
            mapped = value_map.get(raw, raw)
        except Exception:
            mapped = ''
        return str(mapped or '')

    def _copy_to_clipboard(self, text: str) -> None:
        try:
            js_text = json.dumps(text)
            ui.run_javascript(f'navigator.clipboard.writeText({js_text});')
            ui.notify('HTML copied to clipboard')
        except Exception as exc:
            ui.notify(f'Copy failed: {exc}', color='negative', timeout=0, close_button=True)

    def _flush_notifications(self) -> None:
        """Display any queued notifications from background tasks."""
        try:
            items = op_status.drain_notifications()
        except Exception:
            items = []
        for it in items:
            try:
                text = str(it.get('text', ''))
                color = str(it.get('color', 'negative'))
                timeout = it.get('timeout', 0)
                close_button = bool(it.get('close_button', True))
                ui.notify(text, color=color, timeout=timeout, close_button=close_button)
            except Exception:
                # Best-effort; drop malformed items
                pass


    def _register_selector(self, selector: ModelSelector, *, persistent: bool) -> ModelSelector:
        target = self._persistent_selectors if persistent else self._ephemeral_selectors
        target.append(selector)
        return selector

    def _dispose_selector_list(self, selectors: List[ModelSelector]) -> None:
        while selectors:
            selector = selectors.pop()
            try:
                selector.dispose()
            except Exception:
                pass

    def _dispose_ephemeral_selectors(self) -> None:
        self._dispose_selector_list(self._ephemeral_selectors)

    def _dispose_all_selectors(self) -> None:
        self._dispose_selector_list(self._ephemeral_selectors)
        self._dispose_selector_list(self._persistent_selectors)

    def _stop_timers(self) -> None:
        for attr in ('_status_timer', '_notif_timer'):
            timer = getattr(self, attr, None)
            if timer is None:
                continue
            try:
                timer.cancel()
            except Exception:
                pass
            setattr(self, attr, None)

    async def shutdown(self) -> None:
        if self._shutdown_called:
            return
        self._shutdown_called = True
        self._stop_timers()
        self._dispose_all_selectors()
        try:
            task_registry.clear_all_tasks()
        except Exception:
            pass
        try:
            op_status.clear_all()
            op_status.drain_notifications()
        except Exception:
            pass
        try:
            self.controller.remove_listener(self)
        except Exception:
            pass
        if self._status_panel is not None:
            try:
                self._status_panel.clear()
            except Exception:
                pass
            self._status_panel = None
        self.node_panels.clear()
        for attr in ('chat_container', 'goal_panel', 'scroll_area'):
            elem = getattr(self, attr, None)
            if elem is None:
                continue
            try:
                elem.clear()
            except Exception:
                pass
            setattr(self, attr, None)
        self._op_busy = False




    def _create_visual_diff(self, text1: str, text2: str) -> str:
        """Return HTML for a modern-looking inline diff between two texts.
        The HTML tags within inputs are escaped so they render as text.
        """
        try:
            dmp = diff_match_patch()
            diffs = dmp.diff_main(text1 or '', text2 or '')
            dmp.diff_cleanupSemantic(diffs)
        except Exception:
            # Fallback: plain escaped output if diffing fails
            safe1 = _html.escape(text1 or '')
            safe2 = _html.escape(text2 or '')
            if safe1 == safe2:
                return safe2
            return safe1 + ' -> ' + safe2

        html_parts: List[str] = []
        for op, segment in diffs:
            escaped = _html.escape(segment)
            if op == 1:  # Insert
                html_parts.append(f'<span class="diff-insert">{escaped}</span>')
            elif op == -1:  # Delete
                html_parts.append(f'<span class="diff-delete">{escaped}</span>')
            else:  # Equal
                html_parts.append(escaped)
        return ''.join(html_parts)
