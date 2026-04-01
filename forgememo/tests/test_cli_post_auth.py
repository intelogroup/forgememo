"""Tests for _do_post_auth_setup — CLI loopback event listener after login."""
import sys
import time
import threading
import urllib.parse
from pathlib import Path
from unittest.mock import patch, MagicMock

import requests as http_requests
import requests as _real_requests  # noqa: E401
import pytest
import http.server

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

_REAL_GET = _real_requests.get


def _wait_for_open(opened_urls: list, timeout: float = 3.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if opened_urls:
            return opened_urls[0]
        time.sleep(0.05)
    raise TimeoutError("webbrowser.open was never called")


def _extract_event_url(login_url: str) -> str:
    parsed = urllib.parse.urlparse(login_url)
    params = urllib.parse.parse_qs(parsed.query)
    callback = params["cli_callback"][0]
    return urllib.parse.unquote(callback)


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


def _balance_mock(balance: float):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"balance_usd": balance}
    return resp


def _make_balance_side_effect(balance: float):
    """Return a side_effect that mocks balance API calls but passes loopback calls through."""
    mock_resp = _balance_mock(balance)

    def _side_effect(url, **kwargs):
        if "127.0.0.1" in url:
            return _REAL_GET(url, **kwargs)
        return mock_resp

    return _side_effect


def test_setup_skipped_when_balance_sufficient():
    """If balance > 2.0 USD, setup is skipped and browser is never opened."""
    from forgememo.commands import configure as fm_cli
    opened_urls: list = []

    with (
        patch("webbrowser.open", side_effect=opened_urls.append),
        patch("forgememo.commands.configure.console"),
        patch("requests.get", side_effect=_make_balance_side_effect(3.5)),
        patch("forgememo.commands.configure._POST_AUTH_TIMEOUT", 2),
    ):
        result = fm_cli._do_post_auth_setup("fake.jwt.token")

    assert result == []
    assert len(opened_urls) == 0


def test_setup_opens_browser_when_balance_low():
    """If balance <= 2.0 USD, browser is opened to billing setup page."""
    from forgememo.commands import configure as fm_cli
    opened_urls: list = []

    with (
        patch("webbrowser.open", side_effect=opened_urls.append),
        patch("forgememo.commands.configure.console"),
        patch("requests.get", side_effect=_make_balance_side_effect(0.5)),
        patch("forgememo.commands.configure._POST_AUTH_TIMEOUT", 2),
    ):
        def run():
            fm_cli._do_post_auth_setup("fake.jwt.token")

        t = threading.Thread(target=run, daemon=True)
        t.start()
        _wait_for_open(opened_urls)
        t.join(timeout=10)

    assert "billing/cli-setup" in opened_urls[0]


def test_setup_receives_both_events():
    """Loopback server collects card_added and credits_added when state matches."""
    from forgememo.commands import configure as fm_cli
    opened_urls: list = []
    result: dict = {}

    with (
        patch("webbrowser.open", side_effect=opened_urls.append),
        patch("forgememo.commands.configure.console"),
        patch("requests.get", side_effect=_make_balance_side_effect(0.0)),
        patch("forgememo.commands.configure._POST_AUTH_TIMEOUT", 10),
    ):
        def run():
            result["events"] = fm_cli._do_post_auth_setup("fake.jwt.token")

        t = threading.Thread(target=run, daemon=True)
        t.start()

        login_url = _wait_for_open(opened_urls)
        parsed = urllib.parse.urlparse(login_url)
        state = urllib.parse.parse_qs(parsed.query)["state"][0]
        event_url = _extract_event_url(login_url)

        http_requests.get(event_url, params={"type": "card_added", "state": state})
        http_requests.get(event_url, params={"type": "credits_added", "state": state, "amount": "5.0"})

        t.join(timeout=15)

    events = result.get("events", [])
    assert any(e.get("type") == "card_added" for e in events)
    assert any(e.get("type") == "credits_added" for e in events)


def test_setup_rejects_wrong_state():
    """Loopback server returns 400 for events with wrong state."""
    from forgememo.commands import configure as fm_cli
    opened_urls: list = []

    with (
        patch("webbrowser.open", side_effect=opened_urls.append),
        patch("forgememo.commands.configure.console"),
        patch("requests.get", side_effect=_make_balance_side_effect(0.0)),
        patch("forgememo.commands.configure._POST_AUTH_TIMEOUT", 2),
    ):
        def run():
            fm_cli._do_post_auth_setup("fake.jwt.token")

        t = threading.Thread(target=run, daemon=True)
        t.start()
        login_url = _wait_for_open(opened_urls)
        event_url = _extract_event_url(login_url)

        resp = http_requests.get(event_url, params={"type": "card_added", "state": "WRONG"})
        assert resp.status_code == 400

        t.join(timeout=10)


def test_setup_returns_empty_on_timeout():
    """Returns [] gracefully if no events arrive within timeout."""
    from forgememo.commands import configure as fm_cli
    opened_urls: list = []
    result: dict = {}

    with (
        patch("webbrowser.open", side_effect=opened_urls.append),
        patch("forgememo.commands.configure.console"),
        patch("requests.get", side_effect=_make_balance_side_effect(0.0)),
        patch("forgememo.commands.configure._POST_AUTH_TIMEOUT", 2),
    ):
        def run():
            result["events"] = fm_cli._do_post_auth_setup("fake.jwt.token")

        t = threading.Thread(target=run, daemon=True)
        t.start()
        _wait_for_open(opened_urls)
        t.join(timeout=10)

    assert result.get("events") == []
