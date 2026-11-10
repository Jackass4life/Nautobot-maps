"""The last data contract check per endpoint, in ``contract_checks`` (#309, #598):
the current app's SQL (nautobot_maps/contract.py)."""

from sqlalchemy import Connection

from app.utils import timeutil
from app.utils.locks import advisory_lock_key


def _run(conn, sql: str, params: tuple = ()):  # type: ignore[no-untyped-def]
    """The statement on a SQLAlchemy connection, or on app/db/connection.py's psycopg one
    (the ported modules, e.g. metrics, read the checks on theirs, #602): rows as dicts
    either way."""
    if hasattr(conn, "exec_driver_sql"):
        return [dict(row) for row in conn.exec_driver_sql(sql, params).mappings()]
    cursor = conn.execute(sql, params)
    return list(cursor.fetchall()) if cursor.description else []


def replace(conn: Connection, source: str, path: str, result: dict) -> int:
    """Store *result* as the endpoint's check; returns how many mismatches it had before.
    One writer per endpoint at a time (e.g. LibreNMS pushes at once)."""
    conn.exec_driver_sql(
        "SELECT pg_advisory_xact_lock(%s)", (advisory_lock_key(f"contract:{source}:{path}"),)
    )
    previous = conn.exec_driver_sql(
        "SELECT count(*) AS n FROM contract_checks "
        "WHERE source = %s AND endpoint = %s AND field <> ''",
        (source, path),
    ).scalar()
    conn.exec_driver_sql(
        "DELETE FROM contract_checks WHERE source = %s AND endpoint = %s", (source, path)
    )
    # One row per endpoint even when all is well: when it was last checked.
    conn.exec_driver_sql(
        "INSERT INTO contract_checks (source, endpoint, field, problem, mismatched, records) "
        "VALUES (%s, %s, '', '', 0, %s)",
        (source, path, result["records"]),
    )
    for mismatch in result["mismatches"]:
        conn.exec_driver_sql(
            "INSERT INTO contract_checks (source, endpoint, field, problem, mismatched, records) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                source,
                path,
                mismatch["field"],
                mismatch["problem"],
                mismatch["count"],
                result["records"],
            ),
        )
    return int(previous or 0)


def latest(conn: Connection) -> list[dict]:
    rows = _run(
        conn,
        "SELECT source, endpoint, field, problem, mismatched, records, checked_at "
        "FROM contract_checks ORDER BY source, endpoint, field, problem",
    )
    return [timeutil.row(row) for row in rows]
