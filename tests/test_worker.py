"""
Tests for the background worker (forgememo/worker.py).

Covers:
- process_one returns None when queue is empty
- process_one picks up undistilled events
- process_one marks event distilled=1 on success
- process_one writes a distilled_summary row
- process_one increments distill_attempts on inference failure
- process_one skips events that hit MAX_DISTILL_ATTEMPTS
- distill_event filters concepts to ALLOWED_CONCEPTS only
- distill_event prompt contains event payload
- concepts outside allowed set are dropped
"""

from __future__ import annotations

import json

import pytest

import forgememo.storage as storage_module
from forgememo.storage import get_conn, init_db
from forgememo.worker import Worker, MAX_DISTILL_ATTEMPTS, ALLOWED_CONCEPTS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_file = tmp_path / "worker_test.db"
    monkeypatch.setattr(storage_module, "DB_PATH", db_file)
    init_db()
    yield db_file


def _insert_event(**overrides) -> int:
    """Insert a raw event and return its id."""
    defaults = {
        "session_id": "sess-1",
        "project_id": "/tmp/proj",
        "source_tool": "claude",
        "event_type": "tool_use",
        "tool_name": "Edit",
        "payload": json.dumps({"file": "main.py"}),
        "seq": 1,
        "distilled": 0,
        "distill_attempts": 0,
        "content_hash": "abc123",
    }
    defaults.update(overrides)
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO events "
        "(session_id, project_id, source_tool, event_type, tool_name, "
        "payload, seq, distilled, distill_attempts, content_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            defaults["session_id"], defaults["project_id"], defaults["source_tool"],
            defaults["event_type"], defaults["tool_name"], defaults["payload"],
            defaults["seq"], defaults["distilled"], defaults["distill_attempts"],
            defaults["content_hash"],
        ),
    )
    event_id = cur.lastrowid
    conn.commit()
    conn.close()
    return event_id


_VALID_SUMMARY = {
    "type": "bugfix",
    "title": "Fixed null check",
    "narrative": "We fixed a null pointer exception in main.py.",
    "facts": ["always check for None"],
    "files_read": [],
    "files_modified": ["main.py"],
    "concepts": ["gotcha"],
    "impact_score": 7,
}


# ---------------------------------------------------------------------------
# process_one — empty queue
# ---------------------------------------------------------------------------

class TestProcessOneEmptyQueue:
    def test_returns_none_when_no_events(self):
        worker = Worker()
        assert worker.process_one() is None

    def test_returns_none_when_all_events_distilled(self):
        _insert_event(distilled=1)
        worker = Worker()
        assert worker.process_one() is None

    def test_returns_none_when_all_events_exhausted(self):
        _insert_event(distill_attempts=MAX_DISTILL_ATTEMPTS)
        worker = Worker()
        assert worker.process_one() is None


# ---------------------------------------------------------------------------
# process_one — success path
# ---------------------------------------------------------------------------

class TestProcessOneSuccess:
    def test_returns_event_id_on_success(self, monkeypatch):
        event_id = _insert_event()
        monkeypatch.setattr(
            "forgememo.worker.inference.call",
            lambda *a, **kw: json.dumps(_VALID_SUMMARY),
        )
        worker = Worker()
        result = worker.process_one()
        assert result == event_id

    def test_marks_event_distilled(self, monkeypatch):
        event_id = _insert_event()
        monkeypatch.setattr(
            "forgememo.worker.inference.call",
            lambda *a, **kw: json.dumps(_VALID_SUMMARY),
        )
        Worker().process_one()
        conn = get_conn()
        row = conn.execute("SELECT distilled FROM events WHERE id=?", (event_id,)).fetchone()
        conn.close()
        assert row["distilled"] == 1

    def test_writes_distilled_summary(self, monkeypatch):
        event_id = _insert_event()
        monkeypatch.setattr(
            "forgememo.worker.inference.call",
            lambda *a, **kw: json.dumps(_VALID_SUMMARY),
        )
        Worker().process_one()
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM distilled_summaries WHERE source_event_id=?", (event_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["title"] == "Fixed null check"
        assert row["type"] == "bugfix"

    def test_summary_stores_session_and_project(self, monkeypatch):
        event_id = _insert_event(session_id="my-sess", project_id="/my/proj")
        monkeypatch.setattr(
            "forgememo.worker.inference.call",
            lambda *a, **kw: json.dumps(_VALID_SUMMARY),
        )
        Worker().process_one()
        conn = get_conn()
        row = conn.execute(
            "SELECT session_id, project_id FROM distilled_summaries WHERE source_event_id=?",
            (event_id,),
        ).fetchone()
        conn.close()
        assert row["session_id"] == "my-sess"
        assert row["project_id"] == "/my/proj"

    def test_facts_stored_as_json(self, monkeypatch):
        _insert_event()
        monkeypatch.setattr(
            "forgememo.worker.inference.call",
            lambda *a, **kw: json.dumps({**_VALID_SUMMARY, "facts": ["fact A", "fact B"]}),
        )
        Worker().process_one()
        conn = get_conn()
        row = conn.execute("SELECT facts FROM distilled_summaries").fetchone()
        conn.close()
        assert json.loads(row["facts"]) == ["fact A", "fact B"]

    def test_impact_score_stored(self, monkeypatch):
        _insert_event()
        monkeypatch.setattr(
            "forgememo.worker.inference.call",
            lambda *a, **kw: json.dumps({**_VALID_SUMMARY, "impact_score": 9}),
        )
        Worker().process_one()
        conn = get_conn()
        row = conn.execute("SELECT impact_score FROM distilled_summaries").fetchone()
        conn.close()
        assert row["impact_score"] == 9

    def test_processes_oldest_event_first(self, monkeypatch):
        id_first = _insert_event(seq=1, content_hash="hash-first")
        _insert_event(seq=2, content_hash="hash-second")
        monkeypatch.setattr(
            "forgememo.worker.inference.call",
            lambda *a, **kw: json.dumps(_VALID_SUMMARY),
        )
        result = Worker().process_one()
        assert result == id_first


