"""systemd --user integration: template service, per-folder timers, linger.

Unit files are generated here and managed via `systemctl --user`; they are
never hand-edited. One template service `icloud-sync@.service` runs
`icloud-sync-runner %i`; per-folder timers `icloud-sync@<id>.timer` override
the template with their own interval.
"""

from __future__ import annotations

import getpass
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths

SERVICE_TEMPLATE_NAME = "icloud-sync@.service"


def runner_executable() -> str:
    """Absolute path to icloud-sync-runner — systemd has no venv on PATH."""
    candidate = Path(sys.executable).parent / "icloud-sync-runner"
    if candidate.is_file():
        return str(candidate)
    found = shutil.which("icloud-sync-runner")
    if found:
        return found
    raise FileNotFoundError("icloud-sync-runner not found; is the package installed?")


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["systemctl", "--user", *args],
                          capture_output=True, text=True)


def service_unit(folder_id: str) -> str:
    return f"icloud-sync@{folder_id}.service"


def timer_unit(folder_id: str) -> str:
    return f"icloud-sync@{folder_id}.timer"


def install_service_template() -> None:
    unit_dir = paths.systemd_user_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    content = f"""\
[Unit]
Description=iCloud sync (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={runner_executable()} %i

[Install]
WantedBy=default.target
"""
    (unit_dir / SERVICE_TEMPLATE_NAME).write_text(content)
    _systemctl("daemon-reload")


def set_startup_sync(folder_id: str, enabled: bool) -> None:
    if enabled:
        install_service_template()
        _systemctl("enable", service_unit(folder_id))
    else:
        _systemctl("disable", service_unit(folder_id))


def startup_sync_enabled(folder_id: str) -> bool:
    return _systemctl("is-enabled", service_unit(folder_id)).stdout.strip() == "enabled"


def set_timer(folder_id: str, interval_minutes: int | None) -> None:
    unit_dir = paths.systemd_user_dir()
    timer_path = unit_dir / timer_unit(folder_id)
    if interval_minutes:
        install_service_template()
        timer_path.write_text(f"""\
[Unit]
Description=Periodic iCloud sync ({folder_id})

[Timer]
OnBootSec=2min
OnUnitActiveSec={interval_minutes}min
Unit={service_unit(folder_id)}

[Install]
WantedBy=timers.target
""")
        _systemctl("daemon-reload")
        _systemctl("enable", "--now", timer_unit(folder_id))
    else:
        _systemctl("disable", "--now", timer_unit(folder_id))
        timer_path.unlink(missing_ok=True)
        _systemctl("daemon-reload")


def timer_enabled(folder_id: str) -> bool:
    return _systemctl("is-enabled", timer_unit(folder_id)).stdout.strip() == "enabled"


def service_active(folder_id: str) -> bool:
    return _systemctl("is-active", service_unit(folder_id)).stdout.strip() == "active"


def start_service(folder_id: str) -> None:
    install_service_template()
    _systemctl("start", "--no-block", service_unit(folder_id))


def stop_service(folder_id: str) -> None:
    _systemctl("stop", service_unit(folder_id))


def remove_folder_units(folder_id: str) -> None:
    """On folder delete: drop its timer and enablement."""
    _systemctl("disable", "--now", timer_unit(folder_id))
    _systemctl("disable", service_unit(folder_id))
    (paths.systemd_user_dir() / timer_unit(folder_id)).unlink(missing_ok=True)
    _systemctl("daemon-reload")


def unit_status_text(folder_id: str) -> str:
    out = _systemctl("status", "--no-pager", service_unit(folder_id))
    timer = _systemctl("status", "--no-pager", timer_unit(folder_id))
    return (out.stdout + out.stderr + "\n" + timer.stdout + timer.stderr).strip()


def linger_enabled() -> bool:
    result = subprocess.run(
        ["loginctl", "show-user", getpass.getuser(), "--property=Linger"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() == "Linger=yes"


def enable_linger() -> tuple[bool, str]:
    result = subprocess.run(["loginctl", "enable-linger", getpass.getuser()],
                            capture_output=True, text=True)
    ok = result.returncode == 0
    return ok, (result.stderr.strip() or result.stdout.strip())
