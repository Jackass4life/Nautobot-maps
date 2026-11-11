"""The alert board's data in PostgreSQL (#424): its reads and writes, moved
here as they were.  The board's rules (levels, likely cause, flapping,
tiers) stay in alerts.py and call these as board.function().

Ported from nautobot_maps/repositories/board.py (#602): only the imports changed.
Async on ``aconnection`` since #661: times are ISO text, cast by the database.
"""

import json
from datetime import timedelta

from loguru import logger

from app.core.legacy_settings import settings
from app.db import aconnection
from app.db import connection as db
from app.services import inventory_service as inventory
from app.utils import timeutil


async def nautobot_inventory_primary_ip_backfill_pending(
    conn: aconnection.Connection | None = None,
) -> bool:
    """Treat primary-IP backfill as pending until Nautobot has synced the current cache version."""
    state = await inventory.sync_state("nautobot_inventory", conn)
    return inventory.cache_version_mismatch(state) or not bool(
        (state or {}).get("last_successful_sync")
    )


async def read_criticality_overrides(
    conn: aconnection.Connection, device_ids: list | None = None
) -> dict:
    """Return ``{device_id: is_critical}`` for *device_ids*, or for every device when ``None``."""
    query = "SELECT nautobot_device_id, is_critical FROM device_criticality_override"
    params: list = []
    if device_ids is not None:
        query += f" WHERE nautobot_device_id IN ({db.placeholders(len(device_ids))})"
        params = list(device_ids)
    overrides = {}
    for row in (await conn.execute(query, params)).fetchall():
        data = db.row_to_dict(row)
        if data.get("nautobot_device_id"):
            overrides[data["nautobot_device_id"]] = bool(data.get("is_critical"))
    return overrides


async def load_librenms_id_map() -> dict:
    """Load persisted Nautobot UUID → LibreNMS device mapping."""
    try:
        async with aconnection.connect() as conn:
            rows = (
                await conn.execute(
                    "SELECT nautobot_device_id, librenms_device_id, librenms_hostname FROM librenms_device_map"
                )
            ).fetchall()
    except Exception as exc:
        logger.debug("Could not read librenms_device_map: {}", exc)
        return {}
    return {
        r["nautobot_device_id"]: {
            "device_id": r["librenms_device_id"],
            "hostname": r["librenms_hostname"],
        }
        for r in rows
    }


async def store_librenms_maps(maps: dict) -> None:
    """Upsert ``{nautobot_device_id: (librenms_device_id, hostname)}`` in one transaction (#240)."""
    if not maps:
        return
    try:
        async with aconnection.connect() as conn:
            await conn.executemany(
                """
                INSERT INTO librenms_device_map (nautobot_device_id, librenms_device_id, librenms_hostname)
                VALUES (%s, %s, %s)
                ON CONFLICT(nautobot_device_id) DO UPDATE SET
                    librenms_device_id = excluded.librenms_device_id,
                    librenms_hostname   = excluded.librenms_hostname
                """,
                [
                    (nautobot_id, int(lnms_id), hostname or "")
                    for nautobot_id, (lnms_id, hostname) in maps.items()
                ],
            )
    except Exception as exc:
        logger.warning("Could not store {} LibreNMS device mappings: {}", len(maps), exc)


async def insert_alert_event(
    conn: aconnection.Connection,
    instance_id: int,
    event_type: str,
    event_at: str,
    alert_level: str,
    alert_reason: str,
    snapshot: dict,
) -> None:
    await conn.execute(
        """
        INSERT INTO alert_events
            (alert_instance_id, event_type, event_at, alert_level, alert_reason, snapshot_json, created_at)
        VALUES (%s, %s, %s::text::timestamptz, %s, %s, %s, CURRENT_TIMESTAMP)
        """,
        (
            int(instance_id),
            event_type,
            event_at,
            alert_level,
            alert_reason,
            json.dumps(snapshot, separators=(",", ":"), sort_keys=True),
        ),
    )


async def read_device_states(conn: aconnection.Connection) -> dict:
    """``{device_id: {"state", "since", "changes", "flapping_since", "updated_at"}}`` (#286)."""
    rows = (
        await conn.execute(
            "SELECT device_id, state, since, changes, flapping_since, source, updated_at FROM device_states"
        )
    ).fetchall()
    states = {}
    for row in map(db.row_to_dict, rows):
        row["changes"] = [db.serialize_value(change) for change in row.get("changes") or []]
        states[row["device_id"]] = row
    return states


