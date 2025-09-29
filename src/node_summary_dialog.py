from __future__ import annotations

import html as _html
from typing import Dict, List, Tuple

from nicegui import ui

from .interfaces import IterationNode
from .view_utils import format_html_size


def _format_kb_from_bytes(size_bytes: int) -> str:
    size_kb = size_bytes / 1024
    return f"{size_kb:.2f} KB"


def create_node_summary_dialog(node: IterationNode) -> Tuple[ui.dialog, str, bool]:
    """Return a NiceGUI dialog summarizing costs, timings, and HTML size for a node."""
    outputs = node.outputs or {}
    data_rows: List[Dict[str, object]] = []
    cost_values: List[float] = []
    time_values: List[float] = []
    size_values: List[int] = []

    for model_slug, out in outputs.items():
        raw_cost = getattr(out, 'total_cost', None)
        raw_time = getattr(out, 'generation_time', None)
        html_output = getattr(out, 'html_output', '') or ''
        size_bytes = len(html_output.encode('utf-8'))

        cost_value = float(raw_cost) if isinstance(raw_cost, (int, float)) else None
        time_value = float(raw_time) if isinstance(raw_time, (int, float)) else None

        if cost_value is not None:
            cost_values.append(cost_value)
        if time_value is not None:
            time_values.append(time_value)
        size_values.append(size_bytes)

        data_rows.append({
            'model': model_slug,
            'price_value': cost_value,
            'time_value': time_value,
            'size_value': size_bytes,
            'price_display': f"${cost_value:.6f}" if cost_value is not None else '$—',
            'time_display': f"{time_value:.1f}s" if time_value is not None else '—',
            'size_display': format_html_size(html_output),
        })

    total_cost = sum(cost_values) if cost_values else 0.0
    max_cost = max(cost_values) if cost_values else None
    max_time = max(time_values) if time_values else None
    avg_time = (sum(time_values) / len(time_values)) if time_values else 0.0
    total_size_bytes = sum(size_values)

    summary_cost_label = f"${total_cost:.6f}"
    summary_time_label = f"{max_time:.1f} seconds" if max_time is not None else '0.0 seconds'
    summary_size_label = _format_kb_from_bytes(total_size_bytes)

    table_html_rows: List[str] = []
    highlight_cost = max_cost if max_cost is not None else None
    highlight_time = max_time if max_time is not None else None
    highlight_size = max(size_values) if size_values else None

    for row in data_rows:
        model_label = _html.escape(row['model'])
        price_display = _html.escape(row['price_display'])
        time_display = _html.escape(row['time_display'])
        size_display = _html.escape(row['size_display'])

        price_style = ''
        if highlight_cost is not None and row['price_value'] == highlight_cost:
            price_style = ' style="color:#ef4444;font-weight:600;"'

        time_style = ''
        if highlight_time is not None and row['time_value'] == highlight_time:
            time_style = ' style="color:#ef4444;font-weight:600;"'

        size_style = ''
        if highlight_size is not None and row['size_value'] == highlight_size:
            size_style = ' style="color:#ef4444;font-weight:600;"'

        table_html_rows.append(
            f"<tr>"
            f"<td class='summary-cell text-left'>{model_label}</td>"
            f"<td class='summary-cell text-right'{price_style}>{price_display}</td>"
            f"<td class='summary-cell text-right'{time_style}>{time_display}</td>"
            f"<td class='summary-cell text-right'{size_style}>{size_display}</td>"
            f"</tr>"
        )

    total_time_display = (
        f"max {max_time:.1f}s · avg {avg_time:.1f}s" if time_values else 'max 0.0s · avg 0.0s'
    )
    total_size_display = _format_kb_from_bytes(total_size_bytes)
    table_html_rows.append(
        "<tr class='summary-total-row'>"
        "<td class='summary-cell text-left font-semibold'>Total</td>"
        f"<td class='summary-cell text-right font-semibold'>{_html.escape(summary_cost_label)}</td>"
        f"<td class='summary-cell text-right font-semibold'>{_html.escape(total_time_display)}</td>"
        f"<td class='summary-cell text-right font-semibold'>{_html.escape(total_size_display)}</td>"
        "</tr>"
    )

    table_html = (
        "<style>"
        ".summary-table { width: 100%; border-collapse: collapse; }"
        ".summary-header th { text-align: left; font-weight: 600; padding: 8px 12px; font-size: 0.85rem; }"
        ".summary-cell { padding: 8px 12px; font-size: 0.85rem; border-top: 1px solid rgba(148, 163, 184, 0.3); }"
        ".summary-total-row { background: rgba(148, 163, 184, 0.08); }"
        ".text-right { text-align: right; }"
        ".text-left { text-align: left; }"
        ".font-semibold { font-weight: 600; }"
        ".summary-wrapper { max-height: 60vh; overflow-y: auto; }"
        "</style>"
        "<div class='summary-wrapper'>"
        "<table class='summary-table'>"
        "<thead class='summary-header'><tr>"
        "<th>Model</th><th class='text-right'>Price</th><th class='text-right'>Time</th><th class='text-right'>HTML Size</th>"
        "</tr></thead>"
        "<tbody>"
        + ''.join(table_html_rows)
        + "</tbody></table></div>"
    )

    summary_dialog = ui.dialog()
    summary_dialog.props('persistent')
    with summary_dialog:
        with ui.card().classes('w-[95vw] max-w-[900px] gap-3 p-4'):
            with ui.row().classes('items-center justify-between w-full'):
                ui.label('Iteration Summary').classes('text-lg font-semibold')
                ui.button(icon='close', on_click=summary_dialog.close).props('flat round dense')

            meta_parts = [f"Total {summary_cost_label}"]
            meta_parts.append(f"Max {summary_time_label}")
            meta_parts.append(f"Sum {summary_size_label}")
            meta_parts.append(f"Avg {avg_time:.1f} seconds")
            ui.label(' · '.join(meta_parts)).classes('text-sm text-gray-500 dark:text-gray-400')

            ui.html(table_html)

    button_label = '📊 Summary'
    disabled = not bool(data_rows)
    return summary_dialog, button_label, disabled
