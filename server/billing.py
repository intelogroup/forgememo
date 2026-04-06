"""Stripe Checkout session creation and webhook parsing."""
from __future__ import annotations

import os

import stripe

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")


def _webhook_secret() -> str:
    """Read at call time so Render env-var updates take effect without rebuild."""
    return os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# Credit packs: pack_id → {price_id (Stripe), credit_usd, label}
# Create these price IDs in your Stripe dashboard as one-time payment prices.
CREDIT_PACKS: dict[str, dict] = {
    "starter": {
        "price_id": os.environ.get("STRIPE_PRICE_STARTER", "price_starter"),
        "credit_usd": 5.0,
        "label": "$5 — 1,000 distills",
    },
    "pro": {
        "price_id": os.environ.get("STRIPE_PRICE_PRO", "price_pro"),
        "credit_usd": 20.0,
        "label": "$20 — 4,000 distills",
    },
    "team": {
        "price_id": os.environ.get("STRIPE_PRICE_TEAM", "price_team"),
        "credit_usd": 50.0,
        "label": "$50 — 10,000 distills",
    },
}


def create_checkout_session(
    user_id: str,
    pack_id: str,
    success_url: str,
    cancel_url: str,
) -> str:
    """Create a Stripe Checkout session. Returns the hosted checkout URL."""
    pack = CREDIT_PACKS.get(pack_id)
    if not pack:
        raise ValueError(f"Unknown pack_id '{pack_id}'. Valid: {list(CREDIT_PACKS)}")

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": pack["price_id"], "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": user_id, "credit_usd": str(pack["credit_usd"])},
    )
    return session.url


def parse_webhook_event(payload: bytes, sig: str) -> dict | None:
    """Verify Stripe signature and parse the event.

    Returns {'event_id', 'user_id', 'credit_usd'} for successful payments,
    or None for non-payment events / unpaid sessions.
    Raises stripe.error.SignatureVerificationError on bad signature.
    """
    event = stripe.Webhook.construct_event(payload, sig, _webhook_secret())

    if event["type"] != "checkout.session.completed":
        return None

    session_obj = event["data"]["object"]
    # stripe v8+: StripeObject no longer inherits from dict; use [] or getattr
    if session_obj["payment_status"] != "paid":
        return None

    meta = session_obj["metadata"]
    user_id = meta["user_id"] if "user_id" in meta else None
    credit_usd = meta["credit_usd"] if "credit_usd" in meta else None

    if not user_id or not credit_usd:
        return None

    return {
        "event_id": event["id"],
        "user_id": user_id,
        "credit_usd": float(credit_usd),
    }
