"""
Tests for the daemon Flask API (forgememo/daemon.py).

Covers every endpoint:
  GET  /health
  POST /events
  GET  /search
  GET  /timeline
  GET  /observation/<prefix>/<id>
  POST /session_summaries
  GET  /session_summaries
"""

from __future__ import annotations

import json

import pytest

import forgememo.storage as storage_module
from forgememo.storage import get_conn, init_db
from forgememo.daemon import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path, monkeypatch):
    db_file = tmp_path / "daemon_test.db"
    monkeypatch.setattr(storage_module, "DB_PATH", db_file)
    init_db()
    return create_app()


@pytest.fixture()
def client(app):
    with app.test_client() as c:
        yield c


def _post_event(client, **overrides):
    payload = {
        "session_id": "sess-1",
        "project_id": "/tmp/myproject",
        "source_tool": "claude",
        "event_type": "tool_use",
        "tool_name": "Edit",
        "payload": json.dumps({"file": "main.py", "action": "edit"}),
        "seq": 1,
    }
    payload.update(overrides)
    return client.post("/events", json=payload)


def _insert_distilled(
    tmp_path,
    monkeypatch,
    project_id="/tmp/myproject",
    title="Fixed null pointer",
    narrative="We fixed a null pointer in main.py",
    concepts=None,
    impact_score=8,
    ts: str | None = None,
):
    """Insert a distilled_summary row directly and return its id."""
    concepts = concepts or ["gotcha"]
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO distilled_summaries "
        "(session_id, project_id, source_tool, type, title, narrative, "
        "facts, files_read, files_modified, concepts, impact_score) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("sess-1", project_id, "claude", "bugfix", title,
         narrative,
         json.dumps(["check for None"]), json.dumps([]), json.dumps([]),
         json.dumps(concepts), impact_score),
    )
    row_id = cur.lastrowid
    if ts:
        conn.execute("UPDATE distilled_summaries SET ts=? WHERE id=?", (ts, row_id))
    conn.execute(
        "INSERT INTO distilled_summaries_fts(rowid, title, narrative, concepts, tags, project_id) "
        "VALUES (?,?,?,?,?,?)",
        (row_id, title, narrative,
         ",".join(concepts), "", project_id),
    )
    conn.commit()
    conn.close()
    return row_id


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_returns_200(self, client):
        r = client.get("/health")
        assert r.status_code == 200

    def test_returns_ok_true(self, client):
        r = client.get("/health")
        assert r.get_json() == {"ok": True}


# ---------------------------------------------------------------------------
# POST /events
# ---------------------------------------------------------------------------

