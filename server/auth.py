"""JWT-based auth for Forgemem server. No external auth provider required."""
from __future__ import annotations

import os
import secrets
import time

import jwt

JWT_SECRET    = os.environ.get("FORGEMEM_JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
SESSION_TTL   = 30 * 86400  # 30 days


def _secret() -> str:
    if not JWT_SECRET:
        raise RuntimeError("FORGEMEM_JWT_SECRET env var required")
    return JWT_SECRET


def create_session_token(user_id: str, ttl_seconds: int = SESSION_TTL) -> str:
    """Create a signed JWT for the given user_id."""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def verify_session_token(token: str) -> dict:
    """Verify a JWT and return the payload. Raises ValueError on failure."""
    try:
        payload = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise ValueError("Token expired")
    except jwt.InvalidTokenError as e:
        raise ValueError(f"Invalid token: {e}")


def create_magic_link_token() -> str:
    """Generate a secure random token for magic link emails."""
    return secrets.token_urlsafe(32)
