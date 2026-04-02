"""
Tests for forgememo.port — daemon port discovery (ENV > lockfile > default).
"""

import socket
from unittest.mock import patch


class TestReadPort:
    """Test the three-tier precedence chain."""

    def test_env_var_wins(self, tmp_path, monkeypatch):
        """FORGEMEMO_HTTP_PORT env var takes priority over lockfile and default."""
        port_file = tmp_path / "daemon.port"
        port_file.write_text("9999")
        monkeypatch.setenv("FORGEMEMO_HTTP_PORT", "8888")

        from forgememo import port as port_module

        with patch.object(port_module, "PORT_FILE", port_file):
            result = port_module.read_port()

        assert result == 8888

    def test_lockfile_used_when_no_env(self, tmp_path, monkeypatch):
        """Lockfile port is used when env var is absent and port is listening."""
        monkeypatch.delenv("FORGEMEMO_HTTP_PORT", raising=False)

        # Find a free port and start a listener so _port_listening returns True
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        free_port = server.getsockname()[1]
        server.listen(1)

        port_file = tmp_path / "daemon.port"
        port_file.write_text(str(free_port))

        try:
            from forgememo import port as port_module

            with patch.object(port_module, "PORT_FILE", port_file):
                result = port_module.read_port()
        finally:
            server.close()

        assert result == free_port

    def test_lockfile_skipped_when_port_not_listening(self, tmp_path, monkeypatch):
        """Stale lockfile (port not listening) falls through to default."""
        monkeypatch.delenv("FORGEMEMO_HTTP_PORT", raising=False)

        port_file = tmp_path / "daemon.port"
        port_file.write_text("19998")  # nothing listening there

        from forgememo import port as port_module

        with patch.object(port_module, "PORT_FILE", port_file):
            with patch.object(port_module, "_DEFAULT_PORT", 5555):
                result = port_module.read_port()

        assert result == 5555

    def test_missing_lockfile_returns_default(self, tmp_path, monkeypatch):
        """Missing lockfile returns the default port."""
        monkeypatch.delenv("FORGEMEMO_HTTP_PORT", raising=False)

        from forgememo import port as port_module

        with patch.object(port_module, "PORT_FILE", tmp_path / "daemon.port"):
            with patch.object(port_module, "_DEFAULT_PORT", 5555):
                result = port_module.read_port()

        assert result == 5555

    def test_invalid_lockfile_content_returns_default(self, tmp_path, monkeypatch):
        """Lockfile with non-integer content falls through to default."""
        monkeypatch.delenv("FORGEMEMO_HTTP_PORT", raising=False)

        port_file = tmp_path / "daemon.port"
        port_file.write_text("not-a-port")

        from forgememo import port as port_module

        with patch.object(port_module, "PORT_FILE", port_file):
            with patch.object(port_module, "_DEFAULT_PORT", 5555):
                result = port_module.read_port()

        assert result == 5555

    def test_env_var_invalid_falls_through(self, tmp_path, monkeypatch):
        """Invalid env var value falls through to lockfile/default."""
        monkeypatch.setenv("FORGEMEMO_HTTP_PORT", "not-a-number")

        from forgememo import port as port_module

        with patch.object(port_module, "PORT_FILE", tmp_path / "daemon.port"):
            with patch.object(port_module, "_DEFAULT_PORT", 5555):
                result = port_module.read_port()

        assert result == 5555


class TestWriteDeletePort:
    """Test lockfile write/delete lifecycle."""

    def test_write_creates_file(self, tmp_path):
        """write_port creates the lockfile with the port number."""
        from forgememo import port as port_module

        port_file = tmp_path / "daemon.port"
        with patch.object(port_module, "PORT_FILE", port_file):
            with patch.object(port_module, "_FORGEMEMO_DIR", tmp_path):
                port_module.write_port(7777)

        assert port_file.read_text().strip() == "7777"

    def test_write_is_atomic(self, tmp_path):
        """write_port uses a temp file + rename for atomicity."""
        from forgememo import port as port_module

        port_file = tmp_path / "daemon.port"
        with patch.object(port_module, "PORT_FILE", port_file):
            with patch.object(port_module, "_FORGEMEMO_DIR", tmp_path):
                port_module.write_port(1234)
                # No .port.tmp file should remain
                assert not (tmp_path / "daemon.port.tmp").exists()

        assert port_file.exists()

    def test_delete_removes_file(self, tmp_path):
        """delete_port removes the lockfile."""
        from forgememo import port as port_module

        port_file = tmp_path / "daemon.port"
        port_file.write_text("5555")

        with patch.object(port_module, "PORT_FILE", port_file):
            port_module.delete_port()

        assert not port_file.exists()

    def test_delete_is_idempotent(self, tmp_path):
        """delete_port does not raise if the file is already gone."""
        from forgememo import port as port_module

        with patch.object(port_module, "PORT_FILE", tmp_path / "daemon.port"):
            port_module.delete_port()  # file doesn't exist — should not raise

    def test_write_overwrites_existing(self, tmp_path):
        """write_port replaces an existing lockfile."""
        from forgememo import port as port_module

        port_file = tmp_path / "daemon.port"
        port_file.write_text("1111")

        with patch.object(port_module, "PORT_FILE", port_file):
            with patch.object(port_module, "_FORGEMEMO_DIR", tmp_path):
                port_module.write_port(2222)

        assert port_file.read_text().strip() == "2222"
