"""Send transactional email via Resend (production) or SMTP/Mailpit (dev)."""
from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

RESEND_API_KEY  = os.environ.get("RESEND_API_KEY", "")
FROM_ADDRESS    = "Forgemem <noreply@forgemem.com>"

# Dev SMTP settings — defaults to Mailpit (http://localhost:8025)
DEV_SMTP_HOST = os.environ.get("DEV_SMTP_HOST", "127.0.0.1")
DEV_SMTP_PORT = int(os.environ.get("DEV_SMTP_PORT", "1025"))


def _send_via_smtp(to: str, subject: str, html: str) -> None:
    """Send via local SMTP (Mailpit) — for dev/test only."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = FROM_ADDRESS
    msg["To"]      = to
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(DEV_SMTP_HOST, DEV_SMTP_PORT, timeout=5) as s:
        s.sendmail(FROM_ADDRESS, [to], msg.as_string())


def send_magic_link(to: str, magic_url: str) -> None:
    """Send a magic link email. Uses Resend in prod, Mailpit SMTP in dev."""
    html = f"""
    <p>Click the link below to sign in to Forgemem CLI. This link expires in 10 minutes.</p>
    <p><a href="{magic_url}" style="font-size:16px;font-weight:bold">Sign in to Forgemem</a></p>
    <p>Or copy this URL:<br><code>{magic_url}</code></p>
    <p style="color:#888;font-size:12px">If you didn't request this, ignore this email.</p>
    """

    if not RESEND_API_KEY:
        _send_via_smtp(to, "Your Forgemem sign-in link", html)
        return

    html = f"""
    <p>Click the link below to sign in to Forgemem CLI. This link expires in 10 minutes.</p>
    <p><a href="{magic_url}" style="font-size:16px;font-weight:bold">Sign in to Forgemem</a></p>
    <p>Or copy this URL:<br><code>{magic_url}</code></p>
    <p style="color:#888;font-size:12px">If you didn't request this, ignore this email.</p>
    """

    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={
            "from": FROM_ADDRESS,
            "to": [to],
            "subject": "Your Forgemem sign-in link",
            "html": html,
        },
        timeout=10,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"Resend API error {resp.status_code}: {resp.text[:200]}")