async def read_alarmed_device_ids(conn: aconnection.Connection) -> set:
    """Devices with an open alert: never hidden by the alert delay (#286)."""
    rows = (
        await conn.execute("SELECT DISTINCT device_id FROM alert_instances WHERE status = 'open'")
    ).fetchall()
    return {db.row_to_dict(row)["device_id"] for row in rows}


async def flapping_device_ids(conn: aconnection.Connection, now: str, window: timedelta) -> set:
    """Devices flapping now (#286): they count as down.  Flapping ends after a
    whole window without a change, also before a board build clears the row."""
    rows = (
        await conn.execute(
            "SELECT device_id FROM device_states WHERE flapping_since IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM unnest(changes) AS change WHERE change > %s::text::timestamptz - make_interval(secs => %s))",
            (now, float(window.total_seconds())),
        )
    ).fetchall()
    return {db.row_to_dict(row)["device_id"] for row in rows}


async def pending_device_ids(conn: aconnection.Connection, now: str) -> set:
    """Devices down for less than ALERT_DELAY_SECONDS that haven't alarmed (#286)."""
    if not settings.ALERT_DELAY_SECONDS:
        return set()
    rows = (
        await conn.execute(
            "SELECT ds.device_id FROM device_states ds WHERE ds.state = 'down' "
            "AND ds.since > %s::text::timestamptz - make_interval(secs => %s) "
            "AND NOT EXISTS (SELECT 1 FROM alert_instances ai WHERE ai.device_id = ds.device_id AND ai.status = 'open')",
            (now, float(settings.ALERT_DELAY_SECONDS)),
        )
    ).fetchall()
    return {db.row_to_dict(row)["device_id"] for row in rows}


async def get_case_numbers_for_instances(
    conn: aconnection.Connection, instance_ids: list[int]
) -> dict[int, list[str]]:
    if not instance_ids:
        return {}
    markers = db.placeholders(len(instance_ids))
    rows = (
        await conn.execute(
            f"""
        SELECT alert_instance_id, case_number
        FROM alert_cases
        WHERE alert_instance_id IN ({markers})
        ORDER BY created_at DESC, id DESC
        """,
            tuple(int(instance_id) for instance_id in instance_ids),
        )
    ).fetchall()
    case_numbers_by_instance: dict[int, list[str]] = {
        instance_id: [] for instance_id in instance_ids
    }
    for row in rows:
        data = db.row_to_dict(row)
        instance_id = int(data.get("alert_instance_id") or 0)
        case_number = (data.get("case_number") or "").strip()
        if instance_id and case_number:
            case_numbers_by_instance.setdefault(instance_id, []).append(case_number)
    return case_numbers_by_instance


async def read_alert_board_data(conn: aconnection.Connection) -> dict:
    """Read what an alert-board build needs for every site, in a few queries (#149).

    Replaces per-site reads of cached devices, criticality overrides, the
    primary-IP backfill state and the alert context.  Raises on database
    errors; the caller then falls back to reading per site.
    """
    devices_by_location: dict[str, list] = {}
    for device in await inventory.read_devices(conn=conn):
        devices_by_location.setdefault(device.get("location_id") or "", []).append(device)

    # Closed instances are summed in SQL: the table grows with every outage.
    historical_downtime_by_site = {}
    for row in (
        await conn.execute(
            """
        SELECT site_id, SUM(total_downtime_seconds) AS total
        FROM alert_instances
        GROUP BY site_id
        """
        )
    ).fetchall():
        data = db.row_to_dict(row)
        historical_downtime_by_site[data.get("site_id") or ""] = int(data.get("total") or 0)

    open_rows_by_site: dict[str, list[dict]] = {}
    for row in (
        await conn.execute(
            """
        SELECT id, site_id, device_id, device_name, down_started_at, probable_cause, is_core,
               acknowledged_at, acknowledged_by, ack_note
        FROM alert_instances
        WHERE status = 'open'
        ORDER BY id DESC
        """
        )
    ).fetchall():
        data = db.row_to_dict(row)
        open_rows_by_site.setdefault(data.get("site_id") or "", []).append(data)

    case_numbers_by_instance: dict[int, list[str]] = {}
    for row in (
        await conn.execute(
            """
        SELECT ac.alert_instance_id, ac.case_number
        FROM alert_cases ac
        JOIN alert_instances ai ON ai.id = ac.alert_instance_id
        WHERE ai.status = 'open'
        ORDER BY ac.created_at DESC, ac.id DESC
        """
        )
    ).fetchall():
        data = db.row_to_dict(row)
        instance_id = int(data.get("alert_instance_id") or 0)
        case_number = (data.get("case_number") or "").strip()
        if instance_id and case_number:
            case_numbers_by_instance.setdefault(instance_id, []).append(case_number)

    return {
        "devices_by_location": devices_by_location,
        "override_map": await read_criticality_overrides(conn),
        "primary_ip_backfill_pending": await nautobot_inventory_primary_ip_backfill_pending(
            conn=conn
        ),
        "historical_downtime_by_site": historical_downtime_by_site,
        "open_rows_by_site": open_rows_by_site,
        "case_numbers_by_instance": case_numbers_by_instance,
    }


