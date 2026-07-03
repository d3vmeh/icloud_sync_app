"""Sync-pair data model, persisted as JSON under the XDG config dir."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import paths

SyncMode = Literal["pull", "push", "bisync"]

_ID_RE = re.compile(r"[^a-z0-9-]+")


def slugify(name: str) -> str:
    slug = _ID_RE.sub("-", name.lower()).strip("-") or "folder"
    return f"{slug}-{secrets.token_hex(2)}"


@dataclass
class SyncFolder:
    id: str
    name: str
    remote: str
    remote_path: str
    local_path: str
    mode: SyncMode = "bisync"
    sync_on_startup: bool = False
    interval_minutes: int | None = None
    check_access: bool = False
    keep_parent: bool = False

    @property
    def remote_full(self) -> str:
        return f"{self.remote}:{self.remote_path}"

    @property
    def local_expanded(self) -> Path:
        return Path(self.local_path).expanduser()

    @property
    def local_target(self) -> Path:
        """Where rclone actually syncs to. rclone copies a directory's
        *contents*, so keep_parent recreates the remote folder itself
        (e.g. `.../MyFolder`) inside the local path."""
        name = Path(self.remote_path).name
        if self.keep_parent and name:
            return self.local_expanded / name
        return self.local_expanded

    @classmethod
    def new(cls, name: str, remote: str, remote_path: str, local_path: str,
            mode: SyncMode = "bisync") -> SyncFolder:
        return cls(id=slugify(name), name=name, remote=remote,
                   remote_path=remote_path, local_path=local_path, mode=mode)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyncFolder:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class AppConfig:
    folders: list[SyncFolder] = field(default_factory=list)

    def get(self, folder_id: str) -> SyncFolder | None:
        return next((f for f in self.folders if f.id == folder_id), None)

    def upsert(self, folder: SyncFolder) -> None:
        for i, existing in enumerate(self.folders):
            if existing.id == folder.id:
                self.folders[i] = folder
                return
        self.folders.append(folder)

    def remove(self, folder_id: str) -> None:
        self.folders = [f for f in self.folders if f.id != folder_id]


def load_config() -> AppConfig:
    path = paths.config_file()
    if not path.exists():
        return AppConfig()
    data = json.loads(path.read_text())
    return AppConfig(folders=[SyncFolder.from_dict(d) for d in data.get("folders", [])])


def save_config(config: AppConfig) -> None:
    paths.ensure_dirs()
    path = paths.config_file()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"folders": [asdict(f) for f in config.folders]}, indent=2) + "\n")
    tmp.replace(path)
