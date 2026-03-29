"""Rate limiting helpers."""
from __future__ import annotations

import os

RATE_LIMIT_PER_HOUR = int(os.environ.get("RATE_LIMIT_PER_HOUR", "60"))


class RateLimitExceeded(Exception):
    pass


def check_rate_limit(db, user_id: str, limit: int = RATE_LIMIT_PER_HOUR) -> None:
    """Raise RateLimitExceeded if user has exceeded hourly run limit."""
    count = db.run_count_in_window(user_id, window_seconds=3600)
    if count >= limit:
        raise RateLimitExceeded(f"Rate limit: {limit} runs/hour. Try again later.")
