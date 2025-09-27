from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any, Optional
import sys

import yaml
from PIL import Image
from nicegui import ui


class _LiteralString(str):
    """Marker to force YAML literal block output for multi-line values."""


def _literal_string_representer(dumper, data):
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')


yaml.add_representer(_LiteralString, _literal_string_representer)

# Ensure src/ is importable when running as a standalone script
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
for path in (str(ROOT), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)

# Guarantee that `src` is treated as a package even when running the script standalone.
import types
if 'src' not in sys.modules:
    src_pkg = types.ModuleType('src')
    src_pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    sys.modules['src'] = src_pkg

import importlib

try:
    get_config = importlib.import_module('src.config').get_config  # type: ignore[attr-defined]
    ModelSelector = importlib.import_module('src.model_selector').ModelSelector  # type: ignore[attr-defined]
    or_client = importlib.import_module('src.or_client')  # type: ignore[assignment]
    apply_theme = importlib.import_module('src.ui_theme').apply_theme  # type: ignore[attr-defined]
except Exception as exc:  # pragma: no cover - surface friendly error in UI
    raise RuntimeError(f'Failed to import app modules: {exc}')

_MAX_ATTEMPTS_DEFAULT = 12
DEFAULT_MODEL_SLUG = 'qwen/qwen3-vl-235b-a22b-thinking'


@dataclass
class DetectionState:
    is_running: bool = False
    model_slug: Optional[str] = None
    detected_limit: Optional[int] = None
    reached_max: bool = False
    error_message: Optional[str] = None
    log_lines: list[str] = field(default_factory=list)
    selector: Optional[ModelSelector] = None
    result_label: Optional[Any] = None
    log_output: Optional[Any] = None
    detect_button: Optional[Any] = None
    max_attempt_input: Optional[Any] = None
    update_button: Optional[Any] = None
    current_limit_label: Optional[Any] = None


state = DetectionState()


def _append_log(message: str) -> None:
    state.log_lines.append(message)
    if state.log_output is not None:
        state.log_output.value = '\n'.join(state.log_lines)


def _format_exception(exc: Exception) -> str:
    label = exc.__class__.__name__
    text = str(exc)
    if not text:
        return label
    return f'{label}: {text}'



@lru_cache(maxsize=256)
def _generate_image_data_url(seed: int) -> str:
    base = (seed * 73) % 256
    color = (
        (base + 64) % 256,
        (base * 5 + 32) % 256,
        (base * 11 + 16) % 256,
    )
    img = Image.new('RGB', (32, 32), color=color)
    buf = BytesIO()
    img.save(buf, format='PNG')
    return or_client.encode_image_to_data_url(buf.getvalue())


async def _send_test_message(model: str, image_count: int) -> None:
    parts: list[dict[str, object]] = [{"type": "text", "text": f"Please confirm {image_count} image(s)."}]
    for idx in range(image_count):
        seed = image_count * 257 + idx
        parts.append({
            "type": "image_url",
            "image_url": {"url": _generate_image_data_url(seed), "detail": "low"},
        })
    messages = [{"role": "user", "content": parts}]
    await or_client.chat(
        messages=messages,
        model=model,
        temperature=0,
        max_tokens=64,
    )


async def _detect_limit() -> None:
    if state.is_running:
        return
    selector = state.selector
    if selector is None:
        ui.notify('Model selector not ready', color='negative')
        return

    raw_value = (selector.get_value() or '').strip()
    if not raw_value:
        ui.notify('Select a vision-capable model first', color='negative')
        return
    slug = raw_value.split(',')[0].strip()
    if not slug:
        ui.notify('Select a vision-capable model first', color='negative')
        return

    try:
        max_attempts = int(state.max_attempt_input.value) if state.max_attempt_input else _MAX_ATTEMPTS_DEFAULT
    except Exception:
        max_attempts = _MAX_ATTEMPTS_DEFAULT
    max_attempts = max(1, min(max_attempts, 32))

    state.is_running = True
    state.model_slug = slug
    state.detected_limit = None
    state.reached_max = False
    state.error_message = None
    state.log_lines.clear()

    if state.detect_button is not None:
        state.detect_button.props('loading')
    if state.update_button is not None:
        state.update_button.visible = False
    if state.result_label is not None:
        state.result_label.text = f'Running detection for {slug} (up to {max_attempts} images)...'
    _append_log(f'Starting detection for {slug}')

    last_success = 0

    for count in range(1, max_attempts + 1):
        _append_log(f'Trying with {count} image(s)...')
        try:
            await _send_test_message(slug, count)
            last_success = count
            _append_log(f'  ✓ {count} image(s) accepted')
            await asyncio.sleep(0.25)
        except Exception as exc:
            err = _format_exception(exc)
            state.error_message = err
            _append_log(f'  ✗ {count} image(s) rejected: {err}')
            break

    if state.error_message is None and last_success >= max_attempts:
        state.detected_limit = last_success
        state.reached_max = True
        summary = f'Model accepted all {last_success} tested image(s); limit may exceed {max_attempts}.'
    else:
        state.detected_limit = last_success
        if state.error_message is None:
            summary = f'Model accepted {last_success} image(s); no error encountered.'
        elif last_success == 0:
            summary = f'Model rejected the first image: {state.error_message}'
        else:
            summary = f'Estimated limit: {last_success} image(s). Last error: {state.error_message}'

    if state.result_label is not None:
        state.result_label.text = summary
    else:
        ui.notify(summary, color='positive')

    if state.update_button is not None:
        state.update_button.visible = bool(state.detected_limit and state.detected_limit > 0)

    state.is_running = False
    if state.detect_button is not None:
        state.detect_button.props(remove='loading')


