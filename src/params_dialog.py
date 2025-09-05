from __future__ import annotations

from typing import List, Dict

from nicegui import ui

try:
    from . import or_client as orc
    from . import model_params as mp
except Exception:  # pragma: no cover
    import or_client as orc  # type: ignore
    import model_params as mp  # type: ignore


async def _resolve_supported(slug: str) -> List[str]:
    try:
        models = await orc.list_models(force_refresh=False, limit=2000)
        sp = next((m.supported_parameters for m in models if m.id == slug), []) or []
        return sorted({str(x) for x in sp})
    except Exception:
        return []


async def open_params_dialog(slug: str, title_name: str | None = None) -> None:
    """Open a modal dialog to edit per-model parameters.

    The table and its rows are created fresh on each open to avoid empty or stale state.
    """
    supported = await _resolve_supported(slug)

    with ui.dialog() as dlg:
        dlg.props('persistent')
        with ui.card().classes('w-[900px] max-w-[95vw]').style('position: relative;'):
            with ui.row().classes('items-center w-full'):
                ui.label(f'Parameters: {title_name or slug}')\
                    .classes('text-lg font-semibold truncate pr-10')
            ui.button(icon='close', on_click=dlg.close)\
                .props('flat round dense')\
                .style('position: absolute; top: 8px; right: 8px;')

            with ui.column().classes('gap-2 w-full'):
                # Simple, reliable two-column grid (avoids QTable slot issues)
                # Use minmax(0, ...) to prevent horizontal overflow and hide any accidental x-scroll.
                grid = ui.element('div')\
                    .style('display: grid; grid-template-columns: minmax(200px, 1fr) minmax(0, 2fr); gap: 10px; max-width: 100%;')\
                    .classes('w-full max-h-[60vh] overflow-y-auto overflow-x-hidden')
                inputs: Dict[str, ui.input] = {}
                with grid:
                    for p in supported:
                        ui.label(p).classes('text-sm font-mono break-words')
                        inputs[p] = ui.input(value='').props('dense outlined clearable hide-bottom-space').classes('w-full').style('max-width: 100%; box-sizing: border-box;')

            # Prefill values
            try:
                existing = mp.get_params(slug)
                for k, inp in inputs.items():
                    try:
                        inp.set_value(existing.get(k, ''))
                    except Exception:
                        inp.value = existing.get(k, '')
            except Exception:
                pass

            with ui.row().classes('justify-end gap-2'):
                def _save():
                    try:
                        collected: Dict[str, str] = {}
                        for k, inp in inputs.items():
                            v = str(getattr(inp, 'value', '') or '').strip()
                            if v:
                                collected[k] = v
                        mp.set_params(slug, collected)
                        ui.notify('Parameters saved')
                        dlg.close()
                    except Exception as exc:
                        ui.notify(f'Failed to save: {exc}', color='negative', timeout=0, close_button=True)
                ui.button('Save', on_click=_save).props('unelevated')
                ui.button('Cancel', on_click=dlg.close).props('flat')

    dlg.open()


def build_rows_for_table(supported: List[str] | None, existing: Dict[str, str] | None, fallback: List[str] | None = None) -> List[Dict[str, str]]:
    """Pure helper to build table rows from supported keys and existing values.

    Build rows from supported keys; if none provided, returns an empty list (no fallback).
    """
    supp = list(sorted({str(x) for x in (supported or [])}))
    exist = existing or {}
    rows = [{"parameter": p, "value": str(exist.get(p, ""))} for p in supp]
    return rows
