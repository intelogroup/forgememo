"""
Tests for forgememo/core.py — the legacy CLI data layer
(traces + principles tables, FTS search, stats, backup, export).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pytest

import forgememo.core as core
import forgememo.storage as storage_module


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def _ns(**kwargs) -> argparse.Namespace:
    """Build an argparse.Namespace from keyword args."""
    return argparse.Namespace(**kwargs)


def _save_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        type="note",
        content="default content",
        project="testproject",
        session="sess-1",
        principle=None,
        score=5,
        tags=None,
        distill=False,
    )
    defaults.update(overrides)
    return _ns(**defaults)


@pytest.fixture(autouse=True)
def isolated_core(tmp_path, monkeypatch):
    """Give each test its own DB in tmp_path and initialize the legacy schema."""
    db_file = tmp_path / "core_test.db"
    monkeypatch.setattr(core, "DB_PATH", db_file)
    # Also patch storage so get_conn in any module picks up the same file
    monkeypatch.setattr(storage_module, "DB_PATH", db_file)
    # Initialize legacy schema via cmd_init
    core.cmd_init(_ns())


# ---------------------------------------------------------------------------
# cmd_init / get_conn
# ---------------------------------------------------------------------------

class TestInit:
    def test_creates_traces_table(self):
        conn = core.get_conn()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "traces" in tables

    def test_creates_principles_table(self):
        conn = core.get_conn()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "principles" in tables

    def test_creates_fts_tables(self):
        conn = core.get_conn()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "traces_fts" in tables
        assert "principles_fts" in tables

    def test_idempotent(self):
        """Running cmd_init twice should not raise."""
        core.cmd_init(_ns())

    def test_get_conn_wal_mode(self):
        conn = core.get_conn()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"


# ---------------------------------------------------------------------------
# detect_project
# ---------------------------------------------------------------------------

class TestDetectProject:
    def test_returns_git_toplevel_name(self, monkeypatch, tmp_path):
        repo = tmp_path / "myrepo"

        def _fake_run(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = str(repo) + "\n"
                stderr = ""
            return R()

        monkeypatch.setattr(core.subprocess, "run", _fake_run)
        assert core.detect_project() == "myrepo"

    def test_falls_back_to_cwd_name(self, monkeypatch, tmp_path):
        def _fail(cmd, **kwargs):
            class R:
                returncode = 1
                stdout = ""
                stderr = "not a git repo"
            return R()

        monkeypatch.setattr(core.subprocess, "run", _fail)
        monkeypatch.chdir(tmp_path / "somedir")
        (tmp_path / "somedir").mkdir(exist_ok=True)
        result = core.detect_project()
        assert result == "somedir"

    def test_handles_missing_git_binary(self, monkeypatch):
        def _raise(*a, **kw):
            raise FileNotFoundError("git not found")
        monkeypatch.setattr(core.subprocess, "run", _raise)
        result = core.detect_project()
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# _sanitize_fts_query
# ---------------------------------------------------------------------------

class TestSanitizeFts:
    def test_strips_double_quotes(self):
        assert '"' not in core._sanitize_fts_query('hello "world"')

    def test_escapes_single_quotes(self):
        result = core._sanitize_fts_query("it's")
        assert "''" in result

    def test_passthrough_normal_query(self):
        assert core._sanitize_fts_query("WAL mode database") == "WAL mode database"

    def test_empty_string(self):
        assert core._sanitize_fts_query("") == ""


# ---------------------------------------------------------------------------
# cmd_save / insert_principle
# ---------------------------------------------------------------------------

class TestCmdSave:
    def test_saves_trace_to_db(self, capsys):
        core.cmd_save(_save_args(content="Fixed null pointer", type="success"))
        conn = core.get_conn()
        row = conn.execute("SELECT * FROM traces").fetchone()
        conn.close()
        assert row is not None
        assert row["content"] == "Fixed null pointer"

    def test_returns_trace_id_in_output(self, capsys):
        core.cmd_save(_save_args())
        out = capsys.readouterr().out
        assert "Saved trace #" in out

    def test_all_valid_types_accepted(self, capsys):
        for t in ("success", "failure", "plan", "note"):
            core.cmd_save(_save_args(type=t, content=f"content for {t}"))
        conn = core.get_conn()
        count = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
        conn.close()
        assert count == 4

    def test_with_manual_principle(self, capsys):
        core.cmd_save(_save_args(principle="Always validate inputs", score=8))
        conn = core.get_conn()
        p = conn.execute("SELECT * FROM principles").fetchone()
        conn.close()
        assert p is not None
        assert p["principle"] == "Always validate inputs"
        assert p["impact_score"] == 8

    def test_manual_principle_marks_trace_distilled(self):
        core.cmd_save(_save_args(principle="Keep it simple"))
        conn = core.get_conn()
        row = conn.execute("SELECT distilled FROM traces").fetchone()
        conn.close()
        assert row["distilled"] == 1

    def test_no_principle_leaves_trace_undistilled(self):
        core.cmd_save(_save_args())
        conn = core.get_conn()
        row = conn.execute("SELECT distilled FROM traces").fetchone()
        conn.close()
        assert row["distilled"] == 0

    def test_with_tags(self):
        core.cmd_save(_save_args(principle="Use WAL mode", tags="database,sqlite"))
        conn = core.get_conn()
        p = conn.execute("SELECT tags FROM principles").fetchone()
        conn.close()
        assert "database" in p["tags"]

    def test_project_stored(self):
        core.cmd_save(_save_args(project="myproject"))
        conn = core.get_conn()
        row = conn.execute("SELECT project_tag FROM traces").fetchone()
        conn.close()
        assert row["project_tag"] == "myproject"

    def test_distill_flag_calls_inference(self, monkeypatch, capsys):
        monkeypatch.setattr(
            core, "distill_via_api",
            lambda content, trace_type: {
                "principle": "Mock principle", "impact_score": 7, "tags": ["test"]
            }
        )
        core.cmd_save(_save_args(distill=True, content="Fixed auth bug"))
        conn = core.get_conn()
        p = conn.execute("SELECT * FROM principles").fetchone()
        conn.close()
        assert p["principle"] == "Mock principle"

    def test_content_indexed_in_fts(self, capsys):
        core.cmd_save(_save_args(content="unique_token_xyz123"))
        conn = core.get_conn()
        rows = conn.execute(
            "SELECT rowid FROM traces_fts WHERE traces_fts MATCH 'unique_token_xyz123'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1

    def test_session_stored(self):
        core.cmd_save(_save_args(session="my-session-abc"))
        conn = core.get_conn()
        row = conn.execute("SELECT session_id FROM traces").fetchone()
        conn.close()
        assert row["session_id"] == "my-session-abc"


# ---------------------------------------------------------------------------
# insert_principle
# ---------------------------------------------------------------------------

class TestInsertPrinciple:
    def _seed_trace(self) -> int:
        conn = core.get_conn()
        cur = conn.execute(
            "INSERT INTO traces (project_tag, type, content) VALUES (?, ?, ?)",
            ("proj", "note", "some content"),
        )
        tid = cur.lastrowid
        conn.commit()
        conn.close()
        return tid

    def test_inserts_principle_row(self):
        tid = self._seed_trace()
        conn = core.get_conn()
        core.insert_principle(conn, tid, "proj", "note", "Test principle", 6, "tag1")
        conn.commit()
        p = conn.execute("SELECT * FROM principles").fetchone()
        conn.close()
        assert p["principle"] == "Test principle"
        assert p["impact_score"] == 6

    def test_principle_indexed_in_fts(self):
        tid = self._seed_trace()
        conn = core.get_conn()
        core.insert_principle(conn, tid, "proj", "note", "unique_principle_abc", 5, None)
        conn.commit()
        rows = conn.execute(
            "SELECT rowid FROM principles_fts WHERE principles_fts MATCH 'unique_principle_abc'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1

    def test_returns_principle_id(self):
        tid = self._seed_trace()
        conn = core.get_conn()
        p_id = core.insert_principle(conn, tid, "proj", "note", "A principle", 5, None)
        conn.commit()
        conn.close()
        assert isinstance(p_id, int)
        assert p_id > 0


# ---------------------------------------------------------------------------
# distill_via_api
# ---------------------------------------------------------------------------

class TestDistillViaApi:
    def test_returns_dict_with_principle(self, monkeypatch):
        payload = {"principle": "Always use WAL mode", "impact_score": 8, "tags": ["db"]}
        monkeypatch.setattr("forgememo.inference.call", lambda *a, **kw: json.dumps(payload))
        result = core.distill_via_api("WAL is faster for concurrent reads", "note")
        assert result["principle"] == "Always use WAL mode"
        assert result["impact_score"] == 8

    def test_strips_markdown_fences(self, monkeypatch):
        payload = {"principle": "Use indexes", "impact_score": 7, "tags": []}
        wrapped = f"```json\n{json.dumps(payload)}\n```"
        monkeypatch.setattr("forgememo.inference.call", lambda *a, **kw: wrapped)
        result = core.distill_via_api("some content", "note")
        assert result["principle"] == "Use indexes"

    def test_raises_value_error_on_bad_json(self, monkeypatch):
        monkeypatch.setattr("forgememo.inference.call", lambda *a, **kw: "not json {{{")
        with pytest.raises(ValueError, match="non-JSON"):
            core.distill_via_api("content", "note")


# ---------------------------------------------------------------------------
# cmd_retrieve
# ---------------------------------------------------------------------------

class TestCmdRetrieve:
    def _seed(self, content="auth bug fix", project="myproj", trace_type="success",
              principle=None):
        core.cmd_save(_save_args(
            content=content, project=project, type=trace_type,
            principle=principle, score=7,
        ))

    def _retrieve(self, query, project=None, type_=None, k=10, fmt="md"):
        return _ns(query=query, project=project, type=type_, k=k, format=fmt)

    def test_finds_saved_principle(self, capsys):
        self._seed(principle="Always validate JWT tokens")
        core.cmd_retrieve(self._retrieve("JWT"))
        out = capsys.readouterr().out
        assert "Always validate JWT tokens" in out

    def test_finds_saved_trace(self, capsys):
        self._seed(content="Fixed race condition in worker queue")
        core.cmd_retrieve(self._retrieve("race condition"))
        out = capsys.readouterr().out
        assert "race condition" in out or "Raw Traces" in out

    def test_json_format_returns_parseable_json(self, capsys):
        self._seed(principle="Keep DB connections short")
        core.cmd_retrieve(self._retrieve("DB connections", fmt="json"))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "principles" in data
        assert "traces" in data

    def test_no_match_returns_empty(self, capsys):
        self._seed(content="totally unrelated stuff")
        core.cmd_retrieve(self._retrieve("zzznomatch999"))
        out = capsys.readouterr().out
        assert "No principles found" in out

    def test_project_filter_excludes_other_projects(self, capsys):
        self._seed(content="project A content", project="proj-A", principle="Principle A")
        self._seed(content="project B content", project="proj-B", principle="Principle B")
        core.cmd_retrieve(self._retrieve("content", project="proj-A", fmt="json"))
        data = json.loads(capsys.readouterr().out)
        for p in data["principles"]:
            assert p["project_tag"] == "proj-A"

    def test_type_filter(self, capsys):
        self._seed(content="deployment failure", trace_type="failure",
                   principle="Always have rollback plan")
        self._seed(content="great new feature", trace_type="success",
                   principle="Ship incrementally")
        core.cmd_retrieve(self._retrieve("plan", type_="failure", fmt="json"))
        data = json.loads(capsys.readouterr().out)
        for p in data["principles"]:
            assert p["type"] == "failure"


# ---------------------------------------------------------------------------
# cmd_stats
# ---------------------------------------------------------------------------

class TestCmdStats:
    def test_shows_trace_count(self, capsys):
        core.cmd_save(_save_args(content="trace 1"))
        core.cmd_save(_save_args(content="trace 2"))
        core.cmd_stats(_ns(project=None))
        out = capsys.readouterr().out
        assert "Traces:" in out
        assert "2" in out

    def test_shows_principle_count(self, capsys):
        core.cmd_save(_save_args(principle="A principle"))
        core.cmd_stats(_ns(project=None))
        out = capsys.readouterr().out
        assert "Principles:" in out

    def test_project_filter(self, capsys):
        core.cmd_save(_save_args(project="proj-X", content="trace for X"))
        core.cmd_save(_save_args(project="proj-Y", content="trace for Y"))
        core.cmd_stats(_ns(project="proj-X"))
        out = capsys.readouterr().out
        assert "proj-X" in out

    def test_empty_db_shows_zeros(self, capsys):
        core.cmd_stats(_ns(project=None))
        out = capsys.readouterr().out
        assert "0" in out

    def test_shows_undistilled_count(self, capsys):
        core.cmd_save(_save_args())  # undistilled
        core.cmd_save(_save_args(principle="auto principle"))  # distilled
        core.cmd_stats(_ns(project=None))
        out = capsys.readouterr().out
        assert "1 undistilled" in out


# ---------------------------------------------------------------------------
# cmd_backup
# ---------------------------------------------------------------------------

class TestCmdBackup:
    def test_creates_backup_file(self, tmp_path):
        dest = tmp_path / "backup.db"
        core.cmd_backup(_ns(dest=str(dest)))
        assert dest.exists()
        assert dest.stat().st_size > 0

    def test_backup_is_valid_sqlite(self, tmp_path):
        dest = tmp_path / "backup.db"
        core.cmd_backup(_ns(dest=str(dest)))
        conn = sqlite3.connect(str(dest))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "traces" in tables

    def test_backup_contains_existing_data(self, tmp_path):
        core.cmd_save(_save_args(content="important trace"))
        dest = tmp_path / "backup_with_data.db"
        core.cmd_backup(_ns(dest=str(dest)))
        conn = sqlite3.connect(str(dest))
        count = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
        conn.close()
        assert count == 1

    def test_auto_dest_when_none(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(core, "DB_PATH", tmp_path / "core_test.db")
        core.cmd_init(_ns())
        core.cmd_backup(_ns(dest=None))
        out = capsys.readouterr().out
        assert "Backup saved to" in out


# ---------------------------------------------------------------------------
# cmd_export
# ---------------------------------------------------------------------------

class TestCmdExport:
    def _seed_principle(self, text, project="proj", score=7):
        core.cmd_save(_save_args(principle=text, project=project, score=score))

    def test_exports_principles(self, capsys):
        self._seed_principle("Always use connection pools")
        core.cmd_export(_ns(project=None, k=10))
        out = capsys.readouterr().out
        assert "Always use connection pools" in out

    def test_empty_db_prints_no_principles(self, capsys):
        core.cmd_export(_ns(project=None, k=10))
        out = capsys.readouterr().out
        assert "No principles found" in out

    def test_project_filter(self, capsys):
        self._seed_principle("Principle A", project="proj-A")
        self._seed_principle("Principle B", project="proj-B")
        core.cmd_export(_ns(project="proj-A", k=10))
        out = capsys.readouterr().out
        assert "Principle A" in out
        assert "Principle B" not in out

    def test_k_limits_results(self, capsys):
        for i in range(5):
            self._seed_principle(f"Principle number {i}")
        core.cmd_export(_ns(project=None, k=3))
        out = capsys.readouterr().out
        # Count how many "score:" appear — one per principle
        assert out.count("score:") <= 3

    def test_output_includes_score_and_type(self, capsys):
        self._seed_principle("Use WAL mode for SQLite")
        core.cmd_export(_ns(project=None, k=10))
        out = capsys.readouterr().out
        assert "score:" in out
        assert "note" in out or "success" in out or "failure" in out or "plan" in out