class TestPostEvents:
    def test_valid_event_returns_201(self, client):
        r = _post_event(client)
        assert r.status_code == 201

    def test_valid_event_returns_event_id(self, client):
        r = _post_event(client)
        data = r.get_json()
        assert "event_id" in data
        assert isinstance(data["event_id"], int)

    def test_missing_session_id_returns_400(self, client):
        r = _post_event(client, session_id=None)
        assert r.status_code == 400

    def test_missing_project_id_returns_400(self, client):
        r = _post_event(client, project_id=None)
        assert r.status_code == 400

    def test_missing_payload_returns_400(self, client):
        r = _post_event(client, payload=None)
        assert r.status_code == 400

    def test_missing_event_type_returns_400(self, client):
        r = _post_event(client, event_type=None)
        assert r.status_code == 400

    def test_missing_source_tool_returns_400(self, client):
        r = _post_event(client, source_tool=None)
        assert r.status_code == 400

    def test_missing_seq_returns_400(self, client):
        r = _post_event(client, seq=None)
        assert r.status_code == 400

    def test_400_lists_missing_fields(self, client):
        r = _post_event(client, session_id=None, project_id=None)
        data = r.get_json()
        assert "fields" in data
        assert "session_id" in data["fields"]
        assert "project_id" in data["fields"]

    def test_duplicate_event_returns_200(self, client):
        _post_event(client)
        r = _post_event(client)  # same content, same session → duplicate
        assert r.status_code == 200
        assert r.get_json()["status"] == "duplicate"

    def test_duplicate_different_session_is_not_duplicate(self, client):
        _post_event(client, session_id="sess-A")
        r = _post_event(client, session_id="sess-B")
        assert r.status_code == 201

    def test_duplicate_outside_window_is_not_duplicate(self, client):
        r1 = _post_event(client, seq=101)
        assert r1.status_code == 201
        conn = get_conn()
        conn.execute("UPDATE events SET ts=datetime('now','-120 seconds') WHERE seq=101")
        conn.commit()
        conn.close()
        r2 = _post_event(client, seq=102)
        assert r2.status_code == 201

    def test_private_tags_stripped_from_payload(self, client):
        payload_with_private = json.dumps({
            "content": "normal content <private>SECRET_KEY=abc123</private> end"
        })
        r = _post_event(client, payload=payload_with_private)
        assert r.status_code == 201
        conn = get_conn()
        row = conn.execute("SELECT payload FROM events ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        assert "SECRET_KEY" not in row["payload"]
        assert "normal content" in row["payload"]

    def test_private_tags_stripped_from_dict_payload(self, client):
        payload_with_private = {
            "content": "normal <private>SECRET</private> end",
            "nested": {"token": "<private>ABC</private>"},
        }
        r = _post_event(client, payload=payload_with_private, seq=77)
        assert r.status_code == 201
        conn = get_conn()
        row = conn.execute("SELECT payload FROM events WHERE seq=77").fetchone()
        conn.close()
        assert "SECRET" not in row["payload"]
        assert "ABC" not in row["payload"]

    def test_dict_payload_accepted(self, client):
        r = _post_event(client, payload={"key": "value"}, seq=99)
        assert r.status_code in (200, 201)

    def test_seq_string_is_accepted(self, client):
        r = _post_event(client, seq="123")
        assert r.status_code == 201

    def test_different_tool_name_is_not_duplicate(self, client):
        _post_event(client, tool_name="Edit", seq=201)
        r = _post_event(client, tool_name="Write", seq=202)
        assert r.status_code == 201

    def test_different_payload_is_not_duplicate(self, client):
        _post_event(client, payload=json.dumps({"file": "a.py"}), seq=301)
        r = _post_event(client, payload=json.dumps({"file": "b.py"}), seq=302)
        assert r.status_code == 201

    def test_missing_tool_name_is_ok(self, client):
        r = _post_event(client, tool_name=None, seq=303)
        assert r.status_code == 201

    def test_different_event_type_is_not_duplicate(self, client):
        _post_event(client, event_type="tool_use", seq=304)
        r = _post_event(client, event_type="tool_result", seq=305)
        assert r.status_code == 201

    def test_event_stored_in_db(self, client):
        _post_event(client, seq=42)
        conn = get_conn()
        row = conn.execute("SELECT * FROM events WHERE seq=42").fetchone()
        conn.close()
        assert row is not None
        assert row["source_tool"] == "claude"

    def test_event_stored_in_fts(self, client):
        _post_event(client, payload=json.dumps({"unique": "findme_xyz"}), seq=55)
        conn = get_conn()
        rows = conn.execute(
            "SELECT rowid FROM events_fts WHERE events_fts MATCH 'findme_xyz'"
        ).fetchall()
        conn.close()
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# GET /search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_missing_query_returns_400(self, client):
        r = client.get("/search")
        assert r.status_code == 400

    def test_empty_query_returns_400(self, client):
        r = client.get("/search?q=")
        assert r.status_code == 400

    def test_fts_operator_tokens_are_sanitized(self, client):
        """FTS5 operator tokens like AND/OR/NOT are quoted and treated as literals, not errors."""
        r = client.get("/search?q=foo+AND")
        assert r.status_code == 200
        assert "results" in r.get_json()

    def test_hyphen_query_is_sanitized(self, client):
        """Hyphens in queries are wrapped in quotes so FTS5 doesn't misparse them."""
        r = client.get("/search?q=some-thing")
        assert r.status_code == 200
        assert "results" in r.get_json()

    def test_search_returns_results_list(self, client, tmp_path, monkeypatch):
        _insert_distilled(tmp_path, monkeypatch)
        r = client.get("/search?q=null+pointer")
        assert r.status_code == 200
        assert "results" in r.get_json()

    def test_search_finds_distilled_summary(self, client, tmp_path, monkeypatch):
        _insert_distilled(tmp_path, monkeypatch)
        r = client.get("/search?q=null+pointer")
        results = r.get_json()["results"]
        assert any(res["id"].startswith("d:") for res in results)

    def test_search_result_has_required_fields(self, client, tmp_path, monkeypatch):
        _insert_distilled(tmp_path, monkeypatch)
        r = client.get("/search?q=null+pointer")
        result = r.get_json()["results"][0]
        for field in ("id", "ts", "type", "title", "project_id"):
            assert field in result

    def test_search_k_limits_results(self, client, tmp_path, monkeypatch):
        for i in range(5):
            conn = get_conn()
            cur = conn.execute(
                "INSERT INTO distilled_summaries "
                "(session_id, project_id, source_tool, type, title, narrative, "
                "facts, files_read, files_modified, concepts, impact_score) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("s", "/tmp/p", "claude", "note", f"searchable item {i}",
                 "narrative", "[]", "[]", "[]", "[]", 5),
            )
            rid = cur.lastrowid
            conn.execute(
                "INSERT INTO distilled_summaries_fts(rowid,title,narrative,concepts,tags,project_id) "
                "VALUES (?,?,?,?,?,?)",
                (rid, f"searchable item {i}", "narrative", "", "", "/tmp/p"),
            )
            conn.commit()
            conn.close()

        r = client.get("/search?q=searchable&k=2")
        assert len(r.get_json()["results"]) <= 2

    def test_search_project_filter(self, client, tmp_path, monkeypatch):
        _insert_distilled(tmp_path, monkeypatch, project_id="/tmp/proj-A")
        _insert_distilled(tmp_path, monkeypatch, project_id="/tmp/proj-B")
        r = client.get("/search?q=null+pointer&project_id=/tmp/proj-A")
        results = r.get_json()["results"]
        assert all(res["project_id"] == "/tmp/proj-A" for res in results if res["id"].startswith("d:"))

    def test_search_no_match_returns_empty_list(self, client):
        r = client.get("/search?q=zzznomatchzzz")
        assert r.get_json()["results"] == []

    def test_search_concepts_filters_distilled_only(self, client, tmp_path, monkeypatch):
        _insert_distilled(
            tmp_path,
            monkeypatch,
            project_id="/tmp/proj-A",
            title="Null pointer fixed",
            narrative="Null pointer fix with gotcha",
            concepts=["gotcha"],
        )
        _insert_distilled(
            tmp_path,
            monkeypatch,
            project_id="/tmp/proj-A",
            title="Null pointer tuned",
            narrative="Null pointer performance work",
            concepts=["performance"],
        )
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO session_summaries (session_id, project_id, source_tool, request, learnings, concepts) "
            "VALUES (?,?,?,?,?,?)",
            ("s1", "/tmp/proj-A", "mcp", "Null summary", "Null issue summary", json.dumps(["gotcha"])),
        )
        ss_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO principles (project_tag, type, principle, impact_score, tags) "
            "VALUES (?,?,?,?,?)",
            ("/tmp/proj-A", "note", "Null principle", 5, "gotcha"),
        )
        pid = cur.lastrowid
        conn.execute(
            "INSERT INTO principles_fts(rowid, principle, project_tag, tags) VALUES (?,?,?,?)",
            (pid, "Null principle", "/tmp/proj-A", "gotcha"),
        )
        conn.execute(
            "INSERT INTO session_summaries_fts(rowid, request, learnings, next_steps, concepts, project_id) "
            "VALUES (?,?,?,?,?,?)",
            (ss_id, "Null summary", "Null issue summary", "", json.dumps(["gotcha"]), "/tmp/proj-A"),
        )
        conn.commit()
        conn.close()

        r = client.get("/search?q=null&concepts=gotcha")
        assert r.status_code == 200
        results = r.get_json()["results"]
        ids = [res["id"] for res in results]
        assert any(res["id"].startswith("d:") and "Null pointer fixed" in res["title"] for res in results)
        assert not any(res["id"].startswith("d:") and "Null pointer tuned" in res["title"] for res in results)
        assert any(i.startswith("s:") for i in ids)
        assert any(i.startswith("c:") for i in ids)

    def test_search_type_filter_applies(self, client, tmp_path, monkeypatch):
        _insert_distilled(
            tmp_path,
            monkeypatch,
            project_id="/tmp/proj-A",
            title="Bugfix A",
            narrative="Bugfix work",
            concepts=["gotcha"],
        )
        _insert_distilled(
            tmp_path,
            monkeypatch,
            project_id="/tmp/proj-A",
            title="Feature A",
            narrative="Feature work",
            concepts=["pattern"],
        )
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO distilled_summaries (session_id, project_id, source_tool, type, title) "
            "VALUES (?,?,?,?,?)",
            ("s1", "/tmp/proj-A", "claude", "feature", "Feature A2"),
        )
        row_id = cur.lastrowid
        conn.execute(
            "INSERT INTO distilled_summaries_fts(rowid, title, narrative, concepts, tags, project_id) "
            "VALUES (?,?,?,?,?,?)",
            (row_id, "Feature A2", "", "pattern", "", "/tmp/proj-A"),
        )
        conn.commit()
        conn.close()
        r = client.get("/search?q=Feature&type=feature")
        assert r.status_code == 200
        results = r.get_json()["results"]
        assert all(res["type"] == "feature" for res in results if res["id"].startswith("d:"))


