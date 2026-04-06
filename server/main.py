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

import _env  # noqa: F401 — loads .env before any other imports
import os
import re
import secrets
import time as _time
import urllib.parse as _urlparse
from datetime import datetime, timezone
from typing import Annotated, Any

import httpx
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
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Request-Id"],
    max_age=600,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


GROQ_API_KEY         = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL           = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
PLATFORM_FEE_USD     = float(os.environ.get("PLATFORM_FEE_USD", "0.02"))
FREE_CREDIT_USD      = float(os.environ.get("FREE_CREDIT_USD", "5.0"))
API_BASE_URL         = os.environ.get("API_BASE_URL", "https://api.forgememo.com")
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GITHUB_CLIENT_ID     = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")

# Short-lived in-memory state store: state_token -> (callback_url, expires_at)
_oauth_states: dict[str, tuple[str, float]] = {}

db = Database()
db.init()

_CLI_CALLBACK_RE = re.compile(r"^http://127\.0\.0\.1:\d{4,5}/\S*$")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class InferenceRequest(BaseModel):
    prompt: str
    max_tokens: int = 300
    model: str = "llama-3.1-8b-instant"


class InferenceResponse(BaseModel):
    text: str
    cost_usd: float
    balance_usd: float
    run_id: str


class CheckoutRequest(BaseModel):
    pack_id: str = "starter"
    success_url: str = "https://app.forgememo.com/billing/success"
    cancel_url: str = "https://app.forgememo.com/billing"


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
    # Groq llama-3.1-8b-instant: $0.05 input / $0.08 output per 1M tokens
    input_tokens = len(prompt) / 4
    input_cost  = (input_tokens / 1_000_000) * 0.05
    output_cost = (max_tokens  / 1_000_000) * 0.08
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

    resp = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": body.prompt}],
            "max_tokens": body.max_tokens,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Groq error: {resp.text[:200]}")
    text = resp.json()["choices"][0]["message"]["content"].strip()

    try:
        new_balance = db.deduct_credits(billing_user["id"], estimated_cost)
    except ValueError:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    run_id = secrets.token_hex(8)
    db.log_run(billing_user["id"], run_id, estimated_cost, GROQ_MODEL, new_balance)

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


import os as _os
_INSTANCE_ID = _os.environ.get("RENDER_INSTANCE_ID", "local")


@app.get("/debug/webhook-secret-check")
async def debug_webhook_secret():
    """Temporary: confirm which secret is loaded. Returns first/last 4 chars only."""
    import stripe as _stripe
    s = _os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    return {"prefix": s[:10], "suffix": s[-4:], "length": len(s),
            "stripe_ver": _stripe._version.VERSION, "instance": _INSTANCE_ID}


@app.post("/debug/webhook-echo")
async def debug_webhook_echo(request: Request):
    """Temporary: echo back payload hash + try verify to debug signature issues."""
    import hashlib
    import stripe as _stripe
    from billing import _webhook_secret
    body = await request.body()
    sig = request.headers.get("stripe-signature", "")
    secret = _webhook_secret()
    verify_result = "not_attempted"
    verify_error = None
    try:
        _stripe.Webhook.construct_event(body, sig, secret)
        verify_result = "ok"
    except Exception as e:
        verify_result = "fail"
        verify_error = f"{type(e).__name__}: {e}"
    return {
        "body_len": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest()[:16],
        "sig_header": sig[:80],
        "secret_prefix": secret[:10],
        "secret_len": len(secret),
        "verify": verify_result,
        "verify_error": verify_error,
        "instance": _INSTANCE_ID,
    }


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    # Inline verify to isolate from billing module import issues
    import stripe as _stripe_wh
    from billing import _webhook_secret as _get_secret
    _secret = _get_secret()
    try:
        _stripe_wh.Webhook.construct_event(payload, sig, _secret)
    except Exception as _inline_exc:
        import logging
        logging.warning("stripe INLINE failure [inst=%s secret_len=%d]: %s", _INSTANCE_ID, len(_secret), _inline_exc)
        raise HTTPException(status_code=400, detail=f"INLINE fail [{_INSTANCE_ID}] secret_len={len(_secret)}: {_inline_exc}")
    try:
        result = parse_webhook_event(payload, sig)
    except Exception as exc:
        import logging
        logging.warning("stripe webhook signature failure [inst=%s]: %s", _INSTANCE_ID, exc)
        raise HTTPException(status_code=400, detail=f"Invalid webhook signature [{_INSTANCE_ID}]: {exc}")

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
# CLI billing setup routes
# ---------------------------------------------------------------------------

_BILLING_EVENT_TYPES = {"card_added", "credits_added"}


@app.get("/billing/cli-callback")
async def billing_cli_callback(
    type: str = "",
    cli_callback: str = "",
    state: str = "",
    amount: str = "",
):
    """Redirect browser to the CLI loopback with a billing event."""
    _validate_cli_callback(cli_callback)
    if type not in _BILLING_EVENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown event type: {type!r}")

    params: dict[str, str] = {"type": type, "state": state}
    if amount:
        params["amount"] = amount

    redirect_url = cli_callback.rstrip("/") + "?" + _urlparse.urlencode(params)
    return RedirectResponse(url=redirect_url, status_code=302)


