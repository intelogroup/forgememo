import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "test.db")
    d.init()
    return d


def test_create_user_and_get(db):
    uid = db.create_user("test@example.com")
    user = db.get_user_by_email("test@example.com")
    assert user["id"] == uid
    assert user["email"] == "test@example.com"
    assert user["balance_usd"] == 5.0


def test_deduct_credits(db):
    uid = db.create_user("pay@example.com")
    new_balance = db.deduct_credits(uid, 0.02)
    assert abs(new_balance - 4.98) < 0.0001


def test_deduct_credits_insufficient(db):
    uid = db.create_user("broke@example.com")
    with pytest.raises(ValueError, match="Insufficient"):
        db.deduct_credits(uid, 100.00)


def test_top_up_credits(db):
    uid = db.create_user("topup@example.com")
    new_balance = db.top_up_credits(uid, 10.00)
    assert abs(new_balance - 15.00) < 0.0001


def test_log_run_and_count(db):
    uid = db.create_user("runner@example.com")
    db.log_run(uid, "run_abc", 0.02, "claude-haiku-4-5-20251001", 4.98)
    count = db.run_count_in_window(uid, window_seconds=3600)
    assert count == 1


def test_create_session_and_get_user(db):
    uid = db.create_user("sess@example.com")
    db.create_session("token123", uid)
    user = db.get_user_by_session("token123")
    assert user["id"] == uid


def test_magic_link_consume_once(db):
    db.create_user("ml@example.com")
    db.create_magic_link_token("tok1", "ml@example.com", "http://127.0.0.1:9000/cb", "state1")
    link = db.consume_magic_link_token("tok1")
    assert link is not None
    assert link["email"] == "ml@example.com"
    # Second consume returns None (already used)
    assert db.consume_magic_link_token("tok1") is None


def test_stripe_event_idempotency(db):
    assert db.stripe_event_seen("evt_abc") is False
    assert db.stripe_event_seen("evt_abc") is True