# ---------------------------------------------------------------------------
# GET /timeline
# ---------------------------------------------------------------------------

class TestTimeline:
    def test_missing_anchor_returns_400(self, client):
        r = client.get("/timeline")
        assert r.status_code == 400

    def test_non_distilled_prefix_returns_400(self, client):
        r = client.get("/timeline?anchor_id=s:1")
        assert r.status_code == 400

    def test_unknown_anchor_returns_404(self, client):
        r = client.get("/timeline?anchor_id=d:999999")
        assert r.status_code == 404

    def test_valid_anchor_returns_timeline(self, client, tmp_path, monkeypatch):
        row_id = _insert_distilled(tmp_path, monkeypatch)
        r = client.get(f"/timeline?anchor_id=d:{row_id}")
        assert r.status_code == 200
        data = r.get_json()
        assert "timeline" in data
        assert any(item["id"] == f"d:{row_id}" for item in data["timeline"])

    def test_timeline_items_have_required_fields(self, client, tmp_path, monkeypatch):
        row_id = _insert_distilled(tmp_path, monkeypatch)
        r = client.get(f"/timeline?anchor_id=d:{row_id}")
        item = r.get_json()["timeline"][0]
        for field in ("id", "ts", "type", "title"):
            assert field in item

    def test_timeline_project_filter_excludes_other_projects(self, client, tmp_path, monkeypatch):
        a1 = _insert_distilled(
            tmp_path,
            monkeypatch,
            project_id="/tmp/proj-A",
            title="A-first",
            narrative="proj A first",
        )
        _insert_distilled(
            tmp_path,
            monkeypatch,
            project_id="/tmp/proj-B",
            title="B-middle",
            narrative="proj B middle",
        )
        _insert_distilled(
            tmp_path,
            monkeypatch,
            project_id="/tmp/proj-A",
            title="A-last",
            narrative="proj A last",
        )

        r = client.get(f"/timeline?anchor_id=d:{a1}&project_id=/tmp/proj-A")
        assert r.status_code == 200
        titles = [item["title"] for item in r.get_json()["timeline"]]
        assert "B-middle" not in titles

    def test_timeline_orders_with_same_timestamp_by_id(self, client, tmp_path, monkeypatch):
        ts = "2026-03-31 12:00:00"
        a1 = _insert_distilled(
            tmp_path, monkeypatch, project_id="/tmp/proj-A", title="A1", narrative="n1", ts=ts
        )
        a2 = _insert_distilled(
            tmp_path, monkeypatch, project_id="/tmp/proj-A", title="A2", narrative="n2", ts=ts
        )
        r = client.get(f"/timeline?anchor_id=d:{a2}&project_id=/tmp/proj-A&depth_before=2&depth_after=0")
        assert r.status_code == 200
        ids = [item["id"] for item in r.get_json()["timeline"]]
        assert ids[-2] == f"d:{a1}"
        assert ids[-1] == f"d:{a2}"


