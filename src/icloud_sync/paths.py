"""XDG directory layout. All runtime data lives outside the repo."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "icloud-sync"


def config_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return base / APP_NAME


def state_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    return base / APP_NAME


def systemd_user_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return base / "systemd" / "user"


def config_file() -> Path:
    return config_dir() / "config.json"


def log_file(folder_id: str) -> Path:
    return state_dir() / f"{folder_id}.log"


def state_file(folder_id: str) -> Path:
    return state_dir() / f"{folder_id}.json"


def lock_file(folder_id: str) -> Path:
    return state_dir() / f"{folder_id}.lock"


def ensure_dirs() -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    state_dir().mkdir(parents=True, exist_ok=True)
