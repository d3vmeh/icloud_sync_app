"""Dialogs: add/edit folder, delete confirmation, and the 2FA reconnect flow."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from nicegui import ui

from .config import SyncFolder, slugify
from .state import read_state, write_state

MODES = {"pull": "Pull (iCloud → local)", "push": "Push (local → iCloud)",
         "bisync": "Bisync (two-way)"}


async def list_remotes() -> list[str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "rclone", "listremotes",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
    except FileNotFoundError:
        return []
    return [line.strip().rstrip(":") for line in out.decode().splitlines() if line.strip()]


async def folder_dialog(folder: SyncFolder | None,
                        on_save: Callable[[SyncFolder], Awaitable[None]]) -> None:
    """Add (folder=None) or edit a sync pair."""
    remotes = await list_remotes()
    is_new = folder is None

    with ui.dialog() as dialog, ui.card().classes("w-[560px] max-w-full gap-1"):
        ui.label("Add folder" if is_new else f"Edit “{folder.name}”") \
            .classes("text-lg font-semibold")

        name = ui.input("Display name", value="" if is_new else folder.name) \
            .classes("w-full").props("autofocus")
        with ui.row().classes("w-full gap-3 items-end"):
            if remotes:
                remote = ui.select(remotes, label="rclone remote",
                                   value=(folder.remote if not is_new and folder.remote in remotes
                                          else remotes[0])).classes("flex-1")
            else:
                remote = ui.input("rclone remote name",
                                  value="" if is_new else folder.remote).classes("flex-1")
            mode = ui.select(MODES, label="Mode",
                             value="bisync" if is_new else folder.mode).classes("flex-1")
        remote_path = ui.input("Remote path", placeholder="Documents/MyFolder",
                               value="" if is_new else folder.remote_path).classes("w-full")
        local_path = ui.input("Local path", placeholder="~/my-folder",
                              value="" if is_new else folder.local_path).classes("w-full")
        keep_parent = ui.switch(
            "Keep the remote folder name locally",
            value=True if is_new else folder.keep_parent,
        ).props("dense")
        target_hint = ui.label().classes("path-caption")

        def update_hint() -> None:
            base = (local_path.value or "~/…").rstrip("/")
            name = (remote_path.value or "").rstrip("/").rsplit("/", 1)[-1]
            target = f"{base}/{name}" if keep_parent.value and name else base
            target_hint.text = f"Files will sync to: {target}"

        for element in (remote_path, local_path, keep_parent):
            element.on_value_change(update_hint)
        update_hint()

        excludes = ui.textarea(
            "Exclude patterns (one per line)",
            value="" if is_new else "\n".join(folder.excludes),
            placeholder="node_modules/**\n.git/**\n*.tmp",
        ).props("outlined autogrow dense").classes("w-full")
        ui.label("rclone filter syntax (rclone.org/filtering). Changing patterns on "
                 "a bisync pair automatically triggers --resync on its next run.") \
            .classes("subtle text-xs")
        check_access = ui.switch(
            "Bisync safety markers (--check-access with RCLONE_TEST files)",
            value=False if is_new else folder.check_access,
        ).props("dense").classes("mt-1")
        error = ui.label().classes("text-negative text-sm")

        async def save() -> None:
            if not all([name.value.strip(), str(remote.value or "").strip(),
                        remote_path.value.strip(), local_path.value.strip()]):
                error.text = "All fields are required."
                return
            result = SyncFolder(
                id=slugify(name.value) if is_new else folder.id,
                name=name.value.strip(),
                remote=str(remote.value).strip().rstrip(":"),
                remote_path=remote_path.value.strip().strip("/"),
                local_path=local_path.value.strip(),
                mode=mode.value,
                sync_on_startup=False if is_new else folder.sync_on_startup,
                interval_minutes=None if is_new else folder.interval_minutes,
                check_access=bool(check_access.value),
                keep_parent=bool(keep_parent.value),
                excludes=[line.strip() for line in excludes.value.splitlines()
                          if line.strip()],
            )
            dialog.close()
            await on_save(result)

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=save).props("unelevated")

    dialog.open()


def confirm_delete(folder: SyncFolder,
                   on_confirm: Callable[[], Awaitable[None]]) -> None:
    with ui.dialog() as dialog, ui.card().classes("gap-2"):
        ui.label(f"Remove “{folder.name}”?").classes("text-lg font-semibold")
        ui.label("Only the sync configuration and its schedule are removed — "
                 "no files are deleted on either side.").classes("subtle text-sm")

        async def confirm() -> None:
            dialog.close()
            await on_confirm()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Remove", on_click=confirm).props("unelevated color=negative")
    dialog.open()


def reconnect_dialog(folder: SyncFolder) -> None:
    """Interactive `rclone config reconnect` with the 2FA code piped to stdin."""
    proc: asyncio.subprocess.Process | None = None

    with ui.dialog() as dialog, ui.card().classes("w-[640px] max-w-full gap-2"):
        ui.label(f"Reconnect “{folder.remote}”").classes("text-lg font-semibold")
        ui.label("Apple expires the iCloud session roughly monthly. This runs "
                 f"`rclone config reconnect {folder.remote}:` — answer its prompts "
                 "below (usually “y”, then the 6-digit code from your Apple device).") \
            .classes("subtle text-sm")
        output = ui.log(max_lines=400).classes("w-full h-56 mono-log")

        with ui.row().classes("w-full gap-2 items-center"):
            answer = ui.input(placeholder="y / 2FA code…").classes("flex-1") \
                .props("dense outlined")
            send_btn = ui.button("Send").props("unelevated")
        status = ui.label().classes("text-sm")

        async def send() -> None:
            if proc is None or proc.stdin is None:
                return
            text = answer.value.strip()
            if not text:
                return
            output.push(f"> {text}")
            proc.stdin.write((text + "\n").encode())
            await proc.stdin.drain()
            answer.value = ""

        send_btn.on_click(send)
        answer.on("keydown.enter", send)

        async def run() -> None:
            nonlocal proc
            try:
                proc = await asyncio.create_subprocess_exec(
                    "rclone", "config", "reconnect", f"{folder.remote}:",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            except FileNotFoundError:
                output.push("ERROR: rclone not found on PATH")
                return
            assert proc.stdout is not None
            # Prompts don't end in newlines — stream chunks, not lines.
            while chunk := await proc.stdout.read(512):
                for line in chunk.decode(errors="replace").splitlines():
                    if line.strip():
                        output.push(line)
            code = await proc.wait()
            if code == 0:
                status.text = "Reconnected successfully."
                status.classes("text-positive")
                folder_state = read_state(folder.id)
                folder_state.needs_reconnect = False
                write_state(folder.id, folder_state)
            else:
                status.text = f"rclone exited with code {code} — try again."
                status.classes("text-negative")

        def cleanup() -> None:
            if proc is not None and proc.returncode is None:
                proc.terminate()

        dialog.on("hide", cleanup)
        with ui.row().classes("w-full justify-end"):
            ui.button("Close", on_click=dialog.close).props("flat")

        asyncio.get_running_loop().create_task(run())

    dialog.open()