# ---------------------------------------------------------------------------
# GET /observation/<prefix>/<id>
# ---------------------------------------------------------------------------

class TestObservation:
    def test_distilled_not_found_returns_404(self, client):
        r = client.get("/observation/d/999999")
        assert r.status_code == 404

    def test_session_not_found_returns_404(self, client):
        r = client.get("/observation/s/999999")
        assert r.status_code == 404

    def test_invalid_prefix_returns_400(self, client):
        r = client.get("/observation/z/1")
        assert r.status_code == 400

    def test_distilled_returns_full_record(self, client, tmp_path, monkeypatch):
        row_id = _insert_distilled(tmp_path, monkeypatch)
        r = client.get(f"/observation/d/{row_id}")
        assert r.status_code == 200
        data = r.get_json()
        assert data["title"] == "Fixed null pointer"
        assert data["type"] == "bugfix"

    def test_distilled_facts_deserialized_as_list(self, client, tmp_path, monkeypatch):
        row_id = _insert_distilled(tmp_path, monkeypatch)
        r = client.get(f"/observation/d/{row_id}")
        assert isinstance(r.get_json()["facts"], list)

    def test_distilled_concepts_deserialized_as_list(self, client, tmp_path, monkeypatch):
        row_id = _insert_distilled(tmp_path, monkeypatch)
        r = client.get(f"/observation/d/{row_id}")
        assert isinstance(r.get_json()["concepts"], list)

    def test_session_summary_returned(self, client):
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO session_summaries "
            "(session_id, project_id, source_tool, request, learnings, concepts) "
            "VALUES (?,?,?,?,?,?)",
            ("s1", "/tmp/p", "mcp", "Fix the bug", "We learned X", json.dumps(["gotcha"])),
        )
        ss_id = cur.lastrowid
        conn.commit()
        conn.close()
        r = client.get(f"/observation/s/{ss_id}")
        assert r.status_code == 200
        data = r.get_json()
        assert data["request"] == "Fix the bug"
        assert isinstance(data["concepts"], list)

    def test_compat_principle_returned(self, client):
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO principles (project_tag, type, principle, impact_score, tags) "
            "VALUES (?,?,?,?,?)",
            ("myproj", "success", "Always validate inputs", 8, "security"),
        )
        p_id = cur.lastrowid
        conn.commit()
        conn.close()
        compat_id = p_id + 1_000_000
        r = client.get(f"/observation/c/{compat_id}")
        assert r.status_code == 200
        data = r.get_json()
        assert "Always validate inputs" in data["narrative"]


