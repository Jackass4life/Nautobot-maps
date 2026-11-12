"""The last data contract check per endpoint, in ``contract_checks`` (#309, #598):
the current app's SQL (nautobot_maps/contract.py)."""

from app.db import aconnection
from app.utils import timeutil
from app.utils.locks import advisory_lock_key


async def replace(conn: aconnection.Connection, source: str, path: str, result: dict) -> int:
    """Store *result* as the endpoint's check (in the caller's transaction); returns how
    many mismatches it had before. One writer per endpoint at a time (e.g. LibreNMS
    pushes at once)."""
    await conn.execute(
        "SELECT pg_advisory_xact_lock(%s)", (advisory_lock_key(f"contract:{source}:{path}"),)
    )
    previous = (
        await conn.execute(
            "SELECT count(*) AS n FROM contract_checks "
            "WHERE source = %s AND endpoint = %s AND field <> ''",
            (source, path),
        )
    ).fetchone()
    await conn.execute(
        "DELETE FROM contract_checks WHERE source = %s AND endpoint = %s", (source, path)
    )
    # One row per endpoint even when all is well: when it was last checked.
    await conn.execute(
        "INSERT INTO contract_checks (source, endpoint, field, problem, mismatched, records) "
        "VALUES (%s, %s, '', '', 0, %s)",
        (source, path, int(result["records"])),
    )
    await conn.executemany(
        "INSERT INTO contract_checks (source, endpoint, field, problem, mismatched, records) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        [
            (
                source,
                path,
                mismatch["field"],
                mismatch["problem"],
                int(mismatch["count"]),
                int(result["records"]),
            )
            for mismatch in result["mismatches"]
        ],
    )
    return int((previous or {}).get("n") or 0)


async def latest(conn: aconnection.Connection) -> list[dict]:
    rows = (
        await conn.execute(
            "SELECT source, endpoint, field, problem, mismatched, records, checked_at "
            "FROM contract_checks ORDER BY source, endpoint, field, problem"
        )
    ).fetchall()
    return [timeutil.row(row) for row in rows]
