from __future__ import annotations

import json

import pytest

from icloud_sync import paths, rclone
from icloud_sync.config import AppConfig, SyncFolder, load_config, save_config, slugify
from icloud_sync.state import FolderState, Progress, read_state, write_state


@pytest.fixture(autouse=True)
def xdg_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path


def make_folder(**overrides) -> SyncFolder:
    defaults = dict(id="docs-abcd", name="Docs", remote="my-remote",
                    remote_path="Documents/Sub", local_path="~/docs")
    return SyncFolder(**{**defaults, **overrides})


class TestConfig:
    def test_round_trip(self):
        config = AppConfig(folders=[make_folder(mode="pull", interval_minutes=15)])
        save_config(config)
        loaded = load_config()
        assert loaded.folders == config.folders

    def test_missing_file_gives_empty_config(self):
        assert load_config().folders == []

    def test_from_dict_ignores_unknown_keys(self):
        folder = SyncFolder.from_dict(
            {"id": "x", "name": "X", "remote": "r", "remote_path": "p",
             "local_path": "l", "someday_field": True})
        assert folder.id == "x"

    def test_slugify(self):
        slug = slugify("My Docs / Photos!")
        assert slug.startswith("my-docs-photos-")
        assert " " not in slug

    def test_upsert_and_remove(self):
        config = AppConfig()
        folder = make_folder()
        config.upsert(folder)
        config.upsert(make_folder(name="Renamed"))
        assert len(config.folders) == 1
        assert config.folders[0].name == "Renamed"
        config.remove(folder.id)
        assert config.folders == []


class TestRcloneCommands:
    def test_pull(self):
        cmd = rclone.build_command(make_folder(), "pull")
        assert cmd[:2] == ["rclone", "copy"]
        assert cmd[2] == "my-remote:Documents/Sub"
        assert "--use-json-log" in cmd

    def test_push_reverses_direction(self):
        cmd = rclone.build_command(make_folder(), "push")
        assert cmd[3] == "my-remote:Documents/Sub"

    def test_bisync_flags(self):
        folder = make_folder(check_access=True)
        cmd = rclone.build_command(folder, "bisync-resync", dry_run=True)
        assert "bisync" in cmd
        assert "--check-access" in cmd
        assert "--resync" in cmd
        assert cmd[-1] == "--dry-run"
        assert "--conflict-resolve" in cmd

    def test_unknown_action(self):
        with pytest.raises(ValueError):
            rclone.build_command(make_folder(), "explode")

    def test_keep_parent_nests_remote_folder_locally(self):
        folder = make_folder(keep_parent=True, local_path="/data/sync",
                             remote_path="Documents/MyFolder")
        assert str(folder.local_target) == "/data/sync/MyFolder"
        cmd = rclone.build_command(folder, "pull")
        assert cmd[3] == "/data/sync/MyFolder"

    def test_keep_parent_off_syncs_contents_directly(self):
        folder = make_folder(local_path="/data/sync")
        assert str(folder.local_target) == "/data/sync"


class TestRcloneParsing:
    def test_stats_line(self):
        line = json.dumps({"level": "notice", "msg": "stats",
                           "stats": {"bytes": 50, "totalBytes": 200, "speed": 1024.0,
                                     "eta": 12, "transferring": [{"name": "a"}]}})
        entry = rclone.parse_line(line)
        progress = rclone.extract_progress(entry)
        assert progress == Progress(bytes_done=50, bytes_total=200, speed=1024.0,
                                    eta=12, transferring=1)
        assert progress.percent == 0.25

    def test_non_json_line(self):
        assert rclone.parse_line("plain text output") is None

    def test_auth_error_detection(self):
        assert rclone.is_auth_error("CRITICAL: 401 Unauthorized")
        assert rclone.is_auth_error("trust token expired, please reauthenticate")
        assert not rclone.is_auth_error("copied 5 files")


class TestState:
    def test_round_trip(self):
        state = FolderState(running=False, last_run="2026-07-02T10:00:00+00:00",
                            exit_code=0, progress=Progress(bytes_done=1, bytes_total=2))
        write_state("docs-abcd", state)
        loaded = read_state("docs-abcd")
        assert loaded.exit_code == 0
        assert loaded.progress.bytes_total == 2

    def test_missing_state_is_default(self):
        state = read_state("nope")
        assert state.running is False
        assert state.needs_reconnect is False

    def test_stale_running_pid_is_cleared(self):
        write_state("docs-abcd", FolderState(running=True, pid=99999999))
        assert read_state("docs-abcd").running is False

    def test_corrupt_state_file(self):
        paths.ensure_dirs()
        paths.state_file("docs-abcd").write_text("{broken json")
        assert read_state("docs-abcd") == FolderState()
