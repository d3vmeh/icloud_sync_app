"""Per-folder runner state, written by the runner and read by the GUI.

The GUI never holds a live handle to a sync process — this file (plus the log
file and `systemctl --user is-active`) is the single source of truth.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any

from . import paths


@dataclass
class Progress:
    bytes_done: int = 0
    bytes_total: int = 0
    speed: float = 0.0
    eta: int | None = None
    transferring: int = 0

    @property
    def percent(self) -> float:
        if self.bytes_total <= 0:
            return 0.0
        return min(1.0, self.bytes_done / self.bytes_total)


@dataclass
class FolderState:
    running: bool = False
    pid: int | None = None
    action: str | None = None
    last_run: str | None = None
    exit_code: int | None = None
    last_error: str | None = None
    needs_reconnect: bool = False
    progress: Progress | None = None
    filters_sig: str | None = None  # excludes in effect at the last successful run

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FolderState:
        progress = data.pop("progress", None)
        known = {f for f in cls.__dataclass_fields__}
        state = cls(**{k: v for k, v in data.items() if k in known and k != "progress"})
        if isinstance(progress, dict):
            pknown = {f for f in Progress.__dataclass_fields__}
            state.progress = Progress(**{k: v for k, v in progress.items() if k in pknown})
        return state


def read_state(folder_id: str) -> FolderState:
    path = paths.state_file(folder_id)
    if not path.exists():
        return FolderState()
    try:
        state = FolderState.from_dict(json.loads(path.read_text()))
    except (json.JSONDecodeError, TypeError, ValueError):
        return FolderState()
    # A stale "running" flag (crash, power loss) must not wedge the UI.
    if state.running and not _pid_alive(state.pid):
        state.running = False
        state.pid = None
    return state


def write_state(folder_id: str, state: FolderState) -> None:
    paths.ensure_dirs()
    path = paths.state_file(folder_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2) + "\n")
    tmp.replace(path)


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True
