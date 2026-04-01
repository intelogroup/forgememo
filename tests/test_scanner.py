"""
Tests for forgememo/scanner.py.

Covers: find_git_repos, project_from_md_path, md5, is_duplicate,
        _extract_via_inference, and the locked_hashes context manager.
"""

from __future__ import annotations

import json

import pytest

import forgememo.scanner as scanner
import forgememo.storage as storage_module
from forgememo.storage import get_conn, init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own SQLite DB and a writable log file."""
    db_file = tmp_path / "scanner_test.db"
    monkeypatch.setattr(storage_module, "DB_PATH", db_file)
    monkeypatch.setattr(scanner, "LOG_FILE", tmp_path / "daily_scan.log")
    init_db()


# ---------------------------------------------------------------------------
# find_git_repos
# ---------------------------------------------------------------------------

class TestFindGitRepos:
    def test_discovers_direct_git_repo(self, tmp_path, monkeypatch):
        repo = tmp_path / "myproject"
        repo.mkdir()
        (repo / ".git").mkdir()
        monkeypatch.setattr(scanner, "SCAN_ROOT", tmp_path)
        monkeypatch.setattr(scanner, "SKIP_DIRS", set())
        repos = scanner.find_git_repos()
        assert repo in repos

    def test_skips_skip_dirs(self, tmp_path, monkeypatch):
        skip = tmp_path / "Forgememo"
        skip.mkdir()
        (skip / ".git").mkdir()
        monkeypatch.setattr(scanner, "SCAN_ROOT", tmp_path)
        monkeypatch.setattr(scanner, "SKIP_DIRS", {"Forgememo"})
        repos = scanner.find_git_repos()
        assert skip not in repos

    def test_skips_dotdirs(self, tmp_path, monkeypatch):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / ".git").mkdir()
        monkeypatch.setattr(scanner, "SCAN_ROOT", tmp_path)
        monkeypatch.setattr(scanner, "SKIP_DIRS", set())
        repos = scanner.find_git_repos()
        assert hidden not in repos

    def test_discovers_nested_repos_one_level_deep(self, tmp_path, monkeypatch):
        org = tmp_path / "myorg"
        org.mkdir()
        nested = org / "subrepo"
        nested.mkdir()
        (nested / ".git").mkdir()
        monkeypatch.setattr(scanner, "SCAN_ROOT", tmp_path)
        monkeypatch.setattr(scanner, "SKIP_DIRS", set())
        repos = scanner.find_git_repos()
        assert nested in repos

    def test_nested_skip_dirs_honored(self, tmp_path, monkeypatch):
        org = tmp_path / "myorg"
        org.mkdir()
        skipped = org / "node_modules"
        skipped.mkdir()
        (skipped / ".git").mkdir()
        monkeypatch.setattr(scanner, "SCAN_ROOT", tmp_path)
        monkeypatch.setattr(scanner, "SKIP_DIRS", {"node_modules"})
        repos = scanner.find_git_repos()
        assert skipped not in repos

    def test_returns_sorted_list(self, tmp_path, monkeypatch):
        for name in ("zrepo", "arepo", "mrepo"):
            r = tmp_path / name
            r.mkdir()
            (r / ".git").mkdir()
        monkeypatch.setattr(scanner, "SCAN_ROOT", tmp_path)
        monkeypatch.setattr(scanner, "SKIP_DIRS", set())
        repos = scanner.find_git_repos()
        names = [r.name for r in repos]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# project_from_md_path
# ---------------------------------------------------------------------------

class TestProjectFromMdPath:
    def test_standard_developer_slug(self, tmp_path):
        # Mimics ~/.claude/projects/-Users-alice-Developer-Forgememo/memory/foo.md
        proj_dir = tmp_path / "-Users-alice-Developer-Forgememo" / "memory"
        proj_dir.mkdir(parents=True)
        md = proj_dir / "foo.md"
        md.touch()
        assert scanner.project_from_md_path(md) == "Forgememo"

    def test_hyphenated_project_name(self, tmp_path):
        proj_dir = tmp_path / "-Users-alice-Developer-my-cool-project" / "memory"
        proj_dir.mkdir(parents=True)
        md = proj_dir / "bar.md"
        md.touch()
        assert scanner.project_from_md_path(md) == "my-cool-project"

    def test_no_developer_segment_falls_back_to_last_part(self, tmp_path):
        proj_dir = tmp_path / "-Users-alice-someplace" / "memory"
        proj_dir.mkdir(parents=True)
        md = proj_dir / "baz.md"
        md.touch()
        result = scanner.project_from_md_path(md)
        assert result == "someplace"

    def test_developer_with_nothing_after_it_returns_global(self, tmp_path):
        proj_dir = tmp_path / "-Users-alice-Developer" / "memory"
        proj_dir.mkdir(parents=True)
        md = proj_dir / "x.md"
        md.touch()
        assert scanner.project_from_md_path(md) == "global"


# ---------------------------------------------------------------------------
# md5
# ---------------------------------------------------------------------------

class TestMd5:
    def test_deterministic(self):
        assert scanner.md5("hello") == scanner.md5("hello")

    def test_different_inputs_produce_different_hashes(self):
        assert scanner.md5("hello") != scanner.md5("world")

    def test_returns_hex_string(self):
        h = scanner.md5("test")
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# is_duplicate
# ---------------------------------------------------------------------------

class TestIsDuplicate:
    def test_returns_false_on_empty_db(self):
        assert scanner.is_duplicate("some new content", "myproject") is False

    def test_returns_false_for_different_project(self):
        conn = get_conn()
        conn.execute(
            "INSERT INTO events (session_id, project_id, source_tool, event_type, payload, seq, content_hash) "
            "VALUES (?,?,?,?,?,?,?)",
            ("s1", "other_project", "scanner", "scanner_learning",
             json.dumps({"content": "content that already exists"}), 1, "abc"),
        )
        conn.commit()
        conn.close()
        assert scanner.is_duplicate("content that already exists", "myproject") is False

    def test_detects_duplicate_v2_event(self):
        content = "We discovered that WAL mode is required for concurrent writes"
        conn = get_conn()
        conn.execute(
            "INSERT INTO events (session_id, project_id, source_tool, event_type, payload, seq, content_hash) "
            "VALUES (?,?,?,?,?,?,?)",
            ("s1", "myproject", "scanner", "scanner_learning",
             json.dumps({"content": content}), 1, "hash1"),
        )
        conn.commit()
        conn.close()
        assert scanner.is_duplicate(content, "myproject") is True

    def test_fingerprint_uses_first_120_chars(self):
        long_content = "A" * 200
        fingerprint = long_content[:120]
        conn = get_conn()
        conn.execute(
            "INSERT INTO events (session_id, project_id, source_tool, event_type, payload, seq, content_hash) "
            "VALUES (?,?,?,?,?,?,?)",
            ("s1", "proj", "scanner", "scanner_learning",
             json.dumps({"content": fingerprint}), 1, "h2"),
        )
        conn.commit()
        conn.close()
        assert scanner.is_duplicate(long_content, "proj") is True


# ---------------------------------------------------------------------------
# _extract_via_inference
# ---------------------------------------------------------------------------

class TestExtractViaInference:
    def _make_learning(self, **overrides):
        base = {
            "type": "success",
            "content": "We fixed the retry logic",
            "principle": "Always retry with backoff",
            "impact_score": 7,
            "tags": ["reliability"],
        }
        base.update(overrides)
        return base

    def test_returns_valid_learnings(self, monkeypatch):
        payload = {"learnings": [self._make_learning()]}
        monkeypatch.setattr(scanner.inference, "call", lambda *a, **kw: json.dumps(payload))
        result = scanner._extract_via_inference("some prompt")
        assert len(result) == 1
        assert result[0]["content"] == "We fixed the retry logic"

    def test_strips_markdown_code_fences(self, monkeypatch):
        payload = json.dumps({"learnings": [self._make_learning()]})
        wrapped = f"```json\n{payload}\n```"
        monkeypatch.setattr(scanner.inference, "call", lambda *a, **kw: wrapped)
        result = scanner._extract_via_inference("prompt")
        assert len(result) == 1

    def test_strips_plain_code_fences(self, monkeypatch):
        payload = json.dumps({"learnings": [self._make_learning()]})
        wrapped = f"```\n{payload}\n```"
        monkeypatch.setattr(scanner.inference, "call", lambda *a, **kw: wrapped)
        result = scanner._extract_via_inference("prompt")
        assert len(result) == 1

    def test_normalizes_invalid_type_to_note(self, monkeypatch):
        learning = self._make_learning(type="bogus")
        payload = {"learnings": [learning]}
        monkeypatch.setattr(scanner.inference, "call", lambda *a, **kw: json.dumps(payload))
        result = scanner._extract_via_inference("prompt")
        assert result[0]["type"] == "note"

    def test_rejects_items_with_empty_content(self, monkeypatch):
        good = self._make_learning()
        bad = self._make_learning(content="")
        payload = {"learnings": [good, bad]}
        monkeypatch.setattr(scanner.inference, "call", lambda *a, **kw: json.dumps(payload))
        result = scanner._extract_via_inference("prompt")
        assert len(result) == 1
        assert result[0]["content"] == "We fixed the retry logic"

    def test_rejects_non_dict_items(self, monkeypatch):
        payload = {"learnings": [self._make_learning(), "not a dict", 42]}
        monkeypatch.setattr(scanner.inference, "call", lambda *a, **kw: json.dumps(payload))
        result = scanner._extract_via_inference("prompt")
        assert len(result) == 1

    def test_returns_empty_on_json_error(self, monkeypatch):
        monkeypatch.setattr(scanner.inference, "call", lambda *a, **kw: "this is not json {{{")
        result = scanner._extract_via_inference("prompt")
        assert result == []

    def test_returns_empty_on_system_exit(self, monkeypatch):
        def raise_exit(*a, **kw):
            raise SystemExit(1)
        monkeypatch.setattr(scanner.inference, "call", raise_exit)
        result = scanner._extract_via_inference("prompt")
        assert result == []

    def test_returns_empty_when_learnings_key_missing(self, monkeypatch):
        monkeypatch.setattr(scanner.inference, "call", lambda *a, **kw: json.dumps({"other": []}))
        result = scanner._extract_via_inference("prompt")
        assert result == []

    def test_all_valid_types_accepted(self, monkeypatch):
        for t in ("success", "failure", "plan", "note"):
            learning = self._make_learning(type=t, content=f"Content for {t}")
            payload = {"learnings": [learning]}
            monkeypatch.setattr(scanner.inference, "call", lambda *a, p=payload, **kw: json.dumps(p))
            result = scanner._extract_via_inference("prompt")
            assert result[0]["type"] == t


# ---------------------------------------------------------------------------
# locked_hashes
# ---------------------------------------------------------------------------

class TestLockedHashes:
    def test_empty_file_yields_empty_dict(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scanner, "HASH_FILE", tmp_path / "hashes.json")
        with scanner.locked_hashes() as h:
            assert h == {}

    def test_writes_changes_back(self, tmp_path, monkeypatch):
        hash_file = tmp_path / "hashes.json"
        monkeypatch.setattr(scanner, "HASH_FILE", hash_file)
        with scanner.locked_hashes() as h:
            h["some/file.md"] = "abc123"
        data = json.loads(hash_file.read_text())
        assert data["some/file.md"] == "abc123"

    def test_reads_existing_hashes(self, tmp_path, monkeypatch):
        hash_file = tmp_path / "hashes.json"
        hash_file.write_text(json.dumps({"existing.md": "def456"}))
        monkeypatch.setattr(scanner, "HASH_FILE", hash_file)
        with scanner.locked_hashes() as h:
            assert h["existing.md"] == "def456"

    def test_existing_hashes_preserved_after_update(self, tmp_path, monkeypatch):
        hash_file = tmp_path / "hashes.json"
        hash_file.write_text(json.dumps({"old.md": "aaa"}))
        monkeypatch.setattr(scanner, "HASH_FILE", hash_file)
        with scanner.locked_hashes() as h:
            h["new.md"] = "bbb"
        data = json.loads(hash_file.read_text())
        assert data["old.md"] == "aaa"
        assert data["new.md"] == "bbb"
