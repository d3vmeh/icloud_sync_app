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
        # resilience + newer-wins resync so a single bad file doesn't abort and
        # a fresh local edit isn't clobbered by an older cloud copy
        assert "--resilient" in cmd and "--recover" in cmd
        assert cmd[cmd.index("--resync-mode") + 1] == "newer"

    def test_plain_bisync_has_no_resync(self):
        cmd = rclone.build_command(make_folder(), "bisync")
        assert "--resync" not in cmd and "--resync-mode" not in cmd
        assert "--resilient" in cmd

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

    def test_excludes_become_flags(self):
        folder = make_folder(excludes=["node_modules/**", "*.tmp"])
        cmd = rclone.build_command(folder, "pull")
        assert cmd.count("--exclude") == 2
        assert "node_modules/**" in cmd and "*.tmp" in cmd


class TestRcloneParsing:
    def test_stats_line(self):
        line = json.dumps({"level": "notice", "msg": "stats",
                           "stats": {"bytes": 50, "totalBytes": 200, "speed": 1024.0,
                                     "eta": 12, "transferring": [{"name": "a"}]}})
        entry = rclone.parse_line(line)
        progress = rclone.extract_progress(entry)
        assert progress == Progress(bytes_done=50, bytes_total=200, speed=1024.0,
                                    eta=12, transferring=1, current_file="a")
        assert progress.percent == 0.25

    def test_current_file_is_first_transferring_name(self):
        line = json.dumps({"stats": {"transferring": [
            {"name": "dir/big.zip", "bytes": 5}, {"name": "dir/other.bin"}]}})
        progress = rclone.extract_progress(rclone.parse_line(line))
        assert progress.transferring == 2
        assert progress.current_file == "dir/big.zip"

    def test_no_transfer_leaves_current_file_none(self):
        line = json.dumps({"stats": {"bytes": 0, "totalBytes": 0, "checks": 40}})
        progress = rclone.extract_progress(rclone.parse_line(line))
        assert progress.transferring == 0
        assert progress.current_file is None

    def test_non_json_line(self):
        assert rclone.parse_line("plain text output") is None

    def test_auth_error_detection(self):
        assert rclone.is_auth_error("CRITICAL: 401 Unauthorized")
        assert rclone.is_auth_error("trust token expired, please reauthenticate")
        assert not rclone.is_auth_error("copied 5 files")

    def test_auth_errors_only_count_at_error_level(self):
        # stats lines are full of numbers that can contain a literal 401
        stats = {"level": "notice", "msg": "stats",
                 "stats": {"transferTime": 401.6, "totalTransfers": 10401}}
        assert not rclone.entry_is_auth_error(stats)
        assert rclone.entry_is_auth_error(
            {"level": "error", "msg": "401 Unauthorized"})
        assert not rclone.entry_is_auth_error(
            {"level": "info", "msg": "401 objects listed"})


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


class TestRunnerResyncDecision:
    def _decide(self, folder, state, action="bisync"):
        import io

        from icloud_sync.runner import _effective_action
        return _effective_action(folder, action, state, io.StringIO())

    def test_filter_change_escalates_bisync_to_resync(self, monkeypatch):
        monkeypatch.setattr("icloud_sync.runner._bisync_initialized", lambda f: True)
        folder = make_folder(excludes=["node_modules/**"])
        assert self._decide(folder, FolderState(filters_sig="")) == "bisync-resync"

    def test_unchanged_filters_keep_plain_bisync(self, monkeypatch):
        monkeypatch.setattr("icloud_sync.runner._bisync_initialized", lambda f: True)
        folder = make_folder(excludes=["node_modules/**"])
        state = FolderState(filters_sig="node_modules/**")
        assert self._decide(folder, state) == "bisync"

    def test_filter_change_never_affects_pull(self, monkeypatch):
        monkeypatch.setattr("icloud_sync.runner._bisync_initialized", lambda f: True)
        folder = make_folder(excludes=["a"])
        assert self._decide(folder, FolderState(filters_sig=""), "pull") == "pull"

    def test_bisync_initialized_matches_spaced_path_and_final_lst(self, monkeypatch, tmp_path):
        from icloud_sync.runner import _bisync_initialized
        monkeypatch.setattr("icloud_sync.runner._BISYNC_WORKDIR", tmp_path)
        folder = make_folder(keep_parent=True, local_path="/home/me/iCloud Documents",
                             remote_path="Documents/LABKickstart-org")
        # rclone encodes the space in "iCloud Documents" as '_'
        stem = "r_Documents_LABKickstart-org..home_me_iCloud_Documents_LABKickstart-org.path1"
        # an aborted/in-flight run leaves only a temp listing -> not initialised
        (tmp_path / f"{stem}.lst-new").touch()
        assert _bisync_initialized(folder) is False
        # a completed baseline (.lst) -> initialised (spaced path still matches)
        (tmp_path / f"{stem}.lst").touch()
        assert _bisync_initialized(folder) is True
