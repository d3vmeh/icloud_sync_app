"""Desktop launcher installation: app icon + .desktop entry for the dock."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .appicon import make_image

DESKTOP_ID = "icloud-sync"


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser()


def gui_executable() -> str:
    candidate = Path(sys.executable).parent / "icloud-sync"
    if candidate.is_file():
        return str(candidate)
    found = shutil.which("icloud-sync")
    if found:
        return found
    raise FileNotFoundError("icloud-sync not found; is the package installed?")


def install_desktop_entry() -> Path:
    """Write the icon and .desktop file; returns the .desktop path."""
    icon_path = _data_home() / "icons" / "hicolor" / "256x256" / "apps" / f"{DESKTOP_ID}.png"
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    make_image(256, tile=True).save(icon_path)

    desktop_path = _data_home() / "applications" / f"{DESKTOP_ID}.desktop"
    desktop_path.parent.mkdir(parents=True, exist_ok=True)
    desktop_path.write_text(f"""\
[Desktop Entry]
Type=Application
Name=iCloud Sync
Comment=Sync iCloud Drive folders via rclone
Exec={gui_executable()}
Icon={DESKTOP_ID}
Terminal=false
Categories=Utility;FileTools;
Keywords=icloud;rclone;sync;backup;
StartupNotify=true
StartupWMClass={DESKTOP_ID}
""")
    _refresh_caches()
    return desktop_path


def _refresh_caches() -> None:
    """Nudge the desktop DB and icon cache so a freshly installed launcher and
    icon are picked up without a re-login. Best-effort — missing tools are fine."""
    apps_dir = _data_home() / "applications"
    icon_theme = _data_home() / "icons" / "hicolor"
    for cmd in (["update-desktop-database", str(apps_dir)],
                ["gtk-update-icon-cache", "-f", "-t", str(icon_theme)]):
        exe = shutil.which(cmd[0])
        if exe:
            subprocess.run([exe, *cmd[1:]], capture_output=True)


def uninstall_desktop_entry() -> None:
    (_data_home() / "applications" / f"{DESKTOP_ID}.desktop").unlink(missing_ok=True)
    (_data_home() / "icons" / "hicolor" / "256x256" / "apps" / f"{DESKTOP_ID}.png") \
        .unlink(missing_ok=True)