# ---------------------------------------------------------------------------
# process_one — failure path
# ---------------------------------------------------------------------------

class TestProcessOneFailure:
    def test_increments_distill_attempts_on_error(self, monkeypatch):
        event_id = _insert_event()
        monkeypatch.setattr(
            "forgememo.worker.inference.call",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("API error")),
        )
        Worker().process_one()
        conn = get_conn()
        row = conn.execute("SELECT distill_attempts FROM events WHERE id=?", (event_id,)).fetchone()
        conn.close()
        assert row["distill_attempts"] == 1

    def test_event_not_marked_distilled_on_error(self, monkeypatch):
        event_id = _insert_event()
        monkeypatch.setattr(
            "forgememo.worker.inference.call",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        Worker().process_one()
        conn = get_conn()
        row = conn.execute("SELECT distilled FROM events WHERE id=?", (event_id,)).fetchone()
        conn.close()
        assert row["distilled"] == 0

    def test_returns_none_on_inference_error(self, monkeypatch):
        _insert_event()
        monkeypatch.setattr(
            "forgememo.worker.inference.call",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        result = Worker().process_one()
        assert result is None

    def test_skips_event_at_max_attempts(self, monkeypatch):
        _insert_event(distill_attempts=MAX_DISTILL_ATTEMPTS)
        called = []
        monkeypatch.setattr(
            "forgememo.worker.inference.call",
            lambda *a, **kw: called.append(1) or json.dumps(_VALID_SUMMARY),
        )
        result = Worker().process_one()
        assert result is None
        assert called == [], "inference.call must not be invoked for exhausted events"

    def test_retries_up_to_max_attempts(self, monkeypatch):
        _insert_event()
        monkeypatch.setattr(
            "forgememo.worker.inference.call",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        worker = Worker()
        for _ in range(MAX_DISTILL_ATTEMPTS):
            worker.process_one()

        # At max attempts, process_one must return None (event skipped)
        result = worker.process_one()
        assert result is None


# ---------------------------------------------------------------------------
# distill_event — concept filtering
# ---------------------------------------------------------------------------

class TestDistillEventConcepts:
    def test_allowed_concepts_kept(self, monkeypatch):
        worker = Worker()
        event = {"id": 1, "session_id": "s", "project_id": "p",
                  "source_tool": "claude", "payload": "{}"}
        summary = {**_VALID_SUMMARY, "concepts": list(ALLOWED_CONCEPTS)}
        monkeypatch.setattr(
            "forgememo.worker.inference.call",
            lambda *a, **kw: json.dumps(summary),
        )
        result = worker.distill_event(event)
        assert set(result["concepts"]) == ALLOWED_CONCEPTS

    def test_disallowed_concepts_filtered(self, monkeypatch):
        worker = Worker()
        event = {"id": 1, "session_id": "s", "project_id": "p",
                  "source_tool": "claude", "payload": "{}"}
        summary = {**_VALID_SUMMARY, "concepts": ["security", "INVALID_CONCEPT", "pattern"]}
        monkeypatch.setattr(
            "forgememo.worker.inference.call",
            lambda *a, **kw: json.dumps(summary),
        )
        result = worker.distill_event(event)
        assert "INVALID_CONCEPT" not in result["concepts"]
        assert "security" in result["concepts"]
        assert "pattern" in result["concepts"]

    def test_empty_concepts_allowed(self, monkeypatch):
        worker = Worker()
        event = {"id": 1, "session_id": "s", "project_id": "p",
                  "source_tool": "claude", "payload": "{}"}
        summary = {**_VALID_SUMMARY, "concepts": []}
        monkeypatch.setattr(
            "forgememo.worker.inference.call",
            lambda *a, **kw: json.dumps(summary),
        )
        result = worker.distill_event(event)
        assert result["concepts"] == []

    def test_payload_truncated_in_prompt(self, monkeypatch):
        prompts = []
        worker = Worker()
        long_payload = "x" * 5000
        event = {"id": 1, "session_id": "s", "project_id": "p",
                  "source_tool": "claude", "payload": long_payload}

        def capture_call(prompt, **kw):
            prompts.append(prompt)
            return json.dumps(_VALID_SUMMARY)

        monkeypatch.setattr("forgememo.worker.inference.call", capture_call)
        worker.distill_event(event)
        # Payload is truncated to 2000 chars in the prompt
        assert len(prompts[0]) < len(long_payload) + 500
