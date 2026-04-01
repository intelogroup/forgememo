from __future__ import annotations

import importlib
import os
import tempfile


def test_daemon_log_fallback_to_tmp(monkeypatch):
    monkeypatch.setenv("FORGEMEMO_DAEMON_LOG", "/root/forgememo/forgememo_daemon.log")
    monkeypatch.setenv("FORGEMEMO_ALLOW_TMP_LOG", "1")

    import forgememo.daemon as daemon

    importlib.reload(daemon)

    expected = os.path.join(tempfile.gettempdir(), "forgememo_daemon.log")
    assert daemon.LOG_FILE == expected
