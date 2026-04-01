from __future__ import annotations

import importlib
import os
import tempfile


def test_daemon_log_fallback_to_tmp(monkeypatch):
    """When FORGEMEMO_DAEMON_LOG points to an unwritable path and
    FORGEMEMO_ALLOW_TMP_LOG=1, the daemon falls back to the system temp dir."""
    monkeypatch.setenv("FORGEMEMO_DAEMON_LOG", "/some/unwritable/path/daemon.log")
    monkeypatch.setenv("FORGEMEMO_ALLOW_TMP_LOG", "1")

    # Force os.makedirs to raise OSError for the configured path so the fallback
    # branch is exercised regardless of which user/OS the tests run under.
    import os as _os
    _orig_makedirs = _os.makedirs

    def _makedirs(path, **kwargs):
        if "unwritable" in str(path):
            raise OSError("permission denied (mocked)")
        return _orig_makedirs(path, **kwargs)

    monkeypatch.setattr(_os, "makedirs", _makedirs)

    import forgememo.daemon as daemon

    importlib.reload(daemon)

    expected = os.path.join(tempfile.gettempdir(), "forgememo_daemon.log")
    assert daemon.LOG_FILE == expected
