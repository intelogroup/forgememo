"""
Tests for error_events circuit breaker.
"""



class TestCircuitBreaker:
    """Test error_events circuit breaker functionality."""

    def test_circuit_breaker_initially_closed(self):
        """Circuit breaker starts in closed (enabled) state."""
        import forgememo.daemon as daemon_module
        import importlib

        importlib.reload(daemon_module)

        assert daemon_module._error_events_circuit_open() is False

    def test_circuit_breaker_records_failure(self):
        """Recording failures increments counter."""
        import forgememo.daemon as daemon_module
        import importlib

        importlib.reload(daemon_module)

        initial = daemon_module._error_events_consecutive_failures
        daemon_module._error_events_record_failure()
        assert daemon_module._error_events_consecutive_failures == initial + 1

    def test_circuit_breaker_trips_after_limit(self):
        """Circuit breaker trips after 3 consecutive failures."""
        import forgememo.daemon as daemon_module
        import importlib

        importlib.reload(daemon_module)

        for _ in range(3):
            daemon_module._error_events_record_failure()

        assert daemon_module._error_events_circuit_open() is True

    def test_circuit_breaker_records_success(self):
        """Recording success resets counter."""
        import forgememo.daemon as daemon_module
        import importlib

        importlib.reload(daemon_module)

        daemon_module._error_events_record_failure()
        daemon_module._error_events_record_failure()
        daemon_module._error_events_record_success()

        assert daemon_module._error_events_consecutive_failures == 0

    def test_circuit_breaker_recovery(self):
        """Circuit breaker closes after success."""
        import forgememo.daemon as daemon_module
        import importlib

        importlib.reload(daemon_module)

        for _ in range(3):
            daemon_module._error_events_record_failure()
        assert daemon_module._error_events_circuit_open() is True

        daemon_module._error_events_record_success()
        assert daemon_module._error_events_circuit_open() is False

    def test_circuit_breaker_half_open_after_interval(self):
        """Circuit breaker enters half-open state after probe interval."""
        import time
        import forgememo.daemon as daemon_module
        import importlib

        importlib.reload(daemon_module)

        for _ in range(3):
            daemon_module._error_events_record_failure()
        assert daemon_module._error_events_circuit_open() is True

        # Simulate time passing beyond the probe interval
        daemon_module._error_events_tripped_at = (
            time.time() - daemon_module._ERROR_EVENTS_HALF_OPEN_INTERVAL - 1
        )
        assert daemon_module._error_events_circuit_open() is False  # half-open: allow probe

    def test_circuit_breaker_returns_503_when_open(self):
        """POST returns 503 when circuit breaker is open."""
        import os
        import tempfile
        from pathlib import Path

        import forgememo.daemon as daemon_module
        import forgememo.storage as storage_module
        import sqlite3
        from forgememo.storage import SCHEMA_SQL
        from forgememo.daemon import create_app

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            os.environ["FORGEMEM_DB"] = str(db_path)

            conn = sqlite3.connect(str(db_path))
            conn.executescript(SCHEMA_SQL)
            conn.close()

            import importlib

            importlib.reload(daemon_module)
            importlib.reload(storage_module)

            for _ in range(3):
                daemon_module._error_events_record_failure()

            app = create_app()
            app.config["TESTING"] = True
            client = app.test_client()

            response = client.post(
                "/error_events",
                json={
                    "session_id": "test-session",
                    "fingerprint": "fp-123",
                },
            )

            assert response.status_code == 503
            data = response.get_json()
            assert "module_disabled" in data["error"]