@app.get("/billing/cli-setup")
async def billing_cli_setup(
    request: Request,
    cli_callback: str = "",
    state: str = "",
    token: str = "",
):
    """Render the CLI billing setup page. Requires valid JWT."""
    _validate_cli_callback(cli_callback)
    try:
        verify_session_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    cli_callback_encoded = _urlparse.quote(cli_callback, safe="")
    return templates.TemplateResponse(
        request, "billing_cli_setup.html",
        {"cli_callback_encoded": cli_callback_encoded, "state": state},
    )


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
    user = _auth_user(authorization)
    return {
        "provider":   user.get("provider", "forgemem"),
        "name":       user.get("name"),
        "avatar_url": user.get("avatar_url"),
        "username":   user.get("username"),
    }


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
        request, "cli_auth.html",
        {"callback": callback, "state": state},
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


# ---------------------------------------------------------------------------
# OAuth routes (Google + GitHub)
# ---------------------------------------------------------------------------

def _oauth_validate_callback(callback_url: str) -> None:
    if not callback_url:
        raise HTTPException(status_code=400, detail="callback_url required")
    if not (_validate_webapp_callback(callback_url) or _CLI_CALLBACK_RE.match(callback_url)):
        raise HTTPException(status_code=400, detail="Invalid callback_url")


def _oauth_issue_state(callback_url: str) -> str:
    state = secrets.token_hex(16)
    _oauth_states[state] = (callback_url, _time.time() + 300)
    return state


def _oauth_consume_state(state: str) -> str:
    entry = _oauth_states.pop(state, None)
    if not entry:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    callback_url, expires_at = entry
    if _time.time() > expires_at:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    return callback_url


def _oauth_finish(user: dict, callback_url: str) -> RedirectResponse:
    jwt_token = create_session_token(user["id"])
    db.create_session(jwt_token, user["id"])
    return RedirectResponse(
        url=f"{callback_url}?token={_urlparse.quote(jwt_token)}",
        status_code=302,
    )


@app.get("/oauth/google/authorize")
async def oauth_google_authorize(callback_url: str = ""):
    _oauth_validate_callback(callback_url)
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google OAuth not configured")
    state = _oauth_issue_state(callback_url)
    redirect_uri = _urlparse.quote(f"{API_BASE_URL}/oauth/google/callback", safe="")
    url = (
        f"https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope=openid%20email%20profile"
        f"&state={state}"
    )
    return RedirectResponse(url=url, status_code=302)


@app.get("/oauth/google/callback")
async def oauth_google_callback(code: str = "", state: str = ""):
    callback_url = _oauth_consume_state(state)
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")

    token_resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": f"{API_BASE_URL}/oauth/google/callback",
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    if token_resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Google token exchange failed")
    access_token = token_resp.json().get("access_token", "")

    userinfo_resp = httpx.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if userinfo_resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Google userinfo fetch failed")
    info = userinfo_resp.json()

    user = db.upsert_oauth_user(
        email=info["email"],
        provider="google",
        provider_id=info["sub"],
        name=info.get("name"),
        avatar_url=info.get("picture"),
        username=None,
        initial_balance=FREE_CREDIT_USD,
    )
    return _oauth_finish(user, callback_url)


@app.get("/oauth/github/authorize")
async def oauth_github_authorize(callback_url: str = ""):
    _oauth_validate_callback(callback_url)
    if not GITHUB_CLIENT_ID:
        raise HTTPException(status_code=503, detail="GitHub OAuth not configured")
    state = _oauth_issue_state(callback_url)
    redirect_uri = _urlparse.quote(f"{API_BASE_URL}/oauth/github/callback", safe="")
    url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=user%3Aemail"
        f"&state={state}"
    )
    return RedirectResponse(url=url, status_code=302)


@app.get("/oauth/github/callback")
async def oauth_github_callback(code: str = "", state: str = ""):
    callback_url = _oauth_consume_state(state)
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")

    token_resp = httpx.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": GITHUB_CLIENT_ID,
            "client_secret": GITHUB_CLIENT_SECRET,
            "code": code,
            "redirect_uri": f"{API_BASE_URL}/oauth/github/callback",
        },
        timeout=10,
    )
    if token_resp.status_code != 200:
        raise HTTPException(status_code=502, detail="GitHub token exchange failed")
    access_token = token_resp.json().get("access_token", "")

    user_resp = httpx.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"},
        timeout=10,
    )
    if user_resp.status_code != 200:
        raise HTTPException(status_code=502, detail="GitHub user fetch failed")
    gh = user_resp.json()

    email = gh.get("email")
    if not email:
        emails_resp = httpx.get(
            "https://api.github.com/user/emails",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if emails_resp.status_code == 200:
            primary = next(
                (e["email"] for e in emails_resp.json() if e.get("primary") and e.get("verified")),
                None,
            )
            email = primary

    if not email:
        raise HTTPException(status_code=400, detail="No verified email on GitHub account")

    user = db.upsert_oauth_user(
        email=email,
        provider="github",
        provider_id=str(gh["id"]),
        name=gh.get("name"),
        avatar_url=gh.get("avatar_url"),
        username=gh.get("login"),
        initial_balance=FREE_CREDIT_USD,
    )
    return _oauth_finish(user, callback_url)