# ---------------------------------------------------------------------------
# POST /session_summaries
# ---------------------------------------------------------------------------

class TestPostSessionSummaries:
    def _valid_payload(self):
        return {
            "request": "Fix the login bug",
            "project_id": "/tmp/myproject",
            "source_tool": "mcp",
            "session_id": "sess-1",
            "investigation": "Checked auth middleware",
            "learnings": "JWT was expired",
            "next_steps": "Add refresh token",
            "concepts": ["security"],
        }

    def test_valid_returns_201(self, client):
        r = client.post("/session_summaries", json=self._valid_payload())
        assert r.status_code == 201

    def test_valid_returns_id(self, client):
        r = client.post("/session_summaries", json=self._valid_payload())
        data = r.get_json()
        assert "id" in data
        assert isinstance(data["id"], int)

    def test_missing_request_returns_400(self, client):
        payload = self._valid_payload()
        del payload["request"]
        r = client.post("/session_summaries", json=payload)
        assert r.status_code == 400

    def test_missing_project_id_returns_400(self, client):
        payload = self._valid_payload()
        del payload["project_id"]
        r = client.post("/session_summaries", json=payload)
        assert r.status_code == 400

    def test_missing_source_tool_returns_400(self, client):
        payload = self._valid_payload()
        del payload["source_tool"]
        r = client.post("/session_summaries", json=payload)
        assert r.status_code == 400

    def test_private_stripped_from_summary(self, client):
        payload = self._valid_payload()
        payload["learnings"] = "key=<private>TOP_SECRET</private> rest"
        client.post("/session_summaries", json=payload)
        conn = get_conn()
        row = conn.execute("SELECT learnings FROM session_summaries ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        assert "TOP_SECRET" not in row["learnings"]

    def test_stored_in_db(self, client):
        client.post("/session_summaries", json=self._valid_payload())
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM session_summaries WHERE request='Fix the login bug'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["source_tool"] == "mcp"


# ---------------------------------------------------------------------------
# GET /session_summaries
# ---------------------------------------------------------------------------

class TestGetSessionSummaries:
    def test_missing_project_id_returns_400(self, client):
        r = client.get("/session_summaries")
        assert r.status_code == 400

    def test_returns_results_list(self, client):
        r = client.get("/session_summaries?project_id=/tmp/myproject")
        assert r.status_code == 200
        assert "results" in r.get_json()

    def test_returns_stored_summary(self, client):
        client.post("/session_summaries", json={
            "request": "Unique request XYZ",
            "project_id": "/tmp/proj-get",
            "source_tool": "mcp",
        })
        r = client.get("/session_summaries?project_id=/tmp/proj-get")
        results = r.get_json()["results"]
        assert any(res["request"] == "Unique request XYZ" for res in results)

    def test_session_filter(self, client):
        for sess in ("sess-A", "sess-B"):
            client.post("/session_summaries", json={
                "request": f"request from {sess}",
                "project_id": "/tmp/proj-sess",
                "source_tool": "mcp",
                "session_id": sess,
            })
        r = client.get("/session_summaries?project_id=/tmp/proj-sess&session_id=sess-A")
        results = r.get_json()["results"]
        assert all(res["session_id"] == "sess-A" for res in results)

    def test_k_limits_results(self, client):
        for i in range(5):
            client.post("/session_summaries", json={
                "request": f"request {i}",
                "project_id": "/tmp/proj-k",
                "source_tool": "mcp",
            })
        r = client.get("/session_summaries?project_id=/tmp/proj-k&k=2")
        assert len(r.get_json()["results"]) <= 2

    def test_concepts_deserialized_as_list(self, client):
        client.post("/session_summaries", json={
            "request": "test concepts",
            "project_id": "/tmp/proj-c",
            "source_tool": "mcp",
            "concepts": ["security", "pattern"],
        })
        r = client.get("/session_summaries?project_id=/tmp/proj-c")
        result = r.get_json()["results"][0]
        assert isinstance(result["concepts"], list)


# ---------------------------------------------------------------------------
# POST /events/batch
# ---------------------------------------------------------------------------

class TestBatchEvents:
    def _make_event(self, seq=1, **overrides):
        evt = {
            "session_id": "sess-batch",
            "project_id": "/tmp/batch-project",
            "source_tool": "claude",
            "event_type": "tool_use",
            "tool_name": "Read",
            "payload": {"file": "main.py"},
            "seq": seq,
        }
        evt.update(overrides)
        return evt

    def test_batch_returns_207(self, client):
        r = client.post("/events/batch", json=[self._make_event()])
        assert r.status_code == 207

    def test_batch_returns_results_list(self, client):
        events = [self._make_event(seq=i) for i in range(3)]
        r = client.post("/events/batch", json=events)
        data = r.get_json()
        assert "results" in data
        assert len(data["results"]) == 3

    def test_batch_all_succeed(self, client):
        events = [self._make_event(seq=i) for i in range(3)]
        r = client.post("/events/batch", json=events)
        results = r.get_json()["results"]
        assert all(res["status"] in ("ok", "duplicate") for res in results)

    def test_batch_empty_list_returns_207(self, client):
        r = client.post("/events/batch", json=[])
        assert r.status_code == 207
        assert r.get_json()["results"] == []

    def test_batch_stores_all_events_in_db(self, client):
        # Each event must have a unique payload so content_hash differs and none are deduplicated.
        events = [self._make_event(seq=i, payload={"file": f"file_{i}.py"}) for i in range(4)]
        client.post("/events/batch", json=events)
        conn = get_conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE project_id=?", ("/tmp/batch-project",)
        ).fetchone()[0]
        conn.close()
        assert count == 4

    def test_batch_deduplicates_within_same_batch(self, client):
        """Two identical events in one batch → second is duplicate, only 1 row stored."""
        evt = self._make_event(seq=99, payload={"x": "same"})
        r = client.post("/events/batch", json=[evt, evt])
        results = r.get_json()["results"]
        statuses = [res["status"] for res in results]
        assert "ok" in statuses
        assert "duplicate" in statuses

    def test_batch_invalid_event_does_not_abort_others(self, client):
        """A malformed event in the batch returns an error entry but others still succeed."""
        good = self._make_event(seq=10)
        bad = {"session_id": "x"}  # missing required fields
        r = client.post("/events/batch", json=[good, bad])
        results = r.get_json()["results"]
        assert results[0]["status"] == "ok"
        assert "error" in results[1]  # malformed event gets {"error": ..., "fields": [...]}

    def test_batch_non_list_body_returns_400(self, client):
        r = client.post("/events/batch", json={"not": "a list"})
        assert r.status_code == 400

    def test_batch_private_tags_stripped(self, client):
        evt = self._make_event(
            seq=50,
            payload={"content": "public <private>SECRET</private> end"},
        )
        client.post("/events/batch", json=[evt])
        conn = get_conn()
        row = conn.execute(
            "SELECT payload FROM events WHERE project_id=? ORDER BY id DESC LIMIT 1",
            ("/tmp/batch-project",),
        ).fetchone()
        conn.close()
        assert "SECRET" not in row["payload"]
        assert "public" in row["payload"]


