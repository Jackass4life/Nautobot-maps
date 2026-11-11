"""The last data contract check per endpoint, in ``contract_checks`` (#309, #598):
the current app's SQL (nautobot_maps/contract.py)."""

from sqlalchemy import Connection

from app.db import aconnection
from app.utils import timeutil
from app.utils.locks import advisory_lock_key


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


async def latest(conn: aconnection.Connection) -> list[dict]:
    rows = (
        await conn.execute(
            "SELECT source, endpoint, field, problem, mismatched, records, checked_at "
            "FROM contract_checks ORDER BY source, endpoint, field, problem"
        )
    ).fetchall()
    return [timeutil.row(row) for row in rows]
