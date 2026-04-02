"""
Tests for cross-platform case sensitivity in file paths and project IDs.

macOS default FS (APFS) is case-insensitive; Linux ext4 is case-sensitive.
These tests verify that:
  1. Project IDs derived from paths are normalized consistently
  2. Search works regardless of path casing
  3. Config keys are case-insensitive where expected
  4. Error fingerprints strip paths before hashing (platform-agnostic)
"""

import json
import os
import sys
import threading

import pytest

import forgememo.config as config_module
import forgememo.daemon as daemon_module
import forgememo.storage as storage_module
from forgememo.daemon import create_app
from forgememo.hook import _error_fingerprint, _extract_error_keywords
from forgememo.config import load, save
from forgememo.storage import get_conn, init_db


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_file = tmp_path / "case_test.db"
    monkeypatch.setattr(storage_module, "DB_PATH", db_file)
    monkeypatch.setattr(daemon_module, "_write_lock", threading.Lock())
    init_db()
    yield db_file


@pytest.fixture()
def client(isolated_db):
    app = create_app()
    with app.test_client() as c:
        yield c


# ─── Error fingerprint path normalization ───────────────────────────────────


class TestFingerprintCaseNormalization:
    def test_posix_paths_stripped_from_fingerprint(self):
        """POSIX paths like /Users/alice/project/foo.py should be stripped."""
        err1 = "TypeError: bad value at /Users/alice/project/foo.py:42"
        err2 = "TypeError: bad value at /Users/bob/project/foo.py:99"
        assert _error_fingerprint(err1) == _error_fingerprint(err2)

    def test_windows_paths_stripped_from_fingerprint(self):
        """Windows paths like C:\\Users\\alice\\foo.py should be stripped."""
        err1 = "TypeError: bad value at C:\\Users\\alice\\project\\foo.py:42"
        err2 = "TypeError: bad value at C:\\Users\\bob\\project\\foo.py:99"
        assert _error_fingerprint(err1) == _error_fingerprint(err2)

    def test_relative_paths_stripped_from_fingerprint(self):
        """Relative paths like ./src/foo.py should be stripped."""
        err1 = "Error in ./src/foo.py: undefined variable"
        err2 = "Error in ../other/src/foo.py: undefined variable"
        assert _error_fingerprint(err1) == _error_fingerprint(err2)

    def test_mixed_path_separators_same_fingerprint(self):
        """Errors with / vs \\ paths for same logical error should match."""
        err_unix = "TypeError: bad at /project/src/main.py"
        err_win = "TypeError: bad at C:\\project\\src\\main.py"
        fp1 = _error_fingerprint(err_unix)
        fp2 = _error_fingerprint(err_win)
        assert fp1 == fp2

    def test_case_insensitive_error_type(self):
        """Error fingerprints normalize to lowercase."""
        err1 = "TypeError: cannot read property 'x' of undefined"
        err2 = "TYPEERROR: cannot read property 'x' of undefined"
        assert _error_fingerprint(err1) == _error_fingerprint(err2)


# ─── Keyword extraction strips paths ────────────────────────────────────────


class TestKeywordCaseNormalization:
    def test_posix_paths_stripped_from_keywords(self):
        kw = _extract_error_keywords(
            "Error in /Users/alice/deep/path/foo.py: bad input"
        )
        assert "/Users" not in kw
        assert "alice" not in kw
        assert "Error" in kw or "bad" in kw

    def test_windows_paths_stripped_from_keywords(self):
        kw = _extract_error_keywords(
            "Error in C:\\Users\\bob\\project\\main.py: bad input"
        )
        assert "Users" not in kw
        assert "bob" not in kw

    def test_keywords_are_meaningful(self):
        kw = _extract_error_keywords("TypeError: Cannot read property 'name' of null")
        words = kw.split()
        assert any(
            w in ("TypeError", "Cannot", "read", "property", "null") for w in words
        )


# ─── Project ID casing in search ───────────────────────────────────────────


