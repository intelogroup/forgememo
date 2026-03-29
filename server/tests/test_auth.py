import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["FORGEMEM_JWT_SECRET"] = "testsecret_64chars_padding_padding_padding_padding_padding_pad"

import pytest
from auth import create_session_token, verify_session_token, create_magic_link_token


def test_round_trip():
    token = create_session_token("user123")
    payload = verify_session_token(token)
    assert payload["sub"] == "user123"


def test_expired_token_raises():
    token = create_session_token("user123", ttl_seconds=-1)
    with pytest.raises(ValueError, match="expired"):
        verify_session_token(token)


def test_invalid_token_raises():
    with pytest.raises(ValueError):
        verify_session_token("not.a.jwt")


def test_magic_link_token_length():
    tok = create_magic_link_token()
    assert len(tok) >= 32
