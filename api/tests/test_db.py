"""app/db.py (#600): connections as the current app's db.get_conn() made them."""

from app import db
from app.utils.legacy_settings import settings


def test_autocommit_dict_rows_and_the_statement_timeout():
    conn = db.get_conn()
    try:
        assert conn.autocommit is True
        row = conn.execute("SELECT 1 AS one").fetchone()
        assert row == {"one": 1}
        seconds = conn.execute(
            "SELECT EXTRACT(epoch FROM current_setting('statement_timeout')::interval) AS s"
        ).fetchone()["s"]
        assert int(seconds) == settings.DB_STATEMENT_TIMEOUT_SECONDS
    finally:
        conn.close()


def test_a_transaction_commits_or_rolls_back():
    conn = db.get_conn()
    try:
        conn.execute("CREATE TEMP TABLE t (n int)")
        with db.transaction(conn):
            conn.execute("INSERT INTO t VALUES (1)")
        try:
            with db.transaction(conn):
                conn.execute("INSERT INTO t VALUES (2)")
                raise RuntimeError("undo")
        except RuntimeError:
            pass
        assert [r["n"] for r in conn.execute("SELECT n FROM t").fetchall()] == [1]
    finally:
        conn.execute("DROP TABLE t")
        conn.close()


def test_closing_returns_it_to_the_pool():
    before = db.engine.pool.checkedout()
    conn = db.get_conn()
    assert db.engine.pool.checkedout() == before + 1
    conn.close()
    conn.close()  # twice: nothing
    assert db.engine.pool.checkedout() == before


def test_the_lock_is_exclusive_and_released():
    release = db.try_advisory_lock("db-test")
    assert callable(release)
    assert db.try_advisory_lock("db-test") is False
    release()
    again = db.try_advisory_lock("db-test")
    assert callable(again)
    again()


def test_rows_as_the_current_app_answers_them():
    from datetime import UTC, datetime

    assert db.row_to_dict({"at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), "n": 1}) == {
        "at": "2026-01-02T03:04:05Z",
        "n": 1,
    }
    assert db.row_to_dict(None) == {}
    assert db.placeholders(3) == "%s,%s,%s"
    assert len(db.build_alert_key("site", "dev")) == 64


def test_a_connection_with_its_own_timeout_is_outside_the_pool():
    """``get_conn(connect_timeout=2)`` (the app status, #608 review): connects on its own,
    so an exhausted pool can't hold it; closing it closes it."""
    before = db.engine.pool.checkedout()
    conn = db.get_conn(connect_timeout=2)
    try:
        assert db.engine.pool.checkedout() == before
        assert conn.execute("SELECT 1 AS one").fetchone() == {"one": 1}
        assert conn.autocommit is True
    finally:
        conn.close()
    assert conn.closed
