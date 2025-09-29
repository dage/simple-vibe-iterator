from __future__ import annotations


def format_html_size(html: str) -> str:
    """Return size of HTML in kilobytes with two decimal places."""
    size_kb = len((html or "").encode("utf-8")) / 1024
    return f"{size_kb:.2f} KB"