# ---------------------------------------------------------------------------
# Additional inference error-path tests
# ---------------------------------------------------------------------------

class TestInferenceErrorPaths:
    """Test that provider backends exit gracefully when deps or keys are missing."""

    def test_missing_anthropic_key_exits(self, monkeypatch):
        import forgememo.inference as inference
        monkeypatch.setattr("forgememo.inference.cfg.get_provider", lambda: "anthropic")
        monkeypatch.setattr("forgememo.inference.cfg.get_model", lambda _: "claude-haiku-4-5-20251001")
        monkeypatch.setattr("forgememo.inference.cfg.get_api_key", lambda _: None)
        with pytest.raises(SystemExit) as exc_info:
            inference.call("hello")
        assert exc_info.value.code == 1

    def test_missing_openai_key_exits(self, monkeypatch):
        import forgememo.inference as inference
        from openai import OpenAI  # ensure importable

        monkeypatch.setattr("forgememo.inference.cfg.get_provider", lambda: "openai")
        monkeypatch.setattr("forgememo.inference.cfg.get_model", lambda _: "gpt-4o")
        monkeypatch.setattr("forgememo.inference.cfg.get_api_key", lambda _: None)
        with pytest.raises(SystemExit) as exc_info:
            inference.call("hello")
        assert exc_info.value.code == 1

    def test_claude_code_provider_routes_correctly(self, monkeypatch):
        import forgememo.inference as inference
        called_with = {}

        def fake_claude_code(prompt, max_tokens, model):
            called_with["prompt"] = prompt
            called_with["max_tokens"] = max_tokens
            return "response"

        monkeypatch.setattr("forgememo.inference.cfg.get_provider", lambda: "claude_code")
        monkeypatch.setattr("forgememo.inference.cfg.get_model", lambda _: "claude-code")
        monkeypatch.setattr("forgememo.inference._call_claude_code", fake_claude_code)
        result = inference.call("test prompt", max_tokens=42)
        assert result == "response"
        assert called_with["max_tokens"] == 42

    def test_call_passes_explicit_model_override(self, monkeypatch):
        import forgememo.inference as inference

        received = {}

        def fake_anthropic(prompt, max_tokens, model):
            received["model"] = model
            return "ok"

        monkeypatch.setattr("forgememo.inference.cfg.get_provider", lambda: "anthropic")
        monkeypatch.setattr("forgememo.inference.cfg.get_model", lambda _: "default-model")
        monkeypatch.setattr("forgememo.inference._call_anthropic", fake_anthropic)
        inference.call("hello", model="custom-model-override")
        assert received["model"] == "custom-model-override"
