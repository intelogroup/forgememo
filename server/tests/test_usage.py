import sys
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from usage import check_rate_limit, RateLimitExceeded


def test_rate_limit_passes_under_limit():
    db = MagicMock()
    db.run_count_in_window.return_value = 10
    check_rate_limit(db, "user1", limit=60)  # Should not raise


def test_rate_limit_raises_at_limit():
    db = MagicMock()
    db.run_count_in_window.return_value = 60
    with pytest.raises(RateLimitExceeded):
        check_rate_limit(db, "user1", limit=60)


def test_rate_limit_raises_over_limit():
    db = MagicMock()
    db.run_count_in_window.return_value = 100
    with pytest.raises(RateLimitExceeded):
        check_rate_limit(db, "user1", limit=60)
