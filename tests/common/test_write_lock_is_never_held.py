"""One failed statement must not lock the product.

Python's sqlite3 opens an implicit transaction on the first write and holds it
until commit(). On a thread-local connection in a service that runs for days,
an exception between the write and the commit leaves that transaction — and
SQLite's single write lock — open forever. Every other writer in every other
process then fails with "database is locked".

Measured on 2026-08-25: every write failed while core-logic, web-ui and
research all reported healthy, and restarting the service that was failing did
not clear it, because the holder was a different process. Nothing in any log
said so.

104 writes across the services are not inside a `with conn:` or a try/finally,
so this is not an exotic path — it is the default outcome of any raise on a
write path, in a codebase that swallows exceptions widely.
"""
import os
import sqlite3
import tempfile

import pytest

from services.common.storage import connect_safely, _ThreadLocalDB


@pytest.fixture()
def db(tmp_path):
    p = str(tmp_path / "helga.db")
    c = connect_safely(p)
    c.execute("CREATE TABLE t (k TEXT PRIMARY KEY, v TEXT)")
    c.commit()
    c.close()
    return p


def _write_lock_free(path):
    """Can an independent connection take the write lock right now?"""
    probe = sqlite3.connect(path, timeout=1)
    try:
        probe.execute("BEGIN IMMEDIATE")
        probe.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        probe.close()


def test_a_failed_write_does_not_keep_the_lock(db):
    conn = connect_safely(db)
    conn.execute("INSERT INTO t VALUES ('a', '1')")   # opens the transaction
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO t VALUES ('a', '2')")  # duplicate key
    assert _write_lock_free(db), (
        "the failed statement left its transaction open — this is the "
        "'database is locked' outage")
    conn.close()


def test_a_plain_connection_shows_the_bug_this_prevents(db):
    """The behaviour being defended against, so the test above cannot quietly
    stop testing anything."""
    conn = sqlite3.connect(db, timeout=1)
    conn.execute("INSERT INTO t VALUES ('b', '1')")
    try:
        conn.execute("INSERT INTO t VALUES ('b', '2')")
    except sqlite3.IntegrityError:
        pass
    assert not _write_lock_free(db), (
        "a bare connection no longer holds the lock after a failed write — "
        "if sqlite3 changed, connect_safely may no longer be needed")
    conn.close()


def test_a_write_then_commit_through_two_get_calls_is_not_discarded(db):
    """The regression the age check exists to prevent.

    Real code fetches the handle again between its write and its commit:

        conn = store._get_db(); conn.execute("INSERT ...")
        store._get_db().commit()

    A self-heal that rolls back on PRESENCE of a transaction destroys that
    insert before the commit runs. Measured on a live build: every
    hydration_provenance row was discarded this way, silently, so locally
    built concepts had no recorded author.
    """
    pool = _ThreadLocalDB(db)
    pool.get().execute("INSERT INTO t VALUES ('keep', '1')")
    pool.get().commit()

    check = sqlite3.connect(db, timeout=2)
    row = check.execute("SELECT v FROM t WHERE k='keep'").fetchone()
    check.close()
    assert row is not None, "the write was rolled back by the self-heal"


def test_a_leaked_transaction_is_rolled_back_on_reuse(db, caplog, monkeypatch):
    """A connection handed back mid-transaction is a leak, not a state: the
    previous caller returned or raised past its commit and is never coming
    back. Rolling back happens on the thread that owns the connection, which
    is the only thread allowed to do it."""
    import services.common.storage as storage_mod
    monkeypatch.setattr(storage_mod, "IDLE_TXN_LIMIT_S", 0.0)  # age it instantly
    pool = _ThreadLocalDB(db)
    conn = pool.get()
    conn.execute("INSERT INTO t VALUES ('c', '1')")     # no commit: leaked
    assert conn.in_transaction

    again = pool.get()                                   # same thread, new work
    assert again is conn
    assert not again.in_transaction, "the leaked transaction survived reuse"
    assert _write_lock_free(db)
    assert any("SELF-HEAL" in r.message for r in caplog.records), \
        "the self-heal must be loud; a silent one hides the leak that caused it"


def test_the_busy_timeout_is_not_the_five_second_default(db):
    conn = connect_safely(db)
    got = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert got >= 30000, (
        f"busy_timeout is {got}ms; the default 5s is shorter than a single "
        f"hydration write under load, so contention surfaces as a hard failure "
        f"rather than a wait")
    conn.close()
