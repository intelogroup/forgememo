from __future__ import annotations

import json

import forgememo.storage as storage_module
from forgememo.storage import get_conn, init_db
import forgememo.query_tool as query_tool


def _insert_trace(project: str, trace_type: str, content: str, distilled: int = 0) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO traces (project_tag, type, content, distilled) VALUES (?, ?, ?, ?)",
        (project, trace_type, content, distilled),
    )
    trace_id = cur.lastrowid
    conn.execute(
        "INSERT INTO traces_fts(rowid, content, project_tag, type) VALUES (?, ?, ?, ?)",
        (trace_id, content, project, trace_type),
    )
    conn.commit()
    conn.close()
    return trace_id


def _insert_principle(project: str, trace_type: str, principle: str, score: int, tags: str | None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO principles (project_tag, type, principle, impact_score, tags) VALUES (?, ?, ?, ?, ?)",
        (project, trace_type, principle, score, tags),
    )
    pid = cur.lastrowid
    conn.execute(
        "INSERT INTO principles_fts(rowid, principle, project_tag, tags) VALUES (?, ?, ?, ?)",
        (pid, principle, project, tags or ""),
    )
    conn.commit()
    conn.close()
    return pid


def _setup_db(tmp_path, monkeypatch):
    db_file = tmp_path / "query_tool.db"
    monkeypatch.setattr(storage_module, "DB_PATH", db_file)
    monkeypatch.setattr(query_tool, "DB_PATH", db_file)
    init_db()
    return db_file


def test_search_principles_filters_by_score(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _insert_principle("projA", "failure", "Avoid null deref", 8, "gotcha")
    _insert_principle("projA", "note", "Minor note", 2, None)

    out = query_tool.search_principles("null", k=5, project="projA", min_score=5)
    assert "Avoid null deref" in out
    assert "Minor note" not in out


def test_search_traces_distilled_only(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _insert_trace("projA", "note", "undistilled trace", distilled=0)
    _insert_trace("projA", "note", "distilled trace", distilled=1)

    out = query_tool.search_traces("trace", k=5, project="projA", distilled_only=True)
    assert "distilled trace" in out
    assert "undistilled trace" not in out


def test_list_top_principles_orders_by_score(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _insert_principle("projA", "success", "Higher score", 9, None)
    _insert_principle("projA", "success", "Lower score", 3, None)

    out = query_tool.list_top_principles(project="projA", limit=2)
    lines = out.splitlines()
    idx = next(i for i, l in enumerate(lines) if "[9/10]" in l)
    assert "Higher score" in lines[idx + 1]
    assert "Higher score" in out


def test_get_project_summary_includes_counts(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _insert_trace("projA", "note", "t1", distilled=1)
    _insert_trace("projA", "failure", "t2", distilled=0)
    _insert_principle("projA", "note", "p1", 5, None)

    out = query_tool.get_project_summary("projA")
    assert "Traces:" in out
    assert "Principles:" in out
    assert "p1" in out


def test_get_forgemem_status_counts(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _insert_trace("projA", "note", "t1", distilled=0)
    _insert_principle("projA", "note", "p1", 5, None)

    out = query_tool.get_forgemem_status()
    assert "Traces:" in out
    assert "Principles:" in out
