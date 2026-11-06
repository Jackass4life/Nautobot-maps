"""PostgreSQL connections as the current app's code uses them (nautobot_maps/db.py, #600).

``db.get_conn()`` is a psycopg connection in autocommit with dict rows, drawn from
a SQLAlchemy engine's pool; ``with db.transaction(conn)`` is a transaction. The
SQL ported from the current app runs on it unchanged. Connecting gives up after
DB_CONNECT_TIMEOUT_SECONDS and every statement after DB_STATEMENT_TIMEOUT_SECONDS
(#190). Call these as ``db.function()`` so tests can replace them.
"""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from psycopg.rows import dict_row
from sqlalchemy import create_engine, event

from app.utils.config import get_settings

_settings = get_settings()

# create_engine does not connect; importing this module never touches the database.
engine = create_engine(
    _settings.sqlalchemy_url(),
    pool_pre_ping=True,
    connect_args={
        "connect_timeout": _settings.db_connect_timeout_seconds,
        "options": f"-c statement_timeout={_settings.db_statement_timeout_seconds * 1000}",
    },
)


@event.listens_for(engine, "connect")
def _as_the_current_app(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
    dbapi_connection.autocommit = True
    dbapi_connection.row_factory = dict_row


class Connection:
    """A pooled psycopg connection; ``close()`` returns it to the pool."""

    def __init__(self, pooled) -> None:  # type: ignore[no-untyped-def]
        self._pooled = pooled
        self._conn = pooled.driver_connection

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._conn, name)

    def execute(self, query, params=None):  # type: ignore[no-untyped-def]
        return self._conn.execute(query, params)

    def close(self) -> None:
        if self._pooled is not None:
            self._pooled.close()
            self._pooled = None

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def dialect() -> str:
    """Always PostgreSQL here: the new stack needs its database."""
    return "postgres"


def get_conn(connect_timeout: int | None = None) -> Connection:
    """A connection from the pool (*connect_timeout* is the pool's, from the settings)."""
    return Connection(engine.raw_connection())


@contextmanager
def transaction(conn: Connection) -> Iterator[Connection]:
    with conn.transaction():
        yield conn


def serialize_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value


def row_to_dict(row: object) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return {k: serialize_value(v) for k, v in row.items()}
    try:
        data = dict(row)  # type: ignore[call-overload]
        return {k: serialize_value(v) for k, v in data.items()}
    except (TypeError, ValueError):
        return {}


def placeholders(count: int) -> str:
    return ",".join("%s" for _ in range(count))


def sql_now() -> str:
    return "CURRENT_TIMESTAMP"


def build_alert_key(site_id: str, device_id: str) -> str:
    """A device's alert on a site; the severity is deliberately not part of it (#163)."""
    raw = f"{site_id.strip()}::{device_id.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def advisory_lock_key(name: str) -> int:
    return int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big", signed=True)


def try_advisory_lock(name: str):  # type: ignore[no-untyped-def]
    """A release function when this process got the lock *name*, False when another
    holds it. The same keys as the current app: the two exclude each other."""
    conn = get_conn()
    try:
        key = advisory_lock_key(name)
        row = conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (key,)).fetchone()
        if not row_to_dict(row).get("acquired"):
            conn.close()
            return False

        def _release() -> None:
            try:
                conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
            finally:
                conn.close()

        return _release
    except Exception:
        conn.close()
        raise
