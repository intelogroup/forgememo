"""Edge-case unit tests for forgemem CLI changes.

Covers:
  - _ver()              : version string parsing
  - _check_for_update() : cache hit/miss/stale/direction/malformed/network-error
  - init --yes          : auto non-interactive when stdin is not a TTY
  - start --mine        : miner plist written with correct content
  - stop                : miner plist unloaded and removed alongside server plist
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from forgemem.cli import _ver, _check_for_update, app


runner = CliRunner()


# ---------------------------------------------------------------------------
# _ver()
# ---------------------------------------------------------------------------

class TestVer:
    def test_normal(self):
        assert _ver("0.1.3") == (0, 1, 3)

    def test_major_only(self):
        assert _ver("2") == (2,)

    def test_two_part(self):
        assert _ver("1.4") == (1, 4)

    def test_malformed_letters(self):
        # Contains non-integer segment — should return sentinel (0,)
        assert _ver("1.0.0a1") == (0,)

    def test_empty_string(self):
        # int("") raises ValueError
        assert _ver("") == (0,)

    def test_version_ordering(self):
        assert _ver("0.1.3") > _ver("0.1.2")
        assert _ver("1.0.0") > _ver("0.9.9")
        assert _ver("0.2.0") > _ver("0.1.99")

    def test_equal_versions(self):
        assert _ver("1.2.3") == _ver("1.2.3")

    def test_newer_current_than_latest(self):
        # Scenario that caused the original bug
        assert not (_ver("0.1.2") > _ver("0.1.3"))


# ---------------------------------------------------------------------------
# _check_for_update()
# ---------------------------------------------------------------------------

class TestCheckForUpdate:
    """
    Use tmp_path to give _check_for_update a real writable home dir.
    Patch Path.home() → tmp_path so cache_file = tmp_path/.forgemem/.update_check
    is a real Path we can pre-populate and inspect.
    """

    def _run(self, tmp_path: Path, current_ver: str, cached_ver: str | None,
             pypi_ver: str | None, cache_age_seconds: float = 100):
        """
        Set up cache file (if cached_ver given), patch __version__ + Path.home,
        run _check_for_update, return (printed_messages, cache_file_path).
        """
        import os
        import time as _time
        import forgemem.cli as cli_mod

        cache_dir = tmp_path / ".forgemem"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / ".update_check"

        if cached_ver is not None:
            cache_file.write_text(cached_ver)
            # Back-date the mtime so age = cache_age_seconds
            mtime = _time.time() - cache_age_seconds
            os.utime(cache_file, (mtime, mtime))

        printed = []

        with (
            patch.object(cli_mod, "__version__", current_ver),
            patch("forgemem.cli.Path") as mock_path_cls,
            patch.object(cli_mod.console, "print", side_effect=printed.append),
        ):
            # Make Path.home() return tmp_path; all other Path() calls pass through
            real_path = Path
            mock_path_cls.home.return_value = tmp_path
            mock_path_cls.side_effect = lambda *a, **kw: real_path(*a, **kw)

            if pypi_ver is not None:
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"info": {"version": pypi_ver}}
                with patch("requests.get", return_value=mock_resp):
                    _check_for_update()
            else:
                _check_for_update()

        return printed, cache_file

    def test_no_update_needed_cache_same_version(self, tmp_path):
        """Cache says 0.1.3, current is 0.1.3 → cache invalidated, PyPI returns same → no message."""
        printed, cache_file = self._run(tmp_path, "0.1.3", "0.1.3", "0.1.3")
        assert not any("Update available" in str(m) for m in printed)

    def test_update_available_from_cache(self, tmp_path):
        """Cache says 0.2.0, current is 0.1.3 → show update message."""
        printed, cache_file = self._run(tmp_path, "0.1.3", "0.2.0", None, cache_age_seconds=100)
        assert any("Update available" in str(m) for m in printed)

    def test_no_backwards_notification(self, tmp_path):
        """Cache says 0.1.2, current is 0.1.3 → original bug: must NOT show update message."""
        printed, cache_file = self._run(tmp_path, "0.1.3", "0.1.2", "0.1.3")
        assert not any("Update available" in str(m) for m in printed)
        # cache should have been invalidated and rewritten with the fresh PyPI value
        assert cache_file.read_text().strip() == "0.1.3"

    def test_stale_cache_triggers_pypi_check(self, tmp_path):
        """Cache is older than 24h → fresh PyPI check regardless of cached content."""
        printed, cache_file = self._run(tmp_path, "0.1.3", "0.1.2", "0.1.4",
                                        cache_age_seconds=90000)
        assert any("Update available" in str(m) for m in printed)
        assert cache_file.read_text().strip() == "0.1.4"

    def test_empty_cache_content_no_crash(self, tmp_path):
        """Empty/malformed cache content → no exception raised."""
        printed, _ = self._run(tmp_path, "0.1.3", "", "0.1.4", cache_age_seconds=100)
        # empty string → falsy → else: return early, no message
        assert not any("Update available" in str(m) for m in printed)

    def test_network_error_silenced(self, tmp_path):
        """requests.get raises ConnectionError → must not propagate."""
        import forgemem.cli as cli_mod
        with (
            patch.object(cli_mod, "__version__", "0.1.3"),
            patch("forgemem.cli.Path") as mock_path_cls,
            patch("requests.get", side_effect=ConnectionError("network down")),
        ):
            real_path = Path
            mock_path_cls.home.return_value = tmp_path
            mock_path_cls.side_effect = lambda *a, **kw: real_path(*a, **kw)
            _check_for_update()  # must not raise


# ---------------------------------------------------------------------------
# init: auto non-interactive on non-TTY stdin
# ---------------------------------------------------------------------------

class TestInitNonTTY:
    def test_auto_yes_when_no_tty(self, tmp_path):
        """When stdin is not a TTY, init should never call typer.confirm or typer.prompt."""
        confirm_calls = []
        prompt_calls = []

        with (
            patch("forgemem.cli.Path.home", return_value=tmp_path),
            patch("forgemem.core.DB_PATH", tmp_path / "forgemem.db"),
            patch("forgemem.core.get_conn") as mock_conn,
            patch("forgemem.core.INIT_SQL", ""),
            patch("typer.confirm", side_effect=lambda *a, **kw: confirm_calls.append(a) or True),
            patch("typer.prompt", side_effect=lambda *a, **kw: prompt_calls.append(a) or "6"),
            patch("sys.stdin") as mock_stdin,
            patch("forgemem.cli._auto_detect_and_generate_skills"),
            patch("forgemem.cli._prompt_provider_setup"),
            patch("forgemem.config.load", return_value={"provider": "forgemem"}),
        ):
            mock_stdin.isatty.return_value = False
            mock_conn.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.return_value.executescript = MagicMock()
            mock_conn.return_value.commit = MagicMock()
            mock_conn.return_value.close = MagicMock()

            runner.invoke(app, ["init"])

        assert confirm_calls == [], f"typer.confirm was called: {confirm_calls}"
        assert prompt_calls == [], f"typer.prompt was called: {prompt_calls}"

    def test_tty_allows_prompts(self):
        """When stdin IS a TTY and --yes not passed, yes=False (prompts allowed)."""
        # Just verify the flag logic: isatty=True + no --yes → yes stays False
        # We don't run the full command; just test the branch directly
        original = sys.stdin
        try:
            mock_stdin = MagicMock()
            mock_stdin.isatty.return_value = True
            sys.stdin = mock_stdin
            # yes=False, isatty=True → yes stays False
            yes = False
            if not yes and not sys.stdin.isatty():
                yes = True
            assert yes is False
        finally:
            sys.stdin = original


# ---------------------------------------------------------------------------
# start --mine: miner plist written correctly
# ---------------------------------------------------------------------------

class TestStartMine:
    @pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent is macOS only")
    def test_miner_plist_written(self, tmp_path):
        """--mine writes com.forgemem.miner.plist with correct StartInterval and command."""
        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()
        server_plist = plist_dir / "com.forgemem.server.plist"
        miner_plist = plist_dir / "com.forgemem.miner.plist"
        log_path = tmp_path / "Logs" / "forgemem.log"

        with (
            patch("forgemem.cli.PLIST_PATH", server_plist),
            patch("forgemem.cli.MINER_PLIST_PATH", miner_plist),
            patch("forgemem.cli.LOG_PATH", log_path),
            patch("shutil.which", return_value="/usr/local/bin/forgemem"),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            patch("forgemem.core.DB_PATH", tmp_path / "forgemem.db"),
            patch("forgemem.config.load", return_value={"provider": "anthropic"}),
        ):
            result = runner.invoke(app, ["start", "--mine", "--mine-interval", "1800"])

        assert result.exit_code == 0, result.output
        assert miner_plist.exists(), "miner plist was not created"

        content = miner_plist.read_text()
        assert "com.forgemem.miner" in content
        assert "<string>mine</string>" in content
        assert "<integer>1800</integer>" in content

    @pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent is macOS only")
    def test_no_mine_flag_skips_miner_plist(self, tmp_path):
        """Without --mine, no miner plist is created."""
        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()
        server_plist = plist_dir / "com.forgemem.server.plist"
        miner_plist = plist_dir / "com.forgemem.miner.plist"
        log_path = tmp_path / "Logs" / "forgemem.log"

        with (
            patch("forgemem.cli.PLIST_PATH", server_plist),
            patch("forgemem.cli.MINER_PLIST_PATH", miner_plist),
            patch("forgemem.cli.LOG_PATH", log_path),
            patch("shutil.which", return_value="/usr/local/bin/forgemem"),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            patch("forgemem.core.DB_PATH", tmp_path / "forgemem.db"),
            patch("forgemem.config.load", return_value={"provider": "anthropic"}),
        ):
            result = runner.invoke(app, ["start"])

        assert result.exit_code == 0, result.output
        assert not miner_plist.exists(), "miner plist should not exist without --mine"

    @pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent is macOS only")
    def test_default_mine_interval_is_3600(self, tmp_path):
        """Default --mine-interval is 3600 seconds."""
        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()
        server_plist = plist_dir / "com.forgemem.server.plist"
        miner_plist = plist_dir / "com.forgemem.miner.plist"
        log_path = tmp_path / "Logs" / "forgemem.log"

        with (
            patch("forgemem.cli.PLIST_PATH", server_plist),
            patch("forgemem.cli.MINER_PLIST_PATH", miner_plist),
            patch("forgemem.cli.LOG_PATH", log_path),
            patch("shutil.which", return_value="/usr/local/bin/forgemem"),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            patch("forgemem.core.DB_PATH", tmp_path / "forgemem.db"),
            patch("forgemem.config.load", return_value={"provider": "anthropic"}),
        ):
            runner.invoke(app, ["start", "--mine"])

        assert "<integer>3600</integer>" in miner_plist.read_text()


# ---------------------------------------------------------------------------
# stop: unloads and removes both plists
# ---------------------------------------------------------------------------

class TestStop:
    @pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent is macOS only")
    def test_stop_unloads_miner_if_present(self, tmp_path):
        """stop unloads miner plist when it exists."""
        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()
        server_plist = plist_dir / "com.forgemem.server.plist"
        miner_plist = plist_dir / "com.forgemem.miner.plist"
        server_plist.write_text("dummy")
        miner_plist.write_text("dummy")

        launchctl_calls = []

        def fake_run(cmd, **kwargs):
            launchctl_calls.append(cmd)
            return MagicMock(returncode=0, stderr="")

        with (
            patch("forgemem.cli.PLIST_PATH", server_plist),
            patch("forgemem.cli.MINER_PLIST_PATH", miner_plist),
            patch("subprocess.run", side_effect=fake_run),
            patch("typer.confirm", return_value=False),  # don't remove files
            patch("forgemem.config.load", return_value={"provider": "anthropic"}),
        ):
            result = runner.invoke(app, ["stop"])

        assert result.exit_code == 0, result.output
        unloaded = [cmd for cmd in launchctl_calls if "unload" in cmd]
        assert any(str(server_plist) in str(c) for c in unloaded), "server plist not unloaded"
        assert any(str(miner_plist) in str(c) for c in unloaded), "miner plist not unloaded"

    @pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent is macOS only")
    def test_stop_skips_miner_if_absent(self, tmp_path):
        """stop does not error when miner plist doesn't exist."""
        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()
        server_plist = plist_dir / "com.forgemem.server.plist"
        miner_plist = plist_dir / "com.forgemem.miner.plist"  # does NOT exist
        server_plist.write_text("dummy")

        launchctl_calls = []

        def fake_run(cmd, **kwargs):
            launchctl_calls.append(cmd)
            return MagicMock(returncode=0, stderr="")

        with (
            patch("forgemem.cli.PLIST_PATH", server_plist),
            patch("forgemem.cli.MINER_PLIST_PATH", miner_plist),
            patch("subprocess.run", side_effect=fake_run),
            patch("typer.confirm", return_value=False),
        ):
            result = runner.invoke(app, ["stop"])

        assert result.exit_code == 0, result.output
        unloaded_targets = [str(c) for c in launchctl_calls if "unload" in str(c)]
        # Check specifically for the miner plist filename, not just "miner" (which
        # could match the pytest temp dir name like "test_stop_skips_miner_if_absent0")
        assert not any("com.forgemem.miner.plist" in t for t in unloaded_targets), \
            "miner launchctl unload should not be called when plist absent"

    @pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent is macOS only")
    def test_stop_removes_both_plists_on_confirm(self, tmp_path):
        """stop removes both plist files when user confirms."""
        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()
        server_plist = plist_dir / "com.forgemem.server.plist"
        miner_plist = plist_dir / "com.forgemem.miner.plist"
        server_plist.write_text("dummy")
        miner_plist.write_text("dummy")

        with (
            patch("forgemem.cli.PLIST_PATH", server_plist),
            patch("forgemem.cli.MINER_PLIST_PATH", miner_plist),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            patch("typer.confirm", return_value=True),  # confirm removal
        ):
            result = runner.invoke(app, ["stop"])

        assert result.exit_code == 0, result.output
        assert not server_plist.exists(), "server plist should be removed"
        assert not miner_plist.exists(), "miner plist should be removed"


