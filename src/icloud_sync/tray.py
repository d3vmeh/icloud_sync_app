"""System-tray icon: show/hide the window and per-folder quick Pull/Push.

Tray support varies wildly across Linux desktops (GNOME needs the
AppIndicator extension), so every step degrades gracefully — if no tray is
available the app simply runs without one. Quick actions spawn the same
icloud-sync-runner used everywhere else, so they show up in the panel's
live status like any other run.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable

from . import systemd
from .config import load_config

_icon = None  # module singleton, set by start()


def _make_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    blue = (10, 132, 255, 255)
    # simple cloud silhouette
    draw.ellipse((6, 26, 32, 52), fill=blue)
    draw.ellipse((18, 12, 48, 42), fill=blue)
    draw.ellipse((36, 24, 58, 46), fill=blue)
    draw.rectangle((16, 38, 48, 52), fill=blue)
    return img


def _quick_sync(folder_id: str, action: str) -> None:
    try:
        subprocess.Popen(
            [systemd.runner_executable(), folder_id, "--action", action],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:  # noqa: BLE001 — tray thread must never crash the app
        pass


def _build_menu(pystray, on_show: Callable[[], None], on_hide: Callable[[], None] | None,
                on_quit: Callable[[], None]):
    def items():
        yield pystray.MenuItem("Show window", lambda: on_show(), default=True)
        if on_hide is not None:
            yield pystray.MenuItem("Hide window", lambda: on_hide())
        yield pystray.Menu.SEPARATOR
        for folder in load_config().folders:
            yield pystray.MenuItem(folder.name, pystray.Menu(
                pystray.MenuItem("Pull  (iCloud → local)",
                                 lambda _i, f=folder: _quick_sync(f.id, "pull")),
                pystray.MenuItem("Push  (local → iCloud)",
                                 lambda _i, f=folder: _quick_sync(f.id, "push")),
            ))
        if load_config().folders:
            yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Quit", lambda: on_quit())

    return pystray.Menu(items)


def start(on_show: Callable[[], None], on_hide: Callable[[], None] | None,
          on_quit: Callable[[], None]) -> bool:
    """Start the tray icon in a background thread. Returns False if unavailable."""
    global _icon
    try:
        import pystray
    except Exception:  # noqa: BLE001
        return False

    try:
        _icon = pystray.Icon("icloud-sync", _make_image(), "iCloud Sync",
                             menu=_build_menu(pystray, on_show, on_hide, on_quit))
    except Exception:  # noqa: BLE001
        return False

    def run() -> None:
        try:
            _icon.run()
        except Exception:  # noqa: BLE001 — no tray on this desktop; carry on without one
            pass

    threading.Thread(target=run, name="tray", daemon=True).start()
    return True


def refresh_menu() -> None:
    """Re-render the folder list after config changes."""
    if _icon is not None:
        try:
            _icon.update_menu()
        except Exception:  # noqa: BLE001
            pass


def stop() -> None:
    global _icon
    if _icon is not None:
        try:
            _icon.stop()
        except Exception:  # noqa: BLE001
            pass
        _icon = None
