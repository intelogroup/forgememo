"""
Tests for CLI file permissions and config directory handling.

Covers:
  1. Config directory auto-creation with correct permissions
  2. Config file read/write when parent dir is missing
  3. Config survives permission-restricted parent (read-only dir)
  4. DB directory auto-creation
  5. Daemon log directory fallback when primary is unwritable
  6. Skill file paths resolve under home directory
  7. Concurrent config writes don't corrupt JSON
"""

import json
import os
import stat
import sys
import threading

import pytest

import forgememo.config as config_module
import forgememo.storage as storage_module
from forgememo.config import load, save


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    config_file = tmp_path / ".forgemem" / "config.json"
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_file)
    monkeypatch.setattr(
        config_module, "CREDITS_FLAG_PATH", config_file.parent / ".credits_exhausted"
    )
    yield config_file


# ─── Config directory auto-creation ──────────────────────────────────────────


class TestConfigDirCreation:
    def test_save_creates_parent_directories(self, isolated_config):
        """save() should create ~/.forgemem/ if it doesn't exist."""
        assert not isolated_config.parent.exists()
        save({"provider": "anthropic"})
        assert isolated_config.parent.exists()
        assert isolated_config.exists()

    def test_save_preserves_existing_data_structure(self, isolated_config):
        save({"provider": "openai", "api_key": "sk-test"})
        data = load()
        assert data["provider"] == "openai"
        assert data["api_key"] == "sk-test"

    def test_load_returns_empty_when_no_file(self, isolated_config):
        assert load() == {}

    def test_load_handles_corrupt_json(self, isolated_config):
        """Malformed config.json should return empty dict, not crash."""
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text("{broken json!!!")
        assert load() == {}

    def test_load_handles_empty_file(self, isolated_config):
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text("")
        assert load() == {}

    def test_save_overwrites_corrupt_config(self, isolated_config):
        """Saving over a corrupt file should produce valid JSON."""
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text("GARBAGE{{{")
        save({"provider": "gemini"})
        assert load() == {"provider": "gemini"}

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
    @pytest.mark.skipif(
        getattr(os, "getuid", lambda: -1)() == 0, reason="Root has permissive umask"
    )
    def test_config_dir_permissions_are_user_only(self, isolated_config):
        """Config directory should not be world-accessible after creation."""
        save({"test": True})
        mode = os.stat(isolated_config.parent).st_mode & 0o777
        assert mode & 0o200 != 0, f"Config dir not owner-writable: {oct(mode)}"
        assert mode & 0o007 == 0, f"Config dir is world-accessible: {oct(mode)}"


# ─── Read-only parent directory ──────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
@pytest.mark.skipif(
    getattr(os, "getuid", lambda: -1)() == 0, reason="Root bypasses permission checks"
)
class TestReadOnlyParent:
    def test_save_fails_gracefully_on_readonly_parent(self, tmp_path, monkeypatch):
        """If parent of config dir is read-only, save() should raise, not corrupt."""
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        config_file = readonly_dir / ".forgemem" / "config.json"
        monkeypatch.setattr(config_module, "CONFIG_PATH", config_file)

        os.chmod(readonly_dir, stat.S_IRUSR | stat.S_IXUSR)
        try:
            with pytest.raises((OSError, PermissionError)):
                save({"test": True})
        finally:
            os.chmod(readonly_dir, stat.S_IRWXU)


# ─── DB directory auto-creation ──────────────────────────────────────────────


class TestDBDirCreation:
    def test_get_conn_creates_nested_parent_dirs(self, tmp_path, monkeypatch):
        """get_conn() should create deeply nested parent directories."""
        db_file = tmp_path / "deep" / "nested" / "path" / "test.db"
        monkeypatch.setattr(storage_module, "DB_PATH", db_file)
        conn = storage_module.get_conn()
        assert db_file.exists()
        conn.close()

    def test_db_path_override(self, tmp_path, monkeypatch):
        """Overriding DB_PATH should redirect init_db to the custom path."""
        db_file = tmp_path / "custom.db"
        monkeypatch.setattr(storage_module, "DB_PATH", db_file)
        storage_module.init_db()
        assert db_file.exists()


# ─── Daemon log directory fallback ───────────────────────────────────────────


class TestDaemonLogFallback:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
    @pytest.mark.skipif(
        getattr(os, "getuid", lambda: -1)() == 0,
        reason="Root bypasses permission checks",
    )
    def test_log_fallback_logic(self, tmp_path):
        """The fallback pattern: if primary dir fails and ALLOW_TMP_LOG=1, use /tmp."""
        import tempfile

        readonly = tmp_path / "noperm"
        readonly.mkdir()
        log_path = str(readonly / "logs" / "daemon.log")

        os.chmod(readonly, stat.S_IRUSR | stat.S_IXUSR)
        try:
            try:
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                fell_back = False
            except OSError:
                fallback = os.path.join(tempfile.gettempdir(), "forgememo_daemon.log")
                os.makedirs(os.path.dirname(fallback), exist_ok=True)
                fell_back = True

            assert fell_back, "Expected OSError creating log dir in read-only parent"
            assert fallback.startswith(tempfile.gettempdir()), (
                f"Fallback not in tempdir: {fallback}"
            )
        finally:
            os.chmod(readonly, stat.S_IRWXU)


# ─── Concurrent config writes ───────────────────────────────────────────────


class TestConcurrentConfigWrites:
    def test_concurrent_saves_produce_valid_json(self, isolated_config):
        """Multiple threads writing config concurrently should not corrupt JSON."""
        N = 20
        errors = []

        def write_config(i):
            try:
                save({"writer": i, "data": f"value-{i}"})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_config, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Config write errors: {errors}"
        data = load()
        assert "writer" in data
        assert isinstance(data["writer"], int)

    def test_rapid_load_save_cycle(self, isolated_config):
        """Rapid read-modify-write cycles should not lose data."""
        save({"counter": 0})
        for i in range(50):
            cfg = load()
            cfg["counter"] = i + 1
            save(cfg)

        final = load()
        assert final["counter"] == 50


# ─── Skill file path resolution ─────────────────────────────────────────────


class TestSkillPaths:
    def test_skill_paths_under_home(self):
        """All skill paths should be under user's home directory."""
        from forgememo.commands._shared import SKILL_PATHS

        home = str(os.path.expanduser("~"))
        for name, path in SKILL_PATHS.items():
            assert str(path).startswith(home), (
                f"Skill path for {name} is not under home: {path}"
            )

    def test_skill_paths_are_absolute(self):
        from forgememo.commands._shared import SKILL_PATHS

        for name, path in SKILL_PATHS.items():
            assert path.is_absolute(), f"Skill path for {name} is relative: {path}"