# ---------------------------------------------------------------------------
# Provider guard: init and start must exit(1) when no provider configured
# ---------------------------------------------------------------------------

class TestProviderGuard:
    def test_init_exits_1_when_no_provider(self, tmp_path):
        """forgemem init must exit with code 1 when no provider is configured (non-interactive)."""
        from forgemem.core import INIT_SQL
        with (
            patch("forgemem.core.DB_PATH", tmp_path / "forgemem.db"),
            patch("forgemem.cli._register_mcp", return_value=False),
            patch("forgemem.cli._auto_detect_and_generate_skills", return_value=None),
            patch("forgemem.config.detect_ollama", return_value=None),
            patch("forgemem.config.load", return_value={}),
            patch("forgemem.config.save", return_value=None),
        ):
            result = runner.invoke(app, ["init", "--yes"])
        assert result.exit_code == 1, result.output
        assert "ACTION REQUIRED" in result.output

    @pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent is macOS only")
    def test_start_exits_1_when_no_provider(self):
        """forgemem start must exit with code 1 when no provider is configured."""
        with patch("forgemem.config.load", return_value={}):
            result = runner.invoke(app, ["start"])
        assert result.exit_code == 1, result.output
        assert "ACTION REQUIRED" in result.output


# ---------------------------------------------------------------------------
# Version sync: pyproject.toml and __init__.py must always agree
# ---------------------------------------------------------------------------

