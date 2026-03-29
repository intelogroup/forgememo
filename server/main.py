"""
Forgemem Managed Inference API — v0.4

Endpoints:
  POST /v1/inference            — run a prompt, deduct credits
  GET  /v1/balance              — get current credit balance
  POST /v1/checkout             — create Stripe Checkout session
  POST /webhooks/stripe         — Stripe webhook (top up credits on payment)
  POST /v1/sync/push            — push local traces/principles to cloud
  GET  /v1/sync/pull            — pull traces/principles from other devices
  GET  /cli-auth                — browser landing page (renders email form)
  POST /cli-auth/send-link      — send magic link email
  GET  /cli-auth/verify         — verify magic link token, issue JWT, redirect to CLI
"""
from __future__ import annotations

import os
import re
import secrets
import urllib.parse as _urlparse
from datetime import datetime, timezone
from typing import Annotated, Any

import anthropic
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from auth import create_magic_link_token, create_session_token, verify_session_token
from billing import CREDIT_PACKS, create_checkout_session, parse_webhook_event
from db import Database
from usage import RateLimitExceeded, check_rate_limit

app = FastAPI(title="Forgemem Inference API", version="0.4.0")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

_WEBAPP_ORIGIN = os.getenv("WEBAPP_ORIGIN", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_WEBAPP_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PLATFORM_FEE_USD  = float(os.environ.get("PLATFORM_FEE_USD", "0.02"))
FREE_CREDIT_USD   = float(os.environ.get("FREE_CREDIT_USD", "5.0"))
API_BASE_URL      = os.environ.get("API_BASE_URL", "https://api.forgemem.com")

db = Database()
db.init()

_CLI_CALLBACK_RE = re.compile(r"^http://127\.0\.0\.1:\d{4,5}/\S*$")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class InferenceRequest(BaseModel):
    prompt: str
    max_tokens: int = 300
    model: str = "claude-haiku-4-5-20251001"


class InferenceResponse(BaseModel):
    text: str
    cost_usd: float
    balance_usd: float
    run_id: str


class CheckoutRequest(BaseModel):
    pack_id: str = "starter"
    success_url: str = "https://app.forgemem.com/billing/success"
    cancel_url: str = "https://app.forgemem.com/billing"


class SyncPushRequest(BaseModel):
    device_id: str
    device_name: str = ""
    traces: list[dict[str, Any]] = []
    principles: list[dict[str, Any]] = []


class WebappSendLinkRequest(BaseModel):
    email: str
    callback_url: str




# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_user(authorization: str) -> dict:
    """Verify JWT. Returns billing_user_dict."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    try:
        payload = verify_session_token(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    user_id = payload["sub"]
    billing_user = db.get_user_by_id(user_id)
    if billing_user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return billing_user


def _estimate_cost(prompt: str, max_tokens: int, model: str) -> float:
    input_tokens = len(prompt) / 4
    input_cost  = (input_tokens / 1_000_000) * 0.25
    output_cost = (max_tokens  / 1_000_000) * 1.25
    return round(input_cost + output_cost + PLATFORM_FEE_USD, 6)


def _validate_cli_callback(callback: str) -> None:
    if not callback or not _CLI_CALLBACK_RE.match(callback):
        raise HTTPException(
            status_code=400,
            detail="Invalid callback: must be a loopback address (http://127.0.0.1:<port>/...)",
        )


def _validate_webapp_callback(url: str) -> bool:
    """Callback must start with the configured WEBAPP_ORIGIN."""
    return url.startswith(_WEBAPP_ORIGIN + "/") or url == _WEBAPP_ORIGIN



def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Inference + billing routes
# ---------------------------------------------------------------------------

@app.post("/v1/inference", response_model=InferenceResponse)
async def inference(body: InferenceRequest, authorization: Annotated[str, Header()]):
    billing_user = _auth_user(authorization)

    try:
        check_rate_limit(db, billing_user["id"])
    except RateLimitExceeded as e:
        raise HTTPException(status_code=429, detail=str(e))

    estimated_cost = _estimate_cost(body.prompt, body.max_tokens, body.model)
    if billing_user["balance_usd"] < estimated_cost:
        raise HTTPException(
            status_code=402,
            detail={"message": "Insufficient credits", "balance_usd": billing_user["balance_usd"]},
        )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=body.model,
        max_tokens=body.max_tokens,
        messages=[{"role": "user", "content": body.prompt}],
    )
    text = response.content[0].text.strip()

    try:
        new_balance = db.deduct_credits(billing_user["id"], estimated_cost)
    except ValueError:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    run_id = secrets.token_hex(8)
    db.log_run(billing_user["id"], run_id, estimated_cost, body.model, new_balance)

    return InferenceResponse(
        text=text, cost_usd=estimated_cost, balance_usd=new_balance, run_id=run_id,
    )


@app.get("/v1/balance")
async def balance(authorization: Annotated[str, Header()]):
    billing_user = _auth_user(authorization)
    return {"balance_usd": billing_user["balance_usd"]}


@app.post("/v1/checkout")
async def checkout(body: CheckoutRequest, authorization: Annotated[str, Header()]):
    billing_user = _auth_user(authorization)
    try:
        url = create_checkout_session(
            user_id=billing_user["id"],
            pack_id=body.pack_id,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"checkout_url": url, "packs": CREDIT_PACKS}


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        result = parse_webhook_event(payload, sig)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    if result is None:
        return {"status": "ignored"}
    if db.stripe_event_seen(result["event_id"]):
        return {"status": "duplicate"}

    try:
        db.top_up_credits(result["user_id"], result["credit_usd"])
    except ValueError:
        raise HTTPException(status_code=404, detail="User not found for this payment")
    return {"status": "ok", "credit_usd": result["credit_usd"]}


# ---------------------------------------------------------------------------
# Sync routes
# ---------------------------------------------------------------------------

@app.post("/v1/sync/push")
async def sync_push(body: SyncPushRequest, authorization: Annotated[str, Header()]):
    """Push local traces + principles to cloud store."""
    billing_user = _auth_user(authorization)
    user_id = billing_user["id"]

    db.upsert_device(user_id, body.device_id, body.device_name)

    pushed_traces = 0
    for t in body.traces:
        db.upsert_trace(user_id, body.device_id, t)
        pushed_traces += 1

    pushed_principles = 0
    for p in body.principles:
        db.upsert_principle(user_id, body.device_id, p)
        pushed_principles += 1

    return {"pushed_traces": pushed_traces, "pushed_principles": pushed_principles}


@app.get("/v1/sync/pull")
async def sync_pull(
    authorization: Annotated[str, Header()],
    since: int = 0,
    device_id: str = "",
):
    """Pull traces + principles from all OTHER devices since `since` unix timestamp."""
    billing_user = _auth_user(authorization)
    user_id = billing_user["id"]

    traces     = db.pull_traces(user_id, since=since, exclude_device=device_id)
    principles = db.pull_principles(user_id, since=since, exclude_device=device_id)

    return {"traces": traces, "principles": principles, "server_ts": _now_iso()}


# ---------------------------------------------------------------------------
# Webapp auth routes (magic link — no loopback validation)
# ---------------------------------------------------------------------------

@app.post("/webapp-auth/send-link")
async def webapp_auth_send_link(body: WebappSendLinkRequest):
    """Generate a magic link token for the webapp and email it to the user."""
    from email_sender import send_magic_link

    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email")

    if not _validate_webapp_callback(body.callback_url):
        raise HTTPException(status_code=400, detail="Invalid callback URL")

    token = create_magic_link_token()
    db.create_magic_link_token(token, email, body.callback_url, "", ttl=600)

    verify_url = f"{API_BASE_URL}/webapp-auth/verify?token={_urlparse.quote(token)}"
    send_magic_link(email, verify_url)

    return {"ok": True}


@app.get("/webapp-auth/verify")
async def webapp_auth_verify(token: str = ""):
    """Verify magic link token, issue JWT, redirect to Next.js callback."""
    row = db.consume_magic_link_token(token)
    if not row:
        raise HTTPException(status_code=400, detail="Link expired or already used")

    email = row["email"]
    callback_url = row["callback"]

    if not _validate_webapp_callback(callback_url):
        raise HTTPException(status_code=400, detail="Invalid callback URL")

    user = db.get_user_by_email(email)
    if user is None:
        db.create_user(email, initial_balance=FREE_CREDIT_USD)
        user = db.get_user_by_email(email)

    jwt_token = create_session_token(user["id"])
    safe_token = _urlparse.quote(jwt_token)
    redirect_url = f"{callback_url}?token={safe_token}"

    return RedirectResponse(url=redirect_url, status_code=302)


# ---------------------------------------------------------------------------
# Webapp API routes (stats, activity, settings)
# ---------------------------------------------------------------------------

@app.get("/v1/stats")
async def stats(authorization: Annotated[str, Header()]):
    """Return usage stats for the authenticated user."""
    billing_user = _auth_user(authorization)
    user_id = billing_user["id"]

    result: dict[str, Any] = {
        "total_runs": db.count_runs(user_id),
        "balance_usd": round(billing_user["balance_usd"], 2),
    }

    trace_count = db.count_synced_traces(user_id)
    if trace_count > 0:
        result["traces"] = trace_count
        result["principles"] = db.count_synced_principles(user_id)
        result["projects"] = db.get_synced_projects(user_id)

    return result


@app.get("/v1/activity")
async def activity(authorization: Annotated[str, Header()]):
    """Return recent 20 usage runs for the authenticated user."""
    billing_user = _auth_user(authorization)
    runs = db.get_recent_runs(billing_user["id"], limit=20)
    return [
        {"model": r["model"], "cost_usd": r["cost_usd"], "ts": str(r["ts"])}
        for r in runs
    ]


@app.get("/v1/user/settings")
async def user_settings(authorization: Annotated[str, Header()]):
    """Return user settings (provider info)."""
    _auth_user(authorization)
    return {"provider": "forgemem"}


# ---------------------------------------------------------------------------
# CLI auth routes (magic link — no external auth provider)
# ---------------------------------------------------------------------------

@app.get("/cli-auth")
async def cli_auth_landing(request: Request):
    """Browser landing page — renders the email + magic link form."""
    callback = request.query_params.get("callback", "")
    state    = request.query_params.get("state", "")
    _validate_cli_callback(callback)
    return templates.TemplateResponse(
        "cli_auth.html",
        {"request": request, "callback": callback, "state": state},
    )


@app.post("/cli-auth/send-link")
async def cli_auth_send_link(request: Request):
    """Generate a magic link token, store it, and email it to the user."""
    from email_sender import send_magic_link
    form     = await request.form()
    email    = str(form.get("email", "")).strip().lower()
    callback = str(form.get("callback", ""))
    state    = str(form.get("state", ""))
    _validate_cli_callback(callback)

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email")

    token = create_magic_link_token()
    db.create_magic_link_token(token, email, callback, state, ttl=600)

    verify_url = (
        f"{API_BASE_URL}/cli-auth/verify"
        f"?token={_urlparse.quote(token)}"
        f"&callback={_urlparse.quote(callback)}"
        f"&state={_urlparse.quote(state)}"
    )
    send_magic_link(email, verify_url)

    return HTMLResponse(
        "<html><body><p>Check your email for a magic link (expires in 10 minutes).</p></body></html>"
    )


@app.get("/cli-auth/verify")
async def cli_auth_verify(request: Request):
    """User clicks magic link → verify token, issue JWT, redirect to CLI."""
    token    = request.query_params.get("token", "")
    callback = request.query_params.get("callback", "")
    state    = request.query_params.get("state", "")
    _validate_cli_callback(callback)

    row = db.consume_magic_link_token(token)
    if not row:
        raise HTTPException(status_code=400, detail="Link expired or already used")

    if row["state"] != state:
        raise HTTPException(status_code=400, detail="State mismatch")

    email = row["email"]
    user  = db.get_user_by_email(email)
    if user is None:
        db.create_user(email, initial_balance=FREE_CREDIT_USD)
        user = db.get_user_by_email(email)

    jwt_token = create_session_token(user["id"])
    db.create_session(jwt_token, user["id"])
    safe_token = _urlparse.quote(jwt_token)
    safe_state = _urlparse.quote(state)
    # Use the callback stored in the DB (validated at send-link time), not the query param.
    stored_cb = row["callback"]
    parsed_cb = _urlparse.urlparse(stored_cb)
    cb_port = int(parsed_cb.port)
    cb_path = _urlparse.quote(parsed_cb.path or "/", safe="/")
    redirect_url = f"http://127.0.0.1:{cb_port}{cb_path}?token={safe_token}&state={safe_state}"
    return RedirectResponse(url=redirect_url, status_code=302)
