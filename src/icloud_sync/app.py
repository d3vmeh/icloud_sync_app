"""icloud-sync — the NiceGUI control panel.

The GUI edits config, manages systemd units, and displays status. It never
owns a long-running sync: runs happen in icloud-sync-runner processes and are
observed through their state and log files.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import webbrowser

from nicegui import app, run, ui

from . import __version__, dialogs, systemd, theme, tray
from .cards import FolderCard
from .config import AppConfig, SyncFolder, load_config, save_config
from .state import read_state


class Panel:
    def __init__(self) -> None:
        self.config: AppConfig = load_config()

    def build(self) -> None:
        theme.apply()
        with ui.column().classes("w-full max-w-4xl mx-auto gap-4 p-6"):
            self._header()
            self._reconnect_banner()
            self._linger_hint()
            self.cards_box = ui.column().classes("w-full gap-4")
            self.rebuild_cards()

    def _header(self) -> None:
        with ui.row().classes("w-full items-center gap-3"):
            ui.icon("cloud_sync", size="2.2rem").classes("text-primary")
            with ui.column().classes("gap-0"):
                ui.label("iCloud Sync").classes("text-2xl font-semibold")
                ui.label(f"rclone control panel · v{__version__}") \
                    .classes("text-xs subtle")
            ui.space()
            ui.button("Add folder", on_click=self._add_folder) \
                .props("unelevated icon=add")

    def _reconnect_banner(self) -> None:
        with ui.row().classes(
            "w-full items-center gap-3 p-4 rounded-xl hidden"
        ).style("background:#3a1210; border:1px solid #7a2622") as self.banner:
            ui.icon("key_off", color="negative", size="1.6rem")
            self.banner_label = ui.label().classes("text-negative font-medium")
            ui.space()
            self._banner_target: SyncFolder | None = None
            ui.button("Reconnect…", on_click=self._banner_reconnect) \
                .props("unelevated color=negative icon=key")
        ui.timer(2.0, self._update_banner)

    def _banner_reconnect(self) -> None:
        if self._banner_target is not None:
            dialogs.reconnect_dialog(self._banner_target)

    def _update_banner(self) -> None:
        stale = [f for f in self.config.folders if read_state(f.id).needs_reconnect]
        if stale:
            names = ", ".join(f.name for f in stale)
            self.banner_label.text = (f"iCloud session expired — reconnect required "
                                      f"for: {names}")
            self._banner_target = stale[0]
            self.banner.classes(remove="hidden")
        else:
            self._banner_target = None
            self.banner.classes(add="hidden")

    def _linger_hint(self) -> None:
        if systemd.linger_enabled():
            return
        with ui.row().classes(
            "w-full items-center gap-3 p-4 rounded-xl"
        ).style("background:#1b2233; border:1px solid #2d3c5e") as row:
            ui.icon("bedtime", color="info", size="1.5rem")
            ui.label("Background syncs stop when you log out. Enable lingering "
                     "so scheduled syncs keep running.").classes("text-sm")
            ui.space()

            async def enable() -> None:
                ok, message = await run.io_bound(systemd.enable_linger)
                if ok:
                    ui.notify("Lingering enabled", type="positive")
                    row.set_visibility(False)
                else:
                    ui.notify(f"Failed to enable lingering: {message}", type="negative")

            ui.button("Enable lingering", on_click=enable).props("outline color=info")

    # -- folder CRUD ----------------------------------------------------------

    def rebuild_cards(self) -> None:
        self.cards_box.clear()
        with self.cards_box:
            if not self.config.folders:
                with ui.column().classes("w-full items-center gap-2 p-12"):
                    ui.icon("cloud_off", size="3rem").classes("subtle")
                    ui.label("No folders configured yet").classes("text-lg subtle")
                    ui.label("Add an iCloud folder to start syncing.") \
                        .classes("text-sm subtle")
                    ui.button("Add folder", on_click=self._add_folder) \
                        .props("unelevated icon=add").classes("mt-2")
                return
            for folder in self.config.folders:
                FolderCard(folder, on_edit=self._edit_folder,
                           on_delete=self._delete_folder)

    async def _add_folder(self) -> None:
        await dialogs.folder_dialog(None, self._save_folder)

    async def _edit_folder(self, folder: SyncFolder) -> None:
        await dialogs.folder_dialog(folder, self._save_folder)

    async def _save_folder(self, folder: SyncFolder) -> None:
        self.config.upsert(folder)
        save_config(self.config)
        self.rebuild_cards()
        tray.refresh_menu()
        ui.notify(f"Saved “{folder.name}”", type="positive")

    async def _delete_folder(self, folder: SyncFolder) -> None:
        async def confirmed() -> None:
            await run.io_bound(systemd.remove_folder_units, folder.id)
            self.config.remove(folder.id)
            save_config(self.config)
            self.rebuild_cards()
            tray.refresh_menu()
            ui.notify(f"Removed “{folder.name}”", type="info")

        dialogs.confirm_delete(folder, confirmed)


def _native_available() -> bool:
    return importlib.util.find_spec("webview") is not None and bool(
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="icloud-sync",
                                     description="iCloud Drive sync control panel")
    parser.add_argument("--browser", action="store_true",
                        help="open in the default browser instead of a native window")
    parser.add_argument("--port", type=int, default=8990)
    parser.add_argument("--no-show", action="store_true",
                        help="start the server without opening a window (debug)")
    args = parser.parse_args()

    Panel().build()

    native = _native_available() and not args.browser and not args.no_show
    _setup_tray(native=native, port=args.port)
    if native:
        ui.run(native=True, title="iCloud Sync", window_size=(980, 860),
               reload=False, dark=True)
    else:
        ui.run(title="iCloud Sync", port=args.port, show=not args.no_show,
               reload=False, dark=True)


def _setup_tray(*, native: bool, port: int) -> None:
    """Tray callbacks run in the tray's own thread — keep them thread-safe."""
    if native:
        def show() -> None:
            if app.native.main_window is not None:
                app.native.main_window.show()

        def hide() -> None:
            if app.native.main_window is not None:
                app.native.main_window.hide()
    else:
        def show() -> None:
            webbrowser.open(f"http://127.0.0.1:{port}/")

        hide = None

    def quit_app() -> None:
        tray.stop()
        app.shutdown()

    app.on_startup(lambda: tray.start(show, hide, quit_app))
    app.on_shutdown(tray.stop)


if __name__ == "__main__":
    main()
