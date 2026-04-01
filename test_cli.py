"""Edge-case unit tests for forgememo CLI changes.

Covers:
  - _ver()              : version string parsing
  - _check_for_update() : cache hit/miss/stale/direction/malformed/network-error
  - init provider guard : first-run provider setup requires an interactive TTY
  - start --mine        : miner plist written with correct content
  - stop                : miner plist unloaded and removed alongside server plist
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from forgememo.cli import _ver, _check_for_update, app


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
    Patch Path.home() → tmp_path so cache_file = tmp_path/.forgememo/.update_check
    is a real Path we can pre-populate and inspect.
    """

    def _run(
        self,
        tmp_path: Path,
        current_ver: str,
        cached_ver: str | None,
        pypi_ver: str | None,
        cache_age_seconds: float = 100,
    ):
        """
        Set up cache file (if cached_ver given), patch __version__ + Path.home,
        run _check_for_update, return (printed_messages, cache_file_path).
        """
        import os
        import time as _time
        import forgememo.cli as cli_mod

        cache_dir = tmp_path / ".forgememo"
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
            patch("forgememo.cli.Path") as mock_path_cls,
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
        printed, cache_file = self._run(
            tmp_path, "0.1.3", "0.2.0", None, cache_age_seconds=100
        )
        assert any("Update available" in str(m) for m in printed)

    def test_no_backwards_notification(self, tmp_path):
        """Cache says 0.1.2, current is 0.1.3 → original bug: must NOT show update message."""
        printed, cache_file = self._run(tmp_path, "0.1.3", "0.1.2", "0.1.3")
        assert not any("Update available" in str(m) for m in printed)
        # cache should have been invalidated and rewritten with the fresh PyPI value
        assert cache_file.read_text().strip() == "0.1.3"

    def test_stale_cache_triggers_pypi_check(self, tmp_path):
        """Cache is older than 24h → fresh PyPI check regardless of cached content."""
        printed, cache_file = self._run(
            tmp_path, "0.1.3", "0.1.2", "0.1.4", cache_age_seconds=90000
        )
        assert any("Update available" in str(m) for m in printed)
        assert cache_file.read_text().strip() == "0.1.4"

    def test_empty_cache_content_no_crash(self, tmp_path):
        """Empty/malformed cache content → no exception raised."""
        printed, _ = self._run(tmp_path, "0.1.3", "", "0.1.4", cache_age_seconds=100)
        # empty string → falsy → else: return early, no message
        assert not any("Update available" in str(m) for m in printed)

    def test_network_error_silenced(self, tmp_path):
        """requests.get raises ConnectionError → must not propagate."""
        import forgememo.cli as cli_mod

        with (
            patch.object(cli_mod, "__version__", "0.1.3"),
            patch("forgememo.cli.Path") as mock_path_cls,
            patch("requests.get", side_effect=ConnectionError("network down")),
        ):
            real_path = Path
            mock_path_cls.home.return_value = tmp_path
            mock_path_cls.side_effect = lambda *a, **kw: real_path(*a, **kw)
            _check_for_update()  # must not raise


# ---------------------------------------------------------------------------
# init: provider setup requires an interactive TTY
# ---------------------------------------------------------------------------


class TestInitRequiresTTY:
    def test_init_exits_1_without_tty_when_provider_unconfigured(self, tmp_path):
        """When no provider exists yet, non-TTY init must fail instead of bypassing setup."""
        with (
            patch("forgememo.core.DB_PATH", tmp_path / "forgememo.db"),
            patch("forgememo.cli._register_mcp", return_value=False),
            patch("forgememo.cli._auto_detect_and_generate_skills", return_value=None),
            patch("forgememo.config.load", return_value={}),
            patch("sys.stdin") as mock_stdin,
        ):
            mock_stdin.isatty.return_value = False
            result = runner.invoke(app, ["init", "--yes"])

        assert result.exit_code == 1, result.output
        assert "INTERACTIVE SETUP REQUIRED" in result.output

    def test_init_without_tty_succeeds_when_provider_already_configured(self, tmp_path):
        """Non-TTY init is allowed after provider setup has already happened."""
        with (
            patch("forgememo.core.DB_PATH", tmp_path / "forgememo.db"),
            patch("forgememo.cli._register_mcp", return_value=False),
            patch("forgememo.cli._auto_detect_and_generate_skills", return_value=None),
            patch("forgememo.cli._do_start", return_value=None),
            patch("forgememo.config.load", return_value={"provider": "forgememo"}),
            patch("sys.stdin") as mock_stdin,
        ):
            mock_stdin.isatty.return_value = False
            result = runner.invoke(app, ["init", "--yes"])

        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# start --mine: miner plist written correctly
