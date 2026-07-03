"""icloud-sync-runner — headless per-folder sync, invoked by systemd or the GUI.

Owns the rclone process for exactly one folder:
- takes a non-blocking flock so overlapping runs (timer + manual) exit early,
- appends rclone output to ~/.local/state/icloud-sync/<id>.log,
- keeps ~/.local/state/icloud-sync/<id>.json updated with progress and outcome.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from . import paths, rclone
from .config import SyncFolder, load_config
from .state import FolderState, read_state, write_state

EXIT_LOCKED = 0  # another run in progress is normal, not a failure
EXIT_USAGE = 2

CANCELLED = "cancelled"  # sentinel in state.last_error for user-cancelled runs

# rclone bisync requires --resync on the very first run; detect its listing dir.
_BISYNC_WORKDIR = Path("~/.cache/rclone/bisync").expanduser()


def _timestamp() -> str:
    return datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _filters_sig(folder: SyncFolder) -> str:
    return "\n".join(folder.excludes)


def _effective_action(folder: SyncFolder, action: str, state: FolderState,
                      log: IO[str]) -> str:
    """bisync needs --resync on the first run of a pair and whenever the
    filter set changed since the last successful run (rclone can't detect
    inline --exclude changes itself and would report false deletions)."""
    if action != "bisync":
        return action
    if not _bisync_initialized(folder):
        log.write(f"[{_timestamp()}] first bisync for this pair — using --resync\n")
        return "bisync-resync"
    if state.filters_sig is not None and state.filters_sig != _filters_sig(folder):
        log.write(f"[{_timestamp()}] exclude patterns changed — using --resync\n")
        return "bisync-resync"
    return action


def _bisync_initialized(folder: SyncFolder) -> bool:
    if not _BISYNC_WORKDIR.is_dir():
        return False
    # rclone names listing files after both sync ends; match on the folder id-agnostic
    # local path component, which is stable across runs.
    token = str(folder.local_target).strip("/").replace("/", "_")
    return any(token in p.name for p in _BISYNC_WORKDIR.iterdir())


def _ensure_check_access_markers(folder: SyncFolder, log: IO[str]) -> None:
    """--check-access aborts unless RCLONE_TEST exists on both sides."""
    local_marker = folder.local_target / rclone.CHECK_ACCESS_MARKER
    if not local_marker.exists():
        local_marker.parent.mkdir(parents=True, exist_ok=True)
        local_marker.touch()
        log.write(f"[{_timestamp()}] created local {rclone.CHECK_ACCESS_MARKER} marker\n")
    remote_marker = f"{folder.remote_full}/{rclone.CHECK_ACCESS_MARKER}"
    probe = subprocess.run(
        ["rclone", "lsf", remote_marker], capture_output=True, text=True
    )
    if probe.returncode != 0 or not probe.stdout.strip():
        subprocess.run(["rclone", "touch", remote_marker], capture_output=True)
        log.write(f"[{_timestamp()}] created remote {rclone.CHECK_ACCESS_MARKER} marker\n")


def run_sync(folder: SyncFolder, action: str, *, dry_run: bool = False) -> int:
    paths.ensure_dirs()

    lock_fd = os.open(paths.lock_file(folder.id), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"{folder.id}: sync already running, exiting", file=sys.stderr)
        os.close(lock_fd)
        return EXIT_LOCKED

    try:
        with open(paths.log_file(folder.id), "a", buffering=1) as log:
            return _run_locked(folder, action, dry_run, log)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _run_locked(folder: SyncFolder, action: str, dry_run: bool, log: IO[str]) -> int:
    action = _effective_action(folder, action, read_state(folder.id), log)
    if folder.check_access and action.startswith("bisync") and not dry_run:
        _ensure_check_access_markers(folder, log)

    cmd = rclone.build_command(folder, action, dry_run=dry_run)
    log.write(f"[{_timestamp()}] $ {' '.join(cmd)}\n")

    state = read_state(folder.id)
    state.running = True
    state.pid = os.getpid()
    state.action = action
    state.exit_code = None
    state.last_error = None
    state.progress = None
    write_state(folder.id, state)

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            errors="replace",
        )
    except FileNotFoundError:
        log.write(f"[{_timestamp()}] ERROR: rclone not found on PATH\n")
        _finalize(folder.id, state, 127, "rclone not found on PATH", auth_error=False)
        return 127

    # Forward SIGTERM/SIGINT (GUI cancel, systemctl stop) to rclone so it can
    # shut down cleanly; we still fall through to _finalize afterwards.
    # No file I/O here: a handler firing mid-write would make a reentrant
    # call into the buffered log writer. Log it after the read loop instead.
    signals_received: list[int] = []

    def _forward(signum: int, _frame: object) -> None:
        signals_received.append(signum)
        proc.terminate()

    signal.signal(signal.SIGTERM, _forward)
    signal.signal(signal.SIGINT, _forward)

    auth_error = False
    last_error: str | None = None
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if not line:
            continue
        log.write(line + "\n")
        entry = rclone.parse_line(line)
        if entry is None:
            continue
        progress = rclone.extract_progress(entry)
        if progress is not None:
            state.progress = progress
            write_state(folder.id, state)
        if entry.get("level") == "error" or "failed" in str(entry.get("msg", "")).lower():
            last_error = str(entry.get("msg", ""))[:500]
        if rclone.entry_is_auth_error(entry):
            auth_error = True

    exit_code = proc.wait()
    for signum in signals_received:
        log.write(f"[{_timestamp()}] received signal {signum}, terminated rclone\n")
    log.write(f"[{_timestamp()}] exit code {exit_code}\n")
    if signals_received:
        # A cancel is not a failure — and definitely not an auth problem.
        last_error, auth_error = CANCELLED, False
    _finalize(folder.id, state, exit_code, last_error, auth_error=auth_error,
              filters_sig=_filters_sig(folder))
    return exit_code


def _finalize(folder_id: str, state: FolderState, exit_code: int,
              last_error: str | None, *, auth_error: bool,
              filters_sig: str | None = None) -> None:
    state.running = False
    state.pid = None
    state.last_run = datetime.now(UTC).isoformat()
    state.exit_code = exit_code
    state.last_error = last_error if exit_code != 0 else None
    if exit_code == 0:
        state.needs_reconnect = False
        state.filters_sig = filters_sig
    elif auth_error:
        state.needs_reconnect = True
    write_state(folder_id, state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="icloud-sync-runner",
        description="Headless single-folder sync runner (used by systemd and the GUI).",
    )
    parser.add_argument("folder_id", help="folder id from config.json")
    parser.add_argument("--action", choices=rclone.ACTIONS, default=None,
                        help="override the folder's configured mode")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    folder = load_config().get(args.folder_id)
    if folder is None:
        print(f"unknown folder id: {args.folder_id!r} "
              f"(check {paths.config_file()})", file=sys.stderr)
        return EXIT_USAGE

    return run_sync(folder, args.action or folder.mode, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
