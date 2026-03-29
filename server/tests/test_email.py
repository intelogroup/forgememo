import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["RESEND_API_KEY"] = "re_test_key"

import pytest
from email_sender import send_magic_link


def test_send_magic_link_calls_resend():
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        send_magic_link(
            to="user@example.com",
            magic_url="https://api.forgemem.com/cli-auth/verify?token=abc&callback=http://127.0.0.1:9000/cb&state=xyz",
        )
        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs["json"]
        assert body["to"] == ["user@example.com"]
        assert "abc" in body["html"]


def test_send_magic_link_raises_on_failure():
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Unauthorized"

    with patch("httpx.post", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="Resend"):
            send_magic_link("u@x.com", "https://api.forgemem.com/verify?token=x")
