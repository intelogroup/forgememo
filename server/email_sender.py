"""Send transactional email via Resend (production) or SMTP/Mailpit (dev)."""
from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

RESEND_API_KEY  = os.environ.get("RESEND_API_KEY", "")
FROM_ADDRESS    = "Forgemem <onboarding@resend.dev>"

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
    """Send a magic link email. Tries Resend first; falls back to Mailpit SMTP on any failure."""
    import logging
    html = f"""
    <p>Click the link below to sign in to Forgemem. This link expires in 10 minutes.</p>
    <p><a href="{magic_url}" style="font-size:16px;font-weight:bold">Sign in to Forgemem</a></p>
    <p>Or copy this URL:<br><code>{magic_url}</code></p>
    <p style="color:#888;font-size:12px">If you didn't request this, ignore this email.</p>
    """
    subject = "Your Forgemem sign-in link"

    if RESEND_API_KEY:
        try:
            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={"from": FROM_ADDRESS, "to": [to], "subject": subject, "html": html},
                timeout=10,
            )
            if resp.status_code in (200, 201):
                return
            logging.warning("Resend failed (%s): %s", resp.status_code, resp.text[:200])
            raise RuntimeError(f"Email delivery failed (Resend {resp.status_code}): {resp.text[:120]}")
        except RuntimeError:
            raise
        except Exception as exc:
            logging.warning("Resend error: %s", exc)
            raise RuntimeError(f"Email delivery failed: {exc}") from exc

    # Dev-only SMTP fallback (Mailpit) — only runs when RESEND_API_KEY is not set
    try:
        _send_via_smtp(to, subject, html)
    except Exception as exc:
        logging.warning("SMTP fallback failed: %s", exc)
        raise RuntimeError(f"Email delivery failed (SMTP): {exc}") from exc