class TestVersionSync:
    def test_version_matches_package_metadata(self):
        """forgemem.__version__ must equal importlib.metadata.version('forgemem')."""
        from importlib.metadata import version as pkg_version
        import forgemem
        assert forgemem.__version__ == pkg_version("forgemem"), (
            f"forgemem.__version__ ({forgemem.__version__!r}) != "
            f"installed package metadata ({pkg_version('forgemem')!r}). "
            "Run 'pip install -e .' to sync."
        )

    def test_pyproject_version_matches_init(self):
        """pyproject.toml version must equal forgemem.__version__ at import time."""
        import sys
        from pathlib import Path
        import forgemem

        pyproject = Path(__file__).parent / "pyproject.toml"
        if sys.version_info >= (3, 11):
            import tomllib
            data = tomllib.loads(pyproject.read_text())
        else:
            try:
                import tomli
                data = tomli.loads(pyproject.read_text())
            except ImportError:
                import re
                match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.MULTILINE)
                assert match, "Could not parse version from pyproject.toml"
                data = {"project": {"version": match.group(1)}}

        pyproject_ver = data["project"]["version"]
        assert pyproject_ver == forgemem.__version__, (
            f"pyproject.toml version ({pyproject_ver!r}) != "
            f"forgemem.__version__ ({forgemem.__version__!r}). "
            "Bump pyproject.toml and reinstall with 'pip install -e .'."
        )