RETENTION_BATCH_SIZE = 1000


async def prune_alert_history(conn: aconnection.Connection, retention_days: int) -> dict:
    """Delete alert history older than *retention_days* (#194).

    Resolved alert instances whose ``resolved_at`` is older go, with their
    events and cases (``ON DELETE CASCADE``), and so do site severity
    changes.  Open alerts are never deleted.  Deletes in batches, each its
    own transaction, so the tables aren't locked for long.  Returns the
    number of rows deleted per table.
    """
    deleted = {"alert_instances": 0, "site_level_changes": 0}
    if retention_days <= 0:
        return deleted
    batches = {
        "alert_instances": "DELETE FROM alert_instances WHERE id IN ("
        "SELECT id FROM alert_instances WHERE status = 'resolved' "
        "AND resolved_at < now() - make_interval(days => %s) LIMIT %s)",
        "site_level_changes": "DELETE FROM site_level_changes WHERE id IN ("
        "SELECT id FROM site_level_changes WHERE changed_at < now() - make_interval(days => %s) LIMIT %s)",
    }
    for table, query in batches.items():
        while True:
            async with conn.transaction():
                count = (await conn.execute(query, (retention_days, RETENTION_BATCH_SIZE))).rowcount
            deleted[table] += count
            if count < RETENTION_BATCH_SIZE:
                break
    return deleted


FEED_KINDS = ("down", "up", "severity")


async def read_alert_feed(
    conn: aconnection.Connection,
    limit: int,
    since: str | None = None,
    kinds=FEED_KINDS,
    site_ids: list[str] | None = None,
) -> list[dict]:
    """What changed on the board, newest first (#180).

    ``down`` (an alert opened) and ``up`` (resolved) come from the alert
    events; ``severity`` from the logged site level changes.  *since* keeps
    only entries after that time; *site_ids* (a page for some sites only,
    #369) only those sites' entries, before the limit.
    """

    if site_ids is not None and not site_ids:
        return []  # No site matches the page's scope (#369).

    def filters(time_column: str, site_column: str) -> tuple[list[str], list]:
        """The conditions for *since* and *site_ids* on one branch, and their params."""
        clauses, values = [], []
        if since:
            clauses.append(f"{time_column} > %s::text::timestamptz")
            values.append(since)
        if site_ids is not None:
            clauses.append(f"{site_column} = ANY(%s)")
            values.append(site_ids)
        return clauses, values

    branches, params = [], []
    event_types = [
        event for kind, event in (("down", "opened"), ("up", "resolved")) if kind in kinds
    ]
    if event_types:
        clauses, values = filters("e.event_at", "i.site_id")
        # Access points come and go all day at 10,000 (#521): in their site's
        # History, not here.
        where = " AND ".join(["e.event_type = ANY(%s)", "NOT i.access_point", *clauses])
        branches.append(
            f"""
            SELECT CASE e.event_type WHEN 'opened' THEN 'down' ELSE 'up' END AS kind,
                   e.event_at AS at, e.id AS seq, i.site_id, i.site_name, i.device_id, i.device_name,
                   e.alert_level AS level, i.down_started_at AS down_since,
                   '' AS from_level, '' AS to_level
            FROM alert_events e
            JOIN alert_instances i ON i.id = e.alert_instance_id
            WHERE {where}
            """
        )
        params += [event_types, *values]
    if "severity" in kinds:
        clauses, values = filters("c.changed_at", "c.site_id")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        branches.append(
            f"""
            SELECT 'severity' AS kind, c.changed_at AS at, c.id AS seq, c.site_id, c.site_name,
                   '' AS device_id, '' AS device_name, c.to_level AS level,
                   NULL::timestamptz AS down_since, c.from_level, c.to_level
            FROM site_level_changes c
            {where}
            """
        )
        params += values
    if not branches:
        return []
    rows = (
        await conn.execute(
            # At the same moment a site's severity change sits above the device
            # events that caused it (newest first).
            f"SELECT * FROM ({' UNION ALL '.join(branches)}) feed "
            "ORDER BY at DESC, kind = 'severity' DESC, seq DESC LIMIT %s",
            (*params, limit),
        )
    ).fetchall()
    events = []
    for row in rows:
        event = db.row_to_dict(row)
        event.pop("seq", None)
        if event["kind"] != "down":
            event.pop("down_since", None)
        if event["kind"] != "severity":
            event.pop("from_level", None)
            event.pop("to_level", None)
        events.append(event)
    return events


