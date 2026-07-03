"""Dark theme and shared styling for the control panel."""

from __future__ import annotations

from nicegui import ui

STATUS_COLORS = {
    "idle": "#8e8e93",
    "running": "#ffd60a",
    "success": "#30d158",
    "failed": "#ff453a",
    "reconnect": "#ff453a",
}

_CSS = """
<style>
  body.body--dark {
    background: #0e0e12;
    font-family: 'Inter', -apple-system, 'Segoe UI', Roboto, sans-serif;
  }
  .q-card {
    background: #17171c !important;
    border: 1px solid #26262e;
    border-radius: 14px;
    box-shadow: 0 1px 2px rgba(0,0,0,.4);
  }
  .folder-card { transition: border-color .3s ease; }
  .folder-card.card-running { border-color: #b8960b; }
  .folder-card.card-failed { border-color: #7a2622; }
  .status-dot {
    width: 10px; height: 10px; border-radius: 50%;
    display: inline-block; transition: background-color .4s ease;
  }
  .status-dot.pulse { animation: dotpulse 1.4s ease-in-out infinite; }
  @keyframes dotpulse { 50% { opacity: .35; } }
  .mono-log .q-log__line { font-size: 11px; line-height: 1.5; }
  .subtle { color: #8e8e93; }
  .path-caption {
    color: #8e8e93; font-size: 12px;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
  }
</style>
"""


def apply() -> None:
    ui.dark_mode().enable()
    ui.colors(
        primary="#0a84ff",
        secondary="#5e5ce6",
        positive="#30d158",
        negative="#ff453a",
        warning="#ffd60a",
        info="#64d2ff",
        dark="#17171c",
    )
    ui.add_head_html(_CSS)
