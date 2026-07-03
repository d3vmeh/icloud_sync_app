"""One card per sync pair: controls, live progress, schedule, log tail.

The card never owns a sync process. Manual actions spawn the same
icloud-sync-runner binary systemd uses; all status shown here is
reconstructed each second from the folder's state file and log file.
"""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable
from datetime import datetime

from nicegui import run, ui

from . import dialogs, paths, rclone, systemd, theme
from .config import SyncFolder
from .state import FolderState, read_state

_TAIL_INITIAL_BYTES = 8_192


class FolderCard:
    def __init__(self, folder: SyncFolder,
                 on_edit: Callable[[SyncFolder], Awaitable[None]],
                 on_delete: Callable[[SyncFolder], Awaitable[None]]) -> None:
        self.folder = folder
        self.on_edit = on_edit
        self.on_delete = on_delete
        self._log_offset = 0
        self._starting = False  # runner spawned but state file not yet updated
        self._build()
        ui.timer(1.0, self._refresh)

    # -- layout ---------------------------------------------------------------

    def _build(self) -> None:
        f = self.folder
        with ui.card().classes("w-full folder-card") as self.card:
            with ui.row().classes("w-full items-center gap-3"):
                self.dot = ui.html('<span class="status-dot"></span>')
                ui.label(f.name).classes("text-base font-semibold")
                ui.badge(f.mode).props("outline color=grey-6")
                self.status_label = ui.label("Idle").classes("text-sm subtle")
                ui.space()
                self.reconnect_chip = ui.button(
                    "Reconnect required", on_click=lambda: dialogs.reconnect_dialog(f),
                ).props("dense unelevated color=negative icon=key size=sm")
                self.reconnect_chip.set_visibility(False)
                with ui.button(icon="more_vert").props("flat dense round color=grey-6"):
                    with ui.menu():
                        ui.menu_item("Edit", lambda: self.on_edit(f))
                        ui.menu_item("Bisync --resync (first run)",
                                     lambda: self._start("bisync-resync"))
                        ui.menu_item("systemd status", self._show_unit_status)
                        ui.separator()
                        ui.menu_item("Delete", lambda: self.on_delete(f)) \
                            .classes("text-negative")

            ui.label(f"{f.remote_full}  ⇄  {f.local_path}").classes("path-caption")

            # Progress (visible while a run is active)
            with ui.column().classes("w-full gap-0") as self.progress_box:
                self.progress = ui.linear_progress(value=0.0, show_value=False) \
                    .classes("w-full").props("rounded size=8px color=warning")
                with ui.row().classes("w-full justify-between"):
                    self.progress_pct = ui.label("").classes("text-xs subtle")
                    self.progress_info = ui.label("").classes("text-xs subtle")
            self.progress_box.set_visibility(False)

            with ui.row().classes("w-full items-center gap-2"):
                self.dry = ui.switch("Dry run").props("dense")
                ui.space()
                self.btn_pull = ui.button("Pull", on_click=lambda: self._start("pull")) \
                    .props("unelevated dense icon=download")
                self.btn_push = ui.button("Push", on_click=lambda: self._start("push")) \
                    .props("unelevated dense icon=upload")
                self.btn_bisync = ui.button("Bisync", on_click=lambda: self._start("bisync")) \
                    .props("unelevated dense icon=sync color=secondary")
                self.btn_cancel = ui.button("Cancel", on_click=self._cancel) \
                    .props("outline dense icon=stop color=negative")
                self.btn_cancel.set_visibility(False)

            with ui.row().classes("w-full items-center gap-4"):
                self.startup_switch = ui.switch(
                    "Sync on startup", value=f.sync_on_startup,
                    on_change=self._toggle_startup,
                ).props("dense")
                with ui.row().classes("items-center gap-2"):
                    self.timer_switch = ui.switch(
                        "Sync every", value=bool(f.interval_minutes),
                        on_change=self._toggle_timer,
                    ).props("dense")
                    self.interval_input = ui.number(
                        value=f.interval_minutes or 30, min=1, max=1440, step=5,
                        on_change=self._toggle_timer,
                    ).props("dense outlined suffix=min").classes("w-28")

            with ui.expansion("Log", icon="terminal").classes("w-full"):
                self.log = ui.log(max_lines=1000).classes("w-full h-48 mono-log")

        self._action_buttons = [self.btn_pull, self.btn_push, self.btn_bisync]

    # -- actions --------------------------------------------------------------

    async def _start(self, action: str) -> None:
        if read_state(self.folder.id).running or self._starting:
            ui.notify("A sync for this folder is already running", type="warning")
            return
        self._starting = True
        self._set_buttons_enabled(False)
        cmd = [systemd.runner_executable(), self.folder.id, "--action", action]
        if self.dry.value:
            cmd.append("--dry-run")
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )

        async def clear_starting() -> None:
            await proc.wait()
            self._starting = False

        asyncio.get_running_loop().create_task(clear_starting())
        label = f"{action} (dry run)" if self.dry.value else action
        ui.notify(f"{self.folder.name}: {label} started", type="info")

    def _cancel(self) -> None:
        folder_state = read_state(self.folder.id)
        if folder_state.pid:
            try:
                os.kill(folder_state.pid, signal.SIGTERM)
                ui.notify(f"{self.folder.name}: cancel requested", type="warning")
            except ProcessLookupError:
                pass

    async def _toggle_startup(self) -> None:
        enabled = bool(self.startup_switch.value)
        await run.io_bound(systemd.set_startup_sync, self.folder.id, enabled)
        self.folder.sync_on_startup = enabled
        await self._persist()

    async def _toggle_timer(self) -> None:
        minutes = int(self.interval_input.value or 0) if self.timer_switch.value else None
        await run.io_bound(systemd.set_timer, self.folder.id, minutes)
        self.folder.interval_minutes = minutes
        await self._persist()

    async def _persist(self) -> None:
        from .config import load_config, save_config
        config = load_config()
        config.upsert(self.folder)
        save_config(config)

    async def _show_unit_status(self) -> None:
        text = await run.io_bound(systemd.unit_status_text, self.folder.id)
        with ui.dialog() as dialog, ui.card().classes("w-[640px] max-w-full"):
            ui.label("systemd status").classes("text-lg font-semibold")
            ui.code(text or "no unit state").classes("w-full text-xs")
            ui.button("Close", on_click=dialog.close).props("flat")
        dialog.open()

    # -- live refresh ---------------------------------------------------------

    def _refresh(self) -> None:
        folder_state = read_state(self.folder.id)
        running = folder_state.running or self._starting
        self._apply_status(folder_state, running)
        self._apply_progress(folder_state, running)
        self._set_buttons_enabled(not running)
        self.btn_cancel.set_visibility(bool(folder_state.running))
        self._tail_log()

    def _apply_status(self, folder_state: FolderState, running: bool) -> None:
        self.reconnect_chip.set_visibility(folder_state.needs_reconnect)
        if running:
            action = folder_state.action or "sync"
            self._set_dot("running", pulse=True)
            self.status_label.text = f"Running {action}…"
            self.card.classes(add="card-running", remove="card-failed")
        elif folder_state.needs_reconnect:
            self._set_dot("reconnect")
            self.status_label.text = "Auth expired"
            self.card.classes(add="card-failed", remove="card-running")
        elif folder_state.exit_code == 0:
            self._set_dot("success")
            self.status_label.text = f"Success · {_ago(folder_state.last_run)}"
            self.card.classes(remove="card-running card-failed")
        elif folder_state.exit_code is not None:
            self._set_dot("failed")
            self.status_label.text = (f"Failed (exit {folder_state.exit_code}) · "
                                      f"{_ago(folder_state.last_run)}")
            self.card.classes(add="card-failed", remove="card-running")
        else:
            self._set_dot("idle")
            self.status_label.text = "Idle"
            self.card.classes(remove="card-running card-failed")

    def _apply_progress(self, folder_state: FolderState, running: bool) -> None:
        progress = folder_state.progress
        show = running and progress is not None
        self.progress_box.set_visibility(show)
        if not show:
            return
        self.progress.value = round(progress.percent, 3)
        self.progress_pct.text = f"{progress.percent * 100:.0f}%"
        self.progress_info.text = (f"{rclone.format_speed(progress.speed)} · "
                                   f"ETA {rclone.format_eta(progress.eta)} · "
                                   f"{progress.transferring} transferring")

    def _set_dot(self, status: str, *, pulse: bool = False) -> None:
        color = theme.STATUS_COLORS[status]
        pulse_cls = " pulse" if pulse else ""
        self.dot.content = (f'<span class="status-dot{pulse_cls}" '
                            f'style="background:{color}"></span>')

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for button in self._action_buttons:
            button.set_enabled(enabled)

    def _tail_log(self) -> None:
        path = paths.log_file(self.folder.id)
        if not path.exists():
            return
        size = path.stat().st_size
        if size < self._log_offset:
            self._log_offset = 0  # log was truncated/rotated
        if size == self._log_offset:
            return
        with open(path, errors="replace") as f:
            if self._log_offset == 0 and size > _TAIL_INITIAL_BYTES:
                f.seek(size - _TAIL_INITIAL_BYTES)
                f.readline()  # skip a likely-partial line
            else:
                f.seek(self._log_offset)
            chunk = f.read()
            self._log_offset = f.tell()
        for line in chunk.splitlines():
            self.log.push(line)


def _ago(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    seconds = (datetime.now(then.tzinfo) - then).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"
