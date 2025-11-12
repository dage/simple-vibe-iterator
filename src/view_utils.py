from __future__ import annotations

from .interfaces import TransitionArtifacts


def format_html_size(html: str) -> str:
    """Return size of HTML in kilobytes with two decimal places."""
    size_kb = len((html or "").encode("utf-8")) / 1024
    return f"{size_kb:.2f} KB"


def extract_vision_summary(artifacts: TransitionArtifacts | None) -> str:
    """Return the vision summary text, falling back to analysis metadata when needed."""
    if artifacts is None:
        return ""
    raw = artifacts.vision_output or ""
    if not isinstance(raw, str):
        try:
            raw = str(raw or "")
        except Exception:
            raw = ""
    if (raw or "").strip():
        return raw
    analysis = getattr(artifacts, "analysis", {})
    if isinstance(analysis, dict):
        candidate = analysis.get("vision_summary", "")
        if candidate:
            try:
                return str(candidate)
            except Exception:
                pass
    return ""