async def record_board_build(trigger: str, seconds: float) -> None:
    """Count a board build and its duration per trigger, for /metrics (#295)."""
    try:
        async with aconnection.connect() as conn:
            await conn.execute(
                "INSERT INTO board_build_stats (trigger, builds, total_seconds, last_seconds, last_built_at) "
                "VALUES (%s, 1, %s, %s, %s::text::timestamptz) ON CONFLICT (trigger) DO UPDATE SET "
                "builds = board_build_stats.builds + 1, "
                "total_seconds = board_build_stats.total_seconds + excluded.total_seconds, "
                "last_seconds = excluded.last_seconds, last_built_at = excluded.last_built_at",
                (trigger, float(seconds), float(seconds), timeutil.iso_utc_now()),
            )
    except Exception as exc:
        logger.debug("Could not record the board build: {}", exc)


# ---------------------------------------------------------------------------
# Alert instances: the statements of the lifecycle in alerts.py (#424)
# ---------------------------------------------------------------------------


async def latest_open_alert(conn: aconnection.Connection, alert_key: str) -> dict | None:
    """The open alert instance with *alert_key*, or None."""
    marker = db.placeholders(1)
    row = (
        await conn.execute(
            f"""
        SELECT id, status, down_started_at, alert_level, alert_reason
        FROM alert_instances
        WHERE alert_key = {marker} AND status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """,
            (alert_key,),
        )
    ).fetchone()
    return None if row is None else db.row_to_dict(row)


async def open_alert_instance(
    conn: aconnection.Connection,
    alert_key: str,
    site_id: str,
    site_name: str,
    device_id: str,
    device_name: str,
    level: str,
    reason: str,
    down_started_at: str,
    last_seen_down_at: str,
    probable_cause: str,
    is_core: bool,
    access_point: bool = False,
) -> int | None:
    """Open an alert instance; its id, or None when one is already open for *alert_key*.
    *access_point*: one a wireless controller watches (#521)."""
    inserted = (
        await conn.execute(
            """
        INSERT INTO alert_instances
            (alert_key, site_id, site_name, device_id, device_name, alert_level, alert_reason,
             status, down_started_at, last_seen_down_at, resolved_at, total_downtime_seconds,
             created_at, updated_at, probable_cause, is_core, access_point)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::text::timestamptz, %s::text::timestamptz,
                NULL, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, %s, %s, %s)
        ON CONFLICT (alert_key) WHERE status = 'open' DO NOTHING
        RETURNING id
        """,
            (
                alert_key,
                site_id,
                site_name,
                device_id,
                device_name,
                level,
                reason,
                "open",
                down_started_at,
                last_seen_down_at,
                probable_cause,
                bool(is_core),
                bool(access_point),
            ),
        )
    ).fetchone()
    inserted_id = db.row_to_dict(inserted).get("id")
    return None if inserted_id is None else int(inserted_id)


