"""rclone command construction and JSON-log parsing."""

from __future__ import annotations

import json
import re
from typing import Any

from .config import SyncFolder
from .state import Progress

CHECK_ACCESS_MARKER = "RCLONE_TEST"

# rclone emits one JSON object per line with --use-json-log; stats lines carry
# a "stats" object when run with --stats-log-level NOTICE.
STATS_FLAGS = ["--use-json-log", "--stats", "1s", "--stats-log-level", "NOTICE"]

ACTIONS = ("pull", "push", "bisync", "bisync-resync")

# Signatures of an expired/invalid iCloud session (trust_token lapses ~monthly).
_AUTH_ERROR_RE = re.compile(
    r"\b401\b|unauthenticated|unauthorized|authentication|invalid.grant|"
    r"trust.?token|2fa|two.factor|login (?:failed|required)|couldn'?t login|"
    r"oauth|token (?:expired|has expired|invalid)",
    re.IGNORECASE,
)

_ERROR_LEVELS = ("error", "critical", "fatal")


def build_command(folder: SyncFolder, action: str, *, dry_run: bool = False) -> list[str]:
    remote = folder.remote_full
    local = str(folder.local_target)

    if action == "pull":
        cmd = ["rclone", "copy", remote, local]
    elif action == "push":
        cmd = ["rclone", "copy", local, remote]
    elif action in ("bisync", "bisync-resync"):
        cmd = ["rclone", "bisync", remote, local, "--conflict-resolve", "newer"]
        if folder.check_access:
            cmd.append("--check-access")
        if action == "bisync-resync":
            cmd.append("--resync")
    else:
        raise ValueError(f"unknown action: {action}")

    for pattern in folder.excludes:
        cmd += ["--exclude", pattern]
    cmd += STATS_FLAGS
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def parse_line(raw: str) -> dict[str, Any] | None:
    """Parse one rclone --use-json-log line; None if it isn't JSON."""
    raw = raw.strip()
    if not raw.startswith("{"):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_progress(entry: dict[str, Any]) -> Progress | None:
    stats = entry.get("stats")
    if not isinstance(stats, dict):
        return None
    eta = stats.get("eta")
    transferring = stats.get("transferring") or []
    current_file = None
    if transferring and isinstance(transferring[0], dict):
        current_file = transferring[0].get("name")
    return Progress(
        bytes_done=int(stats.get("bytes") or 0),
        bytes_total=int(stats.get("totalBytes") or 0),
        speed=float(stats.get("speed") or 0.0),
        eta=int(eta) if eta is not None else None,
        transferring=len(transferring),
        current_file=current_file,
    )


def is_auth_error(text: str) -> bool:
    return bool(_AUTH_ERROR_RE.search(text))


def entry_is_auth_error(entry: dict[str, Any]) -> bool:
    """Auth signatures only count in error-level messages — stats lines are
    full of numbers that can contain e.g. a literal 401."""
    if entry.get("level") not in _ERROR_LEVELS:
        return False
    return is_auth_error(str(entry.get("msg", "")))


def format_speed(bytes_per_s: float) -> str:
    for unit in ("B/s", "KiB/s", "MiB/s", "GiB/s"):
        if bytes_per_s < 1024 or unit == "GiB/s":
            return f"{bytes_per_s:.1f} {unit}"
        bytes_per_s /= 1024
    return f"{bytes_per_s:.1f} GiB/s"


def format_eta(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