def _load_model_limits_from_yaml() -> dict[str, int]:
    cfg_path = ROOT / 'config.yaml'
    try:
        raw = cfg_path.read_text(encoding='utf-8')
        data = yaml.safe_load(raw) or {}
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    iteration = data.get('iteration')
    if not isinstance(iteration, dict):
        return {}

    screenshots = iteration.get('input_screenshots')
    if not isinstance(screenshots, dict):
        return {}

    raw_limits = screenshots.get('model_limits')
    limits: dict[str, int] = {}
    if isinstance(raw_limits, dict):
        for key, value in raw_limits.items():
            if isinstance(value, dict):
                for slug, nested in value.items():
                    try:
                        parsed = int(nested)
                    except Exception:
                        continue
                    if parsed > 0:
                        limits[str(slug)] = parsed
            else:
                try:
                    parsed = int(value)
                except Exception:
                    continue
                if parsed > 0:
                    limits[str(key)] = parsed
    return limits




def _apply_literal_strings(payload: dict) -> None:
    templates = payload.get('templates')
    if not isinstance(templates, dict):
        return
    for key in ('code', 'vision'):
        value = templates.get(key)
        if isinstance(value, str) and '\n' in value:
            templates[key] = _LiteralString(value)


def _update_current_limit_label(slug: str) -> None:
    if state.current_limit_label is None:
        return
    existing = _load_model_limits_from_yaml().get(slug)
    if existing is None:
        state.current_limit_label.text = 'Config.yaml does not have a saved limit for this model.'
    else:
        state.current_limit_label.text = f'Current config limit: {existing} image(s).'


def _on_selection_change(value: str) -> None:
    slug = (value or '').split(',')[0].strip()
    if not slug:
        if state.current_limit_label is not None:
            state.current_limit_label.text = 'Select a model to see its saved limit.'
        return
    state.model_slug = slug
    _update_current_limit_label(slug)


def _write_limit_to_config() -> None:
    if not state.model_slug or not state.detected_limit or state.detected_limit <= 0:
        ui.notify('Run detection successfully before updating config.yaml', color='negative')
        return

    cfg_path = ROOT / 'config.yaml'
    try:
        text = cfg_path.read_text(encoding='utf-8')
        data = yaml.safe_load(text) or {}
    except Exception as exc:
        ui.notify(f'Failed to read config.yaml: {exc}', color='negative')
        return

    if not isinstance(data, dict):
        data = {}

    iteration = data.setdefault('iteration', {})
    if not isinstance(iteration, dict):
        iteration = {}
        data['iteration'] = iteration

    screenshots = iteration.setdefault('input_screenshots', {})
    if not isinstance(screenshots, dict):
        screenshots = {}
        iteration['input_screenshots'] = screenshots

    limits = screenshots.setdefault('model_limits', {})
    if not isinstance(limits, dict):
        limits = {}
        screenshots['model_limits'] = limits

    limits[state.model_slug] = int(state.detected_limit)

    _apply_literal_strings(data)

    try:
        dumped = yaml.safe_dump(
            data,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        cfg_path.write_text(dumped, encoding='utf-8')
    except Exception as exc:
        ui.notify(f'Failed to update config.yaml: {exc}', color='negative')
        return

    ui.notify(f'Updated config.yaml: {state.model_slug} -> {state.detected_limit} image(s)', color='positive')
    if state.current_limit_label is not None:
        state.current_limit_label.text = f'Current config limit: {state.detected_limit} image(s).'


# ---------------- UI wiring ---------------- #
app_config = get_config()
apply_theme()

initial_slug = DEFAULT_MODEL_SLUG or getattr(app_config, 'vision_model', '')
initial_slug = initial_slug.strip()

with ui.column().classes('max-w-3xl mx-auto p-6 gap-4'):
    ui.label('Model Image Limit Detector').classes('text-2xl font-semibold')
    ui.label('Test how many images a given OpenRouter model accepts in a single request.').classes('text-sm text-gray-400')

    with ui.card().classes('w-full p-4 gap-3 flex flex-col'):
        ui.label('Choose a vision-capable model').classes('text-sm text-gray-300')
        state.selector = ModelSelector(
            initial_value=initial_slug,
            vision_only=True,
            single_selection=True,
            require_image_input=True,
            on_change=_on_selection_change,
            label='Vision model',
        )
        state.current_limit_label = ui.label('Select a model to see its saved limit.').classes('text-sm text-gray-400')
        state.max_attempt_input = ui.number('Max attempts', value=_MAX_ATTEMPTS_DEFAULT, min=1, max=32, step=1).props('outlined dense').classes('w-40')
        state.result_label = ui.label('Select a model and click "Detect limit".').classes('text-sm')
        state.log_output = ui.textarea('Detection log', value='').props('readonly auto-grow').classes('w-full text-sm')
        state.detect_button = ui.button('Detect limit', on_click=_detect_limit).classes('self-start')
        state.update_button = ui.button('Update config.yaml', on_click=_write_limit_to_config).classes('self-start')
        state.update_button.visible = False

    # Initialize label for the default selection
    if initial_slug:
        state.model_slug = initial_slug
        state.selector.set_value(initial_slug)
        _on_selection_change(initial_slug)


ui.run(native=False, title='Model Image Limit Detector', reload=False)