async def refresh_open_alert(
    conn: aconnection.Connection,
    instance_id: int,
    site_name: str,
    device_name: str,
    level: str,
    reason: str,
    last_seen_down_at: str,
    down_started_at: str,
    probable_cause: str,
    is_core: bool,
) -> None:
    """Bring an open alert up to date.  down_started_at only ever moves
    earlier: this also corrects alerts opened before #166 on their next build."""
    await conn.execute(
        """
        UPDATE alert_instances
        SET site_name = %s,
            device_name = %s,
            alert_level = %s,
            alert_reason = %s,
            last_seen_down_at = %s::text::timestamptz,
            down_started_at = LEAST(down_started_at, %s::text::timestamptz),
            probable_cause = %s,
            is_core = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (
            site_name,
            device_name,
            level,
            reason,
            last_seen_down_at,
            down_started_at,
            probable_cause,
            bool(is_core),
            int(instance_id),
        ),
    )


async def open_alerts_of_site(conn: aconnection.Connection, site_id: str) -> list[dict]:
    """The site's open alert instances, for resolving the recovered ones."""
    marker = db.placeholders(1)
    rows = (
        await conn.execute(
            f"""
        SELECT id, alert_key, site_id, site_name, device_id, device_name, down_started_at
        FROM alert_instances
        WHERE site_id = {marker} AND status = 'open'
        """,
            (site_id,),
        )
    ).fetchall()
    return [db.row_to_dict(row) for row in rows]


async def resolve_alert_instance(
    conn: aconnection.Connection, instance_id: int, resolved_at: str, elapsed_seconds: int
) -> bool:
    """Resolve an open alert at *resolved_at*; False when it was no longer
    open or started after that."""
    cur = await conn.execute(
        """
        UPDATE alert_instances
        SET status = 'resolved',
            resolved_at = %s::text::timestamptz,
            total_downtime_seconds = COALESCE(total_downtime_seconds, 0) + %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s AND status = 'open' AND down_started_at <= %s::text::timestamptz
        """,
        (resolved_at, int(elapsed_seconds), int(instance_id), resolved_at),
    )
    return cur.rowcount != 0


async def lock_open_alerts_of_site(conn: aconnection.Connection, site_id: str) -> list[dict]:
    """The site's open alerts, locked until the transaction ends (#288): a
    board build resolving one meanwhile waits, so an acknowledgement never
    lands on an alarm that just ended."""
    rows = (
        await conn.execute(
            "SELECT id, device_id, device_name, site_name, alert_level, alert_reason, acknowledged_at "
            "FROM alert_instances WHERE site_id = %s AND status = 'open' ORDER BY id FOR UPDATE",
            (site_id,),
        )
    ).fetchall()
    return [db.row_to_dict(row) for row in rows]


async def set_acknowledgement(
    conn: aconnection.Connection, instance_id: int, at: str, by: str, note: str
) -> None:
    await conn.execute(
        "UPDATE alert_instances SET acknowledged_at = %s::text::timestamptz, acknowledged_by = %s, "
        "ack_note = %s WHERE id = %s",
        (at, by, note, int(instance_id)),
    )


async def clear_acknowledgement(conn: aconnection.Connection, instance_id: int) -> None:
    await conn.execute(
        "UPDATE alert_instances SET acknowledged_at = NULL, acknowledged_by = '', ack_note = '' WHERE id = %s",
        (int(instance_id),),
    )


async def open_cases_for_devices(conn: aconnection.Connection, device_ids: list[str]) -> dict:
    """``{"devices": {device_id: {"case_numbers": [...], "acknowledged_by": ""}},
    "cases": [...]}`` for the devices with an open alert (#512): each device's
    cases and all of them, newest first across the site, each once.  One
    query, for the map's site panel.  By device, not site: a location on the
    map can be a building of the board's site."""
    if not device_ids:
        return {"devices": {}, "cases": []}
    rows = (
        await conn.execute(
            """
        SELECT ai.device_id, ai.acknowledged_by, ac.case_number
        FROM alert_instances ai
        LEFT JOIN alert_cases ac ON ac.alert_instance_id = ai.id
        WHERE ai.status = 'open' AND ai.device_id = ANY(%s)
        ORDER BY ac.created_at DESC NULLS LAST, ac.id DESC
        """,
            (list(device_ids),),
        )
    ).fetchall()
    found: dict[str, dict] = {}
    cases: dict[str, None] = {}  # in the rows' order: newest first, across devices
    for row in map(db.row_to_dict, rows):
        entry = found.setdefault(row["device_id"], {"case_numbers": [], "acknowledged_by": ""})
        case = (row.get("case_number") or "").strip()
        if case and case not in entry["case_numbers"]:
            entry["case_numbers"].append(case)
        if case:
            cases.setdefault(case)
        entry["acknowledged_by"] = entry["acknowledged_by"] or (row.get("acknowledged_by") or "")
    return {"devices": found, "cases": list(cases)}


async def site_alert_rows(conn: aconnection.Connection, site_id: str) -> list[dict]:
    """Every alert instance of a site, newest first, for its alert context."""
    site_marker = db.placeholders(1)
    rows = (
        await conn.execute(
            f"""
        SELECT id, device_id, device_name, status, down_started_at, total_downtime_seconds, probable_cause, is_core,
               acknowledged_at, acknowledged_by, ack_note
        FROM alert_instances
        WHERE site_id = {site_marker}
        ORDER BY id DESC
        """,
            (site_id,),
        )
    ).fetchall()
    return [db.row_to_dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Device states (#286)
# ---------------------------------------------------------------------------


async def upsert_device_state(
    conn: aconnection.Connection, device_id: str, site_id: str, row: dict
) -> None:
    await conn.execute(
        "INSERT INTO device_states (device_id, site_id, state, since, changes, flapping_since, source) "
        "VALUES (%s, %s, %s, %s::text::timestamptz, %s::text[]::timestamptz[], %s::text::timestamptz, %s) "
        "ON CONFLICT (device_id) DO UPDATE SET site_id = excluded.site_id, state = excluded.state, "
        "since = excluded.since, changes = excluded.changes, flapping_since = excluded.flapping_since, "
        "source = excluded.source, updated_at = CURRENT_TIMESTAMP",
        (
            device_id,
            site_id,
            row["state"],
            row["since"],
            row["changes"],
            row["flapping_since"],
            row["source"],
        ),
    )


async def delete_device_states(conn: aconnection.Connection, device_ids: list[str]) -> None:
    await conn.execute("DELETE FROM device_states WHERE device_id = ANY(%s)", (device_ids,))


async def delete_unseen_device_states(
    conn: aconnection.Connection, now: str, retention: timedelta, seen_device_ids: list[str]
) -> None:
    """Drop the rows of devices no build has seen for *retention* (gone from the inventory)."""
    await conn.execute(
        "DELETE FROM device_states WHERE updated_at < %s::text::timestamptz - make_interval(secs => %s) "
        "AND NOT (device_id = ANY(%s))",
        (now, float(retention.total_seconds()), list(seen_device_ids)),
    )


# ---------------------------------------------------------------------------
# Site levels and their changes (#180)
# ---------------------------------------------------------------------------


async def lock_site_levels(conn: aconnection.Connection, site_ids: list[str]) -> dict[str, dict]:
    """The stored levels of *site_ids*, locked in site order, so two builds
    running at once neither deadlock nor log the same change twice."""
    rows = (
        await conn.execute(
            "SELECT site_id, alert_level, criticality_key, down_key FROM site_alert_levels "
            "WHERE site_id = ANY(%s) ORDER BY site_id FOR UPDATE",
            (sorted(site_ids),),
        )
    ).fetchall()
    return {row["site_id"]: row for row in map(db.row_to_dict, rows)}


async def insert_site_level(
    conn: aconnection.Connection,
    site_id: str,
    site_name: str,
    level: str,
    at: str,
    criticality_key,
    down_key,
) -> None:
    await conn.execute(
        "INSERT INTO site_alert_levels "
        "(site_id, site_name, alert_level, updated_at, first_seen_at, criticality_key, down_key) "
        "VALUES (%s, %s, %s, %s::text::timestamptz, %s::text::timestamptz, %s, %s) "
        "ON CONFLICT (site_id) DO NOTHING",
        (site_id, site_name, level, at, at, criticality_key, down_key),
    )


async def record_site_level_change(
    conn: aconnection.Connection,
    site_id: str,
    site_name: str,
    from_level: str,
    to_level: str,
    at: str,
) -> None:
    """Store the site's new level and log the change, for the feed."""
    await conn.execute(
        "UPDATE site_alert_levels SET site_name = %s, alert_level = %s, updated_at = %s::text::timestamptz "
        "WHERE site_id = %s",
        (site_name, to_level, at, site_id),
    )
    await conn.execute(
        "INSERT INTO site_level_changes (site_id, site_name, from_level, to_level, changed_at) "
        "VALUES (%s, %s, %s, %s, %s::text::timestamptz)",
        (site_id, site_name, from_level, to_level, at),
    )


async def set_site_level_keys(
    conn: aconnection.Connection, site_id: str, criticality_key, down_key
) -> None:
    await conn.execute(
        "UPDATE site_alert_levels SET criticality_key = %s, down_key = %s WHERE site_id = %s",
        (criticality_key, down_key, site_id),
    )
