"""Tests for the CLI loopback callback handler in _do_auth_login.

The CLI starts an HTTP server on 127.0.0.1:<ephemeral>. When the user completes
browser auth, the server redirects to that loopback with:
  GET /callback?token=<JWT>&state=<state>

These tests verify:
  - Valid state + token → 200, token stored to config
  - Wrong state → 400, token NOT stored
  - Missing token param → 400, token NOT stored
"""
import sys
import time
import threading
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import requests as http_requests
import pytest
import http.server

# Add project root so forgememo package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

def _wait_for_open(opened_urls: list, timeout: float = 3.0) -> str:
    """Wait until webbrowser.open has been called, return the URL."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if opened_urls:
            return opened_urls[0]
        time.sleep(0.05)
    raise TimeoutError("webbrowser.open was never called — server may not have started")


def _extract_state(login_url: str) -> str:
    parsed = urllib.parse.urlparse(login_url)
    params = urllib.parse.parse_qs(parsed.query)
    return params["state"][0]


def _extract_callback_base(login_url: str) -> str:
    parsed = urllib.parse.urlparse(login_url)
    params = urllib.parse.parse_qs(parsed.query)
    callback = params["callback"][0]
    cb = urllib.parse.urlparse(callback)
    return f"{cb.scheme}://{cb.netloc}"


def _can_bind_loopback() -> bool:
    try:
        server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
        server.server_close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _can_bind_loopback(),
    reason="Loopback HTTP server not permitted in this environment",
)


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

def test_handler_accepts_valid_state_and_stores_token():
    """Loopback server returns 200 and saves JWT to config when state matches."""
    from forgememo.commands import configure as fm_cli
    from forgememo import config as fm_cfg

    saved: dict = {}
    opened_urls: list = []

    with (
        patch("webbrowser.open", side_effect=opened_urls.append),
        patch.object(fm_cfg, "load", return_value={}),
        patch.object(fm_cfg, "save", side_effect=saved.update),
        patch.object(fm_cfg, "clear_credits_flag"),
        patch("forgememo.commands.configure.console"),
    ):
        result: dict = {}

        def run():
            try:
                result["ok"] = fm_cli._do_auth_login()
            except Exception as e:
                result["error"] = e

        t = threading.Thread(target=run, daemon=True)
        t.start()

        login_url = _wait_for_open(opened_urls)
        state = _extract_state(login_url)
        callback_base = _extract_callback_base(login_url)

        fake_jwt = "header.payload.signature"
        resp = http_requests.get(
            f"{callback_base}/callback",
            params={"token": fake_jwt, "state": state},
        )
        assert resp.status_code == 200

        t.join(timeout=5)

    assert saved.get("forgememo_token") == fake_jwt
    assert saved.get("provider") == "forgememo"
    assert result.get("ok") is True


# ---------------------------------------------------------------------------
# Rejection paths
# ---------------------------------------------------------------------------

def test_handler_rejects_wrong_state():
    """Loopback server returns 400 and does NOT save a token when state is wrong."""
    from forgememo.commands import configure as fm_cli
    from forgememo import config as fm_cfg

    saved: dict = {}
    opened_urls: list = []

    with (
        patch("webbrowser.open", side_effect=opened_urls.append),
        patch.object(fm_cfg, "load", return_value={}),
        patch.object(fm_cfg, "save", side_effect=saved.update),
        patch.object(fm_cfg, "clear_credits_flag"),
        patch("forgememo.commands.configure.console"),
    ):
        result: dict = {}

        def run():
            try:
                result["ok"] = fm_cli._do_auth_login()
            except Exception as e:
                result["exited"] = e

        t = threading.Thread(target=run, daemon=True)
        t.start()

        login_url = _wait_for_open(opened_urls)
        callback_base = _extract_callback_base(login_url)

        resp = http_requests.get(
            f"{callback_base}/callback",
            params={"token": "some.jwt.token", "state": "WRONG_STATE"},
        )
        assert resp.status_code == 400

        t.join(timeout=5)  # server exits after handling one request

    assert "forgememo_token" not in saved


def test_handler_rejects_missing_token_param():
    """Loopback server returns 400 when token param is absent."""
    from forgememo.commands import configure as fm_cli
    from forgememo import config as fm_cfg

    saved: dict = {}
    opened_urls: list = []

    with (
        patch("webbrowser.open", side_effect=opened_urls.append),
        patch.object(fm_cfg, "load", return_value={}),
        patch.object(fm_cfg, "save", side_effect=saved.update),
        patch.object(fm_cfg, "clear_credits_flag"),
        patch("forgememo.commands.configure.console"),
    ):
        def run():
            try:
                fm_cli._do_auth_login()
            except Exception:
                pass

        t = threading.Thread(target=run, daemon=True)
        t.start()

        login_url = _wait_for_open(opened_urls)
        state = _extract_state(login_url)
        callback_base = _extract_callback_base(login_url)

        resp = http_requests.get(
            f"{callback_base}/callback",
            params={"state": state},  # no token
        )
        assert resp.status_code == 400

        t.join(timeout=5)

    assert "forgememo_token" not in saved
