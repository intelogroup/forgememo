"""
Tests for the DBContext connection-pool fix:
  1. rollback() on exception — no poisoned connections after a write failure
  2. _write_lock serializes concurrent writes — no "database is locked" cascade
"""

import concurrent.futures
import sqlite3
import threading

import pytest

import forgememo.api as api_module
from forgememo.api import create_app, init_db, init_pool, get_db


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point every test at a fresh in-memory-backed temp DB with its own pool."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(api_module, "DB_PATH", db_file)
    monkeypatch.setattr(api_module, "pool", None)
    monkeypatch.setattr(api_module, "_write_lock", threading.Lock())
    init_pool()
    init_db()
    yield db_file
    api_module.pool.close_all()
    monkeypatch.setattr(api_module, "pool", None)


@pytest.fixture()
def client(isolated_db):
    app = create_app()
    with app.test_client() as c:
        yield c


# ─── Unit: rollback on exception prevents connection poisoning ────────────────

def test_rollback_on_exception_does_not_poison_pool():
    """After a failed write, the returned connection must have no open txn."""
    # Force an error inside a write context
    try:
        with get_db(write=True) as conn:
            conn.execute(
                "INSERT INTO traces (session_id, project_tag, type, content) "
                "VALUES (?, ?, ?, ?)",
                ("s1", "proj", "success", "x"),
            )
            raise RuntimeError("simulated failure mid-write")
    except RuntimeError:
        pass

    # The next write must succeed — connection must not carry a broken txn
    with get_db(write=True) as conn:
        conn.execute(
            "INSERT INTO traces (session_id, project_tag, type, content) "
            "VALUES (?, ?, ?, ?)",
            ("s2", "proj", "success", "recovery write"),
        )
        conn.commit()

    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
    # Only the recovery write should be present (failed txn was rolled back)
    assert count == 1


def test_failed_write_does_not_leave_transaction_open():
    """Poison test: verify the connection has in_transaction=False after rollback."""
    try:
        with get_db(write=True) as conn:
            conn.execute(
                "INSERT INTO traces (session_id, project_tag, type, content) "
                "VALUES (?, ?, ?, ?)",
                ("s", "p", "success", "will be rolled back"),
            )
            raise sqlite3.OperationalError("database is locked")
    except sqlite3.OperationalError:
        pass

    # Borrow a connection — it must not be mid-transaction
    with get_db() as conn:
        assert not conn.in_transaction, "connection still has an open transaction after failure"


# ─── Unit: write lock serializes concurrent writers ──────────────────────────

def test_write_lock_serializes_concurrent_writes():
    """N concurrent threads must all succeed without any lock errors."""
    N = 20
    errors = []

    def write_one(i):
        try:
            with get_db(write=True) as conn:
                conn.execute(
                    "INSERT INTO traces (session_id, project_tag, type, content) "
                    "VALUES (?, ?, ?, ?)",
                    (f"s{i}", "load-test", "note", f"concurrent write {i}"),
                )
                conn.commit()
        except Exception as e:
            errors.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=N) as ex:
        list(ex.map(write_one, range(N)))

    assert errors == [], f"Concurrent writes produced errors: {errors}"

    with get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM traces WHERE project_tag='load-test'"
        ).fetchone()[0]
    assert count == N


def test_read_does_not_acquire_write_lock():
    """Reads must not block while a write lock is held."""
    results = {}
    lock_acquired = threading.Event()
    read_done = threading.Event()

    def hold_write_lock():
        with get_db(write=True) as conn:
            lock_acquired.set()
            read_done.wait(timeout=3)
            conn.execute(
                "INSERT INTO traces (session_id, project_tag, type, content) "
                "VALUES (?, ?, ?, ?)",
                ("s", "p", "note", "writer"),
            )
            conn.commit()

    def do_read():
        lock_acquired.wait(timeout=3)
        with get_db() as conn:
            results["count"] = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
        read_done.set()

    t_write = threading.Thread(target=hold_write_lock)
    t_read = threading.Thread(target=do_read)
    t_write.start()
    t_read.start()
    t_write.join(timeout=5)
    t_read.join(timeout=5)

    assert "count" in results, "read thread did not complete (likely blocked by write lock)"


# ─── Integration: POST /traces concurrently ──────────────────────────────────

def test_concurrent_post_traces_all_succeed(isolated_db):
    """/traces must accept N concurrent POSTs without any 500 errors."""
    app = create_app()
    N = 10
    payload = {
        "type": "success",
        "content": "concurrent integration write",
        "project": "ci-test",
    }

    responses = []
    lock = threading.Lock()

    def post_one():
        # Each thread gets its own client to avoid Flask contextvars collision.
        with app.test_client() as c:
            resp = c.post("/traces", json=payload)
        with lock:
            responses.append(resp.status_code)

    threads = [threading.Thread(target=post_one) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    failures = [s for s in responses if s != 201]
    assert failures == [], f"{len(failures)}/{N} POSTs failed with status: {set(failures)}"