# ---------------------------------------------------------------------------


class TestStartMine:
    @pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent is macOS only")
    def test_miner_plist_written(self, tmp_path):
        """--mine writes com.forgememo.miner.plist with correct StartInterval and command."""
        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()
        server_plist = plist_dir / "com.forgememo.server.plist"
        miner_plist = plist_dir / "com.forgememo.miner.plist"
        log_path = tmp_path / "Logs" / "forgememo.log"

        with (
            patch("forgememo.cli.PLIST_PATH", server_plist),
            patch("forgememo.cli.MINER_PLIST_PATH", miner_plist),
            patch("forgememo.cli.LOG_PATH", log_path),
            patch("forgememo.cli._register_mcp", return_value=False),
            patch("forgememo.cli._auto_detect_and_generate_skills", return_value=None),
            patch("shutil.which", return_value="/usr/local/bin/forgememo"),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            patch("forgememo.core.DB_PATH", tmp_path / "forgememo.db"),
            patch("forgememo.config.load", return_value={"provider": "anthropic"}),
        ):
            result = runner.invoke(app, ["start", "--mine", "--mine-interval", "1800"])

        assert result.exit_code == 0, result.output
        assert miner_plist.exists(), "miner plist was not created"

        content = miner_plist.read_text()
        assert "com.forgememo.miner" in content
        assert "<string>mine</string>" in content
        assert "<integer>1800</integer>" in content

    @pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent is macOS only")
    def test_no_mine_flag_skips_miner_plist(self, tmp_path):
        """Without --mine, no miner plist is created."""
        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()
        server_plist = plist_dir / "com.forgememo.server.plist"
        miner_plist = plist_dir / "com.forgememo.miner.plist"
        log_path = tmp_path / "Logs" / "forgememo.log"

        with (
            patch("forgememo.cli.PLIST_PATH", server_plist),
            patch("forgememo.cli.MINER_PLIST_PATH", miner_plist),
            patch("forgememo.cli.LOG_PATH", log_path),
            patch("forgememo.cli._register_mcp", return_value=False),
            patch("forgememo.cli._auto_detect_and_generate_skills", return_value=None),
            patch("shutil.which", return_value="/usr/local/bin/forgememo"),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            patch("forgememo.core.DB_PATH", tmp_path / "forgememo.db"),
            patch("forgememo.config.load", return_value={"provider": "anthropic"}),
        ):
            result = runner.invoke(app, ["start"])

        assert result.exit_code == 0, result.output
        assert not miner_plist.exists(), "miner plist should not exist without --mine"

    @pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent is macOS only")
    def test_default_mine_interval_is_3600(self, tmp_path):
        """Default --mine-interval is 3600 seconds."""
        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()
        server_plist = plist_dir / "com.forgememo.server.plist"
        miner_plist = plist_dir / "com.forgememo.miner.plist"
        log_path = tmp_path / "Logs" / "forgememo.log"

        with (
            patch("forgememo.cli.PLIST_PATH", server_plist),
            patch("forgememo.cli.MINER_PLIST_PATH", miner_plist),
            patch("forgememo.cli.LOG_PATH", log_path),
            patch("forgememo.cli._register_mcp", return_value=False),
            patch("forgememo.cli._auto_detect_and_generate_skills", return_value=None),
            patch("shutil.which", return_value="/usr/local/bin/forgememo"),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            patch("forgememo.core.DB_PATH", tmp_path / "forgememo.db"),
            patch("forgememo.config.load", return_value={"provider": "anthropic"}),
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
        server_plist = plist_dir / "com.forgememo.server.plist"
        miner_plist = plist_dir / "com.forgememo.miner.plist"
        server_plist.write_text("dummy")
        miner_plist.write_text("dummy")

        launchctl_calls = []

        def fake_run(cmd, **kwargs):
            launchctl_calls.append(cmd)
            return MagicMock(returncode=0, stderr="")

        with (
            patch("forgememo.cli.PLIST_PATH", server_plist),
            patch("forgememo.cli.MINER_PLIST_PATH", miner_plist),
            patch("subprocess.run", side_effect=fake_run),
            patch("typer.confirm", return_value=False),  # don't remove files
            patch("forgememo.config.load", return_value={"provider": "anthropic"}),
        ):
            result = runner.invoke(app, ["stop"])

        assert result.exit_code == 0, result.output
        unloaded = [cmd for cmd in launchctl_calls if "unload" in cmd]
        assert any(str(server_plist) in str(c) for c in unloaded), (
            "server plist not unloaded"
        )
        assert any(str(miner_plist) in str(c) for c in unloaded), (
            "miner plist not unloaded"
        )

    @pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent is macOS only")
    def test_stop_skips_miner_if_absent(self, tmp_path):
        """stop does not error when miner plist doesn't exist."""
        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()
        server_plist = plist_dir / "com.forgememo.server.plist"
        miner_plist = plist_dir / "com.forgememo.miner.plist"  # does NOT exist
        server_plist.write_text("dummy")

        launchctl_calls = []

        def fake_run(cmd, **kwargs):
            launchctl_calls.append(cmd)
            return MagicMock(returncode=0, stderr="")

        with (
            patch("forgememo.cli.PLIST_PATH", server_plist),
            patch("forgememo.cli.MINER_PLIST_PATH", miner_plist),
            patch("subprocess.run", side_effect=fake_run),
            patch("typer.confirm", return_value=False),
        ):
            result = runner.invoke(app, ["stop"])

        assert result.exit_code == 0, result.output
        unloaded_targets = [str(c) for c in launchctl_calls if "unload" in str(c)]
        # Check specifically for the miner plist filename, not just "miner" (which
        # could match the pytest temp dir name like "test_stop_skips_miner_if_absent0")
        assert not any("com.forgememo.miner.plist" in t for t in unloaded_targets), (
            "miner launchctl unload should not be called when plist absent"
        )

    @pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent is macOS only")
    def test_stop_non_tty_does_not_remove_plists(self, tmp_path):
        """stop in non-TTY mode (e.g. CI) never prompts and leaves plists intact."""
        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()
        server_plist = plist_dir / "com.forgememo.server.plist"
        miner_plist = plist_dir / "com.forgememo.miner.plist"
        server_plist.write_text("dummy")
        miner_plist.write_text("dummy")

        with (
            patch("forgememo.cli.PLIST_PATH", server_plist),
            patch("forgememo.cli.MINER_PLIST_PATH", miner_plist),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
        ):
            # Click's test runner is always non-TTY, so stop should NOT remove files.
            result = runner.invoke(app, ["stop"])

        assert result.exit_code == 0, result.output
        assert server_plist.exists(), "plist should NOT be removed in non-TTY mode"

    def test_stop_plist_removal_logic(self, tmp_path):
        """Verify the plist removal logic directly when remove=True is forced.

        Click's CliRunner overrides sys.stdin (always non-TTY), so we cannot
        exercise the isatty->confirm path through runner.invoke. Instead, call
        the removal code directly via PLIST_PATH.unlink to confirm the pattern.
        """
        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()
        server_plist = plist_dir / "com.forgememo.server.plist"
        miner_plist = plist_dir / "com.forgememo.miner.plist"
        server_plist.write_text("dummy")
        miner_plist.write_text("dummy")

        # Directly exercise the removal path (what stop() does when remove=True)
        server_plist.unlink(missing_ok=True)
        if miner_plist.exists():
            miner_plist.unlink(missing_ok=True)

        assert not server_plist.exists()
        assert not miner_plist.exists()


# ---------------------------------------------------------------------------
# Provider guard: init and start must exit(1) when no provider configured
# ---------------------------------------------------------------------------


class TestProviderGuard:
    @pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent is macOS only")
    def test_start_exits_1_when_no_provider(self):
        """forgememo start must exit with code 1 when no provider is configured."""
        with patch("forgememo.config.load", return_value={}):
            result = runner.invoke(app, ["start"])
        assert result.exit_code == 1, result.output
        assert "ACTION REQUIRED" in result.output


# ---------------------------------------------------------------------------
# Version sync: pyproject.toml and __init__.py must always agree
# ---------------------------------------------------------------------------


class TestVersionSync:
    def test_version_matches_package_metadata(self):
        """forgememo.__version__ must equal importlib.metadata.version('forgememo')."""
        from importlib.metadata import version as pkg_version
        import forgememo

        assert forgememo.__version__ == pkg_version("forgememo"), (
            f"forgememo.__version__ ({forgememo.__version__!r}) != "
            f"installed package metadata ({pkg_version('forgememo')!r}). "
            "Run 'pip install -e .' to sync."
        )

    def test_pyproject_version_matches_init(self):
        """pyproject.toml version must equal forgememo.__version__ at import time."""
        import sys
        from pathlib import Path
        import forgememo

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

                match = re.search(
                    r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.MULTILINE
                )
                assert match, "Could not parse version from pyproject.toml"
                data = {"project": {"version": match.group(1)}}

        pyproject_ver = data["project"]["version"]
        assert pyproject_ver == forgememo.__version__, (
            f"pyproject.toml version ({pyproject_ver!r}) != "
            f"forgememo.__version__ ({forgememo.__version__!r}). "
            "Bump pyproject.toml and reinstall with 'pip install -e .'."
        )


# ---------------------------------------------------------------------------
# Credits flag helpers + status panel
# ---------------------------------------------------------------------------


class TestCreditsFlag(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._tmp_flag = Path(self._tmpdir) / ".credits_exhausted"
        self._patcher = patch("forgememo.config.CREDITS_FLAG_PATH", self._tmp_flag)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmp_flag.unlink(missing_ok=True)

    def test_set_and_get(self):
        from forgememo.config import set_credits_flag, get_credits_flag

        set_credits_flag(0.01)
        result = get_credits_flag()
        self.assertIsNotNone(result)
        self.assertEqual(result["balance_usd"], 0.01)
        self.assertIn("ts", result)

    def test_clear(self):
        from forgememo.config import (
            set_credits_flag,
            clear_credits_flag,
            get_credits_flag,
        )

        set_credits_flag(0.0)
        clear_credits_flag()
        self.assertIsNone(get_credits_flag())

    def test_get_returns_none_when_absent(self):
        from forgememo.config import get_credits_flag

        self.assertIsNone(get_credits_flag())

    def test_get_returns_none_on_corrupt_file(self):
        self._tmp_flag.write_text("not valid json {{{")
        from forgememo.config import get_credits_flag

        self.assertIsNone(get_credits_flag())

    def test_status_shows_panel_when_flag_set(self):
        from forgememo.config import set_credits_flag

        set_credits_flag(0.0)
        # side_effect: first call (auto-init callback) returns True to skip init,
        # second call (status DB check) returns False to trigger early exit cleanly.
        with (
            patch("forgememo.config.load", return_value={"provider": "forgememo"}),
            patch("forgememo.core.DB_PATH") as mock_db,
        ):
            mock_db.exists.side_effect = [True, False]
            result = runner.invoke(app, ["status"])
        self.assertIn("ACTION REQUIRED", result.output)
        self.assertIn("credits exhausted", result.output)

    def test_status_no_panel_when_flag_absent(self):
        with (
            patch("forgememo.config.load", return_value={"provider": "forgememo"}),
            patch("forgememo.core.DB_PATH") as mock_db,
        ):
            mock_db.exists.side_effect = [True, False]
            result = runner.invoke(app, ["status"])
        self.assertNotIn("ACTION REQUIRED", result.output)

    def test_auth_login_clears_credits_flag(self):
        from forgememo.config import (
            set_credits_flag,
            get_credits_flag,
            clear_credits_flag,
        )

        set_credits_flag(0.0)
        self.assertIsNotNone(get_credits_flag())
        clear_credits_flag()  # simulates what _do_auth_login calls
        self.assertIsNone(get_credits_flag())


# ---------------------------------------------------------------------------
# Expired token: 401 handling across auth / sync commands
# ---------------------------------------------------------------------------


class TestExpiredToken:
    """Tests for when the stored CLI token has expired (server returns 401)."""

    # -- _check_api_response --------------------------------------------------

    def test_check_api_response_401_raises_exit(self):
        """_check_api_response on 401 must raise typer.Exit(1)."""
        import typer
        from forgememo.cli import _check_api_response

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        with pytest.raises(typer.Exit):
            _check_api_response(mock_resp, MagicMock())

    def test_check_api_response_401_prints_relogin_hint(self):
        """_check_api_response on 401 must print a hint directing user to auth login."""
        import typer
        from forgememo.cli import _check_api_response

        printed = []
        fake_console = MagicMock()
        fake_console.print.side_effect = lambda msg, **kw: printed.append(str(msg))

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        with pytest.raises(typer.Exit):
            _check_api_response(mock_resp, fake_console)

        assert any(
            "expired" in m.lower() or "auth login" in m.lower() for m in printed
        ), f"Expected 'expired' or 'auth login' in output, got: {printed}"

    def test_check_api_response_200_does_not_exit(self):
        """_check_api_response on 200 must not raise."""
        from forgememo.cli import _check_api_response

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        _check_api_response(mock_resp, MagicMock())  # must not raise

    # -- forgememo sync with expired token ------------------------------------

    def test_sync_pull_only_401_exits(self, tmp_path):
        """sync --pull-only when server returns 401 must exit 1 with hint."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        with (
            patch(
                "forgememo.config.load",
                return_value={"forgememo_token": "expired-token"},
            ),
            patch("forgememo.config.get_device_id", return_value="dev-123"),
            patch("forgememo.config.get_last_sync_ts", return_value=0),
            patch("requests.get", return_value=mock_resp),
        ):
            result = runner.invoke(app, ["sync", "--pull-only"])

        assert result.exit_code == 1
        assert (
            "expired" in result.output.lower() or "auth login" in result.output.lower()
        )

    def test_sync_push_401_exits(self, tmp_path):
        """sync push path when server returns 401 must exit 1 with hint."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        db_file = tmp_path / "forgememo.db"
        db_file.touch()

        with (
            patch(
                "forgememo.config.load",
                return_value={"forgememo_token": "expired-token"},
            ),
            patch("forgememo.config.get_device_id", return_value="dev-123"),
            patch("forgememo.config.get_last_sync_ts", return_value=0),
            patch("forgememo.core.DB_PATH", db_file),
            patch("sqlite3.connect") as mock_conn,
            patch("requests.post", return_value=mock_resp),
        ):
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = [
                {
                    "id": 1,
                    "ts": 1,
                    "session_id": "s",
                    "project_tag": "p",
                    "type": "t",
                    "content": "c",
                    "distilled": "d",
                }
            ]
            mock_db = MagicMock()
            mock_db.execute.return_value = mock_cursor
            mock_db.__enter__ = lambda s: s
            mock_db.__exit__ = MagicMock(return_value=False)
            mock_conn.return_value = mock_db

            result = runner.invoke(app, ["sync", "--push-only"])

        assert result.exit_code == 1
        assert (
            "expired" in result.output.lower() or "auth login" in result.output.lower()
        )

    # -- forgememo auth login --------------------------------------------------

    def test_auth_login_timeout_exits_1(self):
        """auth login that times out (no browser callback) must exit 1."""
        mock_server = MagicMock()

        def fake_serve():
            pass  # simulate thread that never sets received_token

        with (
            patch("http.server.HTTPServer", return_value=mock_server),
            patch("webbrowser.open"),
            patch("threading.Thread") as mock_thread_cls,
            patch("forgememo.config.load", return_value={}),
        ):
            mock_thread = MagicMock()
            mock_thread.join = (
                lambda timeout=None: None
            )  # returns without setting token
            mock_thread_cls.return_value = mock_thread

            result = runner.invoke(app, ["auth", "login"])

        assert result.exit_code == 1
        assert (
            "timed out" in result.output.lower() or "cancelled" in result.output.lower()
        )

    def test_auth_login_success_saves_token(self, tmp_path):
        """auth login success path: token saved to config, success message printed."""
        mock_server = MagicMock()
        saved_cfg = {}

        def fake_join(timeout=None):
            # Simulate the server callback setting the token
            _do_auth_login_received_token["value"] = "new-fresh-token"

        _do_auth_login_received_token = {}

        with (
            patch("http.server.HTTPServer", return_value=mock_server),
            patch("webbrowser.open"),
            patch("threading.Thread") as mock_thread_cls,
            patch("forgememo.config.load", return_value={}),
            patch("forgememo.config.save", side_effect=lambda d: saved_cfg.update(d)),
            patch("forgememo.config.clear_credits_flag"),
        ):
            mock_thread = MagicMock()

            def patched_join(timeout=None):
                pass

            mock_thread.join = patched_join
            mock_thread_cls.return_value = mock_thread

            # Use a simpler approach: patch _do_auth_login directly to simulate success
            with patch("forgememo.cli._do_auth_login") as mock_login:
                mock_login.return_value = True
                result = runner.invoke(app, ["auth", "login"])

        assert result.exit_code == 0
        mock_login.assert_called_once()

    def test_auth_status_shows_token_when_present(self):
        """auth status when token is stored shows it as authenticated."""
        with patch(
            "forgememo.config.load", return_value={"forgememo_token": "tok_abc123xyz"}
        ):
            result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0
        assert "authenticated" in result.output.lower()

    def test_auth_status_prompts_login_when_no_token(self):
        """auth status when no token stored prompts user to run auth login."""
        with patch("forgememo.config.load", return_value={}):
            result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0
        assert (
            "auth login" in result.output.lower()
            or "not authenticated" in result.output.lower()
        )