class TestProjectIdCasing:
    def _insert_summary(self, project_id, title, narrative):
        from forgememo.daemon import _canonicalize_project_id

        normalized_id = (
            _canonicalize_project_id(project_id) if project_id else project_id
        )
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO distilled_summaries "
            "(session_id, project_id, source_tool, type, title, narrative, "
            "facts, files_read, files_modified, concepts, impact_score) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "sess-case",
                normalized_id,
                "claude",
                "note",
                title,
                narrative,
                json.dumps([]),
                json.dumps([]),
                json.dumps([]),
                json.dumps(["case-test"]),
                5,
            ),
        )
        row_id = cur.lastrowid
        conn.execute(
            "INSERT INTO distilled_summaries_fts(rowid, title, narrative, concepts, tags, project_id) "
            "VALUES (?,?,?,?,?,?)",
            (row_id, title, narrative, "case-test", "", normalized_id),
        )
        conn.commit()
        conn.close()
        return row_id

    def test_exact_project_id_match(self, client):
        """Search with exact project_id should find results."""
        self._insert_summary("/tmp/MyProject", "Case Test Alpha", "Alpha narrative")
        r = client.get("/search?q=Alpha&project_id=/tmp/MyProject&k=5")
        results = r.get_json().get("results", [])
        assert len(results) >= 1

    def test_different_case_project_id_matches_on_case_insensitive_fs(self, client):
        """
        Environment-aware test: On case-insensitive FS (Mac/Windows), ProjectA == projecta.
        On case-sensitive FS (Linux), they are different paths.

        This test reflects the hardware reality, not a bug.
        """
        self._insert_summary("/tmp/MyProject", "Case Test Beta", "Beta narrative")

        if sys.platform == "darwin" or sys.platform == "win32":
            r = client.get("/search?q=Beta&project_id=/tmp/myproject&k=5")
            results = r.get_json().get("results", [])
            assert len(results) >= 1, (
                "On case-insensitive FS, /tmp/MyProject and /tmp/myproject should match"
            )
        else:
            r = client.get("/search?q=Beta&project_id=/tmp/myproject&k=5")
            results = r.get_json().get("results", [])
            assert len(results) == 0, (
                "On case-sensitive FS, /tmp/MyProject and /tmp/myproject should NOT match"
            )


# ─── Error events with mixed-case session IDs ───────────────────────────────


class TestErrorEventsCaseSensitivity:
    def test_session_id_is_case_sensitive(self, client):
        """Session IDs should be treated as case-sensitive identifiers."""
        client.post(
            "/error_events",
            json={"session_id": "Sess-ABC", "fingerprint": "fp1"},
        )
        r = client.get("/error_events?session_id=sess-abc&fingerprint=fp1")
        assert r.get_json()["count"] == 0

        r = client.get("/error_events?session_id=Sess-ABC&fingerprint=fp1")
        assert r.get_json()["count"] == 1

    def test_fingerprint_is_case_sensitive(self, client):
        """Fingerprints (hex hashes) are lowercase by construction."""
        client.post(
            "/error_events",
            json={"session_id": "sess-1", "fingerprint": "abc123def456"},
        )
        r = client.get("/error_events?session_id=sess-1&fingerprint=ABC123DEF456")
        assert r.get_json()["count"] == 0

        r = client.get("/error_events?session_id=sess-1&fingerprint=abc123def456")
        assert r.get_json()["count"] == 1


# ─── Config key handling ───────────────────────────────────────────────────


class TestConfigKeyCasing:
    def test_provider_names_are_lowercase(self):
        """All supported provider names should be lowercase."""
        from forgememo.config import SUPPORTED_PROVIDERS

        for p in SUPPORTED_PROVIDERS:
            assert p == p.lower(), f"Provider {p!r} is not lowercase"

    def test_config_keys_are_case_sensitive(self, tmp_path, monkeypatch):
        """Config dict keys are standard Python dict (case-sensitive)."""
        config_file = tmp_path / "case_config.json"
        monkeypatch.setattr(config_module, "CONFIG_PATH", config_file)

        save({"Provider": "openai", "provider": "anthropic"})
        data = load()
        assert data["Provider"] == "openai"
        assert data["provider"] == "anthropic"
