import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test"

import pytest
from billing import create_checkout_session, parse_webhook_event, CREDIT_PACKS


def test_credit_packs_defined():
    assert len(CREDIT_PACKS) >= 1
    pack = list(CREDIT_PACKS.values())[0]
    assert "price_id" in pack
    assert "credit_usd" in pack


def test_create_checkout_session_calls_stripe():
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/pay/cs_test_abc"

    with patch("stripe.checkout.Session.create", return_value=mock_session) as mock_create:
        url = create_checkout_session(
            user_id="user123",
            pack_id="starter",
            success_url="https://app.forgemem.com/billing/success",
            cancel_url="https://app.forgemem.com/billing",
        )
        assert url == "https://checkout.stripe.com/pay/cs_test_abc"
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["metadata"]["user_id"] == "user123"


def test_unknown_pack_raises():
    with pytest.raises(ValueError, match="Unknown pack_id"):
        create_checkout_session("u", "nonexistent", "https://s", "https://c")


def test_parse_webhook_event_returns_amount_and_user():
    mock_event = {
        "id": "evt_test_123",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {"user_id": "user123", "credit_usd": "10.0"},
                "payment_status": "paid",
            }
        },
    }
    with patch("stripe.Webhook.construct_event", return_value=mock_event):
        result = parse_webhook_event(payload=b"body", sig="sig_header")
        assert result["user_id"] == "user123"
        assert result["credit_usd"] == 10.0
        assert result["event_id"] == "evt_test_123"


def test_parse_webhook_event_skips_unpaid():
    mock_event = {
        "id": "evt_test_456",
        "type": "checkout.session.completed",
        "data": {"object": {"metadata": {"user_id": "u", "credit_usd": "5.0"}, "payment_status": "unpaid"}},
    }
    with patch("stripe.Webhook.construct_event", return_value=mock_event):
        result = parse_webhook_event(payload=b"body", sig="sig")
        assert result is None


def test_parse_webhook_event_ignores_other_events():
    mock_event = {"id": "evt_789", "type": "customer.created", "data": {"object": {}}}
    with patch("stripe.Webhook.construct_event", return_value=mock_event):
        result = parse_webhook_event(payload=b"body", sig="sig")
        assert result is None
