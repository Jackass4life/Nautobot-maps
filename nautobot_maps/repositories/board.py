"""The alert board's data in PostgreSQL (#424): its reads and writes, moved
here as they were.  The board's rules (levels, likely cause, flapping,
tiers) stay in alerts.py and call these as board.function()."""

import json
import logging
from datetime import timedelta

from nautobot_maps import (
    db,
    inventory,
    settings,
    timeutil,
)

logger = logging.getLogger(__name__)


def nautobot_inventory_primary_ip_backfill_pending(conn=None) -> bool:
    """Treat primary-IP backfill as pending until Nautobot has synced the current cache version."""
    state = inventory.get_sync_state("nautobot_inventory", conn=conn)
    return inventory.cache_version_mismatch(state) or not bool((state or {}).get("last_successful_sync"))


def read_criticality_overrides(conn, device_ids: list | None = None) -> dict:
    """Return ``{device_id: is_critical}`` for *device_ids*, or for every device when ``None``."""
    query = "SELECT nautobot_device_id, is_critical FROM device_criticality_override"
    params: list = []
    if device_ids is not None:
        query += f" WHERE nautobot_device_id IN ({db.placeholders(len(device_ids))})"
        params = list(device_ids)
    overrides = {}
    for row in conn.execute(query, params).fetchall():
        data = db.row_to_dict(row)
        if data.get("nautobot_device_id"):
            overrides[data["nautobot_device_id"]] = bool(data.get("is_critical"))
    return overrides


def load_librenms_id_map() -> dict:
    """Load persisted Nautobot UUID → LibreNMS device mapping."""
    lnms_id_map: dict = {}
    conn = db.get_conn()
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT nautobot_device_id, librenms_device_id, librenms_hostname FROM librenms_device_map"
            ).fetchall()
            lnms_id_map = {
                r["nautobot_device_id"]: {
                    "device_id": r["librenms_device_id"],
                    "hostname": r["librenms_hostname"],
                }
                for r in rows
            }
        except Exception as exc:
            logger.debug("Could not read librenms_device_map: %s", exc)
        finally:
            conn.close()
    return lnms_id_map


def store_librenms_maps(maps: dict) -> None:
    """Upsert ``{nautobot_device_id: (librenms_device_id, hostname)}`` in one transaction (#240)."""
    if not maps:
        return
    conn = db.get_conn()
    if conn is None:
        return
    try:
        with db.transaction(conn):
            p0, p1, p2 = db.placeholders(3).split(",")
            conn.cursor().executemany(
                f"""
                INSERT INTO librenms_device_map (nautobot_device_id, librenms_device_id, librenms_hostname)
                VALUES ({p0}, {p1}, {p2})
                ON CONFLICT(nautobot_device_id) DO UPDATE SET
                    librenms_device_id = excluded.librenms_device_id,
                    librenms_hostname   = excluded.librenms_hostname
                """,
                [(nautobot_id, lnms_id, hostname or "") for nautobot_id, (lnms_id, hostname) in maps.items()],
            )
    except Exception as exc:
        logger.warning("Could not store %d LibreNMS device mappings: %s", len(maps), exc)
    finally:
        conn.close()


def insert_alert_event(
    conn,
    instance_id: int,
    event_type: str,
    event_at: str,
    alert_level: str,
    alert_reason: str,
    snapshot: dict,
) -> None:
    now_sql = db.sql_now()
    placeholders = db.placeholders(6)
    conn.execute(
        f"""
        INSERT INTO alert_events
            (alert_instance_id, event_type, event_at, alert_level, alert_reason, snapshot_json, created_at)
        VALUES ({placeholders}, {now_sql})
        """,
        (
            instance_id,
            event_type,
            event_at,
            alert_level,
            alert_reason,
            json.dumps(snapshot, separators=(",", ":"), sort_keys=True),
        ),
    )


def read_device_states(conn) -> dict:
    """``{device_id: {"state", "since", "changes", "flapping_since", "updated_at"}}`` (#286)."""
    rows = conn.execute(
        "SELECT device_id, state, since, changes, flapping_since, source, updated_at FROM device_states"
    ).fetchall()
    states = {}
    for row in map(db.row_to_dict, rows):
        row["changes"] = [db.serialize_value(change) for change in row.get("changes") or []]
        states[row["device_id"]] = row
    return states


def read_alarmed_device_ids(conn) -> set:
    """Devices with an open alert: never hidden by the alert delay (#286)."""
    rows = conn.execute("SELECT DISTINCT device_id FROM alert_instances WHERE status = 'open'").fetchall()
    return {db.row_to_dict(row)["device_id"] for row in rows}


def flapping_device_ids(conn, now: str, window: timedelta) -> set:
    """Devices flapping now (#286): they count as down.  Flapping ends after a
    whole window without a change, also before a board build clears the row."""
    rows = conn.execute(
        "SELECT device_id FROM device_states WHERE flapping_since IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM unnest(changes) AS change WHERE change > %s::timestamptz - make_interval(secs => %s))",
        (now, window.total_seconds()),
    ).fetchall()
    return {db.row_to_dict(row)["device_id"] for row in rows}


def pending_device_ids(conn, now: str) -> set:
    """Devices down for less than ALERT_DELAY_SECONDS that haven't alarmed (#286)."""
    if not settings.ALERT_DELAY_SECONDS:
        return set()
    rows = conn.execute(
        "SELECT ds.device_id FROM device_states ds WHERE ds.state = 'down' "
        "AND ds.since > %s::timestamptz - make_interval(secs => %s) "
        "AND NOT EXISTS (SELECT 1 FROM alert_instances ai WHERE ai.device_id = ds.device_id AND ai.status = 'open')",
        (now, settings.ALERT_DELAY_SECONDS),
    ).fetchall()
    return {db.row_to_dict(row)["device_id"] for row in rows}


def get_case_numbers_for_instances(conn, instance_ids: list[int]) -> dict[int, list[str]]:
    if not instance_ids:
        return {}
    markers = db.placeholders(len(instance_ids))
    rows = conn.execute(
        f"""
        SELECT alert_instance_id, case_number
        FROM alert_cases
        WHERE alert_instance_id IN ({markers})
        ORDER BY created_at DESC, id DESC
        """,
        tuple(instance_ids),
    ).fetchall()
    case_numbers_by_instance: dict[int, list[str]] = {instance_id: [] for instance_id in instance_ids}
    for row in rows:
        data = db.row_to_dict(row)
        instance_id = int(data.get("alert_instance_id") or 0)
        case_number = (data.get("case_number") or "").strip()
        if instance_id and case_number:
            case_numbers_by_instance.setdefault(instance_id, []).append(case_number)
    return case_numbers_by_instance


def read_alert_board_data(conn) -> dict:
    """Read what an alert-board build needs for every site, in a few queries (#149).

    Replaces per-site reads of cached devices, criticality overrides, the
    primary-IP backfill state and the alert context.  Raises on database
    errors; the caller then falls back to reading per site.
    """
    devices_by_location: dict[str, list] = {}
    for device in inventory.read_devices(conn=conn):
        devices_by_location.setdefault(device.get("location_id") or "", []).append(device)

    # Closed instances are summed in SQL: the table grows with every outage.
    historical_downtime_by_site = {}
    for row in conn.execute(
        """
        SELECT site_id, SUM(total_downtime_seconds) AS total
        FROM alert_instances
        GROUP BY site_id
        """
    ).fetchall():
        data = db.row_to_dict(row)
        historical_downtime_by_site[data.get("site_id") or ""] = int(data.get("total") or 0)

    open_rows_by_site: dict[str, list[dict]] = {}
    for row in conn.execute(
        """
        SELECT id, site_id, device_id, device_name, down_started_at, probable_cause, is_core,
               acknowledged_at, acknowledged_by, ack_note
        FROM alert_instances
        WHERE status = 'open'
        ORDER BY id DESC
        """
    ).fetchall():
        data = db.row_to_dict(row)
        open_rows_by_site.setdefault(data.get("site_id") or "", []).append(data)

    case_numbers_by_instance: dict[int, list[str]] = {}
    for row in conn.execute(
        """
        SELECT ac.alert_instance_id, ac.case_number
        FROM alert_cases ac
        JOIN alert_instances ai ON ai.id = ac.alert_instance_id
        WHERE ai.status = 'open'
        ORDER BY ac.created_at DESC, ac.id DESC
        """
    ).fetchall():
        data = db.row_to_dict(row)
        instance_id = int(data.get("alert_instance_id") or 0)
        case_number = (data.get("case_number") or "").strip()
        if instance_id and case_number:
            case_numbers_by_instance.setdefault(instance_id, []).append(case_number)

    return {
        "devices_by_location": devices_by_location,
        "override_map": read_criticality_overrides(conn),
        "primary_ip_backfill_pending": nautobot_inventory_primary_ip_backfill_pending(conn=conn),
        "historical_downtime_by_site": historical_downtime_by_site,
        "open_rows_by_site": open_rows_by_site,
        "case_numbers_by_instance": case_numbers_by_instance,
    }


RETENTION_BATCH_SIZE = 1000


def prune_alert_history(conn, retention_days: int) -> dict:
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
            with db.transaction(conn):
                count = conn.execute(query, (retention_days, RETENTION_BATCH_SIZE)).rowcount
            deleted[table] += count
            if count < RETENTION_BATCH_SIZE:
                break
    return deleted


FEED_KINDS = ("down", "up", "severity")


def read_alert_feed(
    conn, limit: int, since: str | None = None, kinds=FEED_KINDS, site_ids: list[str] | None = None
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
            clauses.append(f"{time_column} > %s")
            values.append(since)
        if site_ids is not None:
            clauses.append(f"{site_column} = ANY(%s)")
            values.append(site_ids)
        return clauses, values

    branches, params = [], []
    event_types = [event for kind, event in (("down", "opened"), ("up", "resolved")) if kind in kinds]
    if event_types:
        clauses, values = filters("e.event_at", "i.site_id")
        where = " AND ".join(["e.event_type = ANY(%s)", *clauses])
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
    rows = conn.execute(
        # At the same moment a site's severity change sits above the device
        # events that caused it (newest first).
        f"SELECT * FROM ({' UNION ALL '.join(branches)}) feed "
        "ORDER BY at DESC, kind = 'severity' DESC, seq DESC LIMIT %s",
        (*params, limit),
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


def record_board_build(trigger: str, seconds: float) -> None:
    """Count a board build and its duration per trigger, for /metrics (#295)."""
    conn = db.get_conn()
    if conn is None:
        return
    try:
        with db.transaction(conn):
            conn.execute(
                "INSERT INTO board_build_stats (trigger, builds, total_seconds, last_seconds, last_built_at) "
                "VALUES (%s, 1, %s, %s, %s) ON CONFLICT (trigger) DO UPDATE SET "
                "builds = board_build_stats.builds + 1, "
                "total_seconds = board_build_stats.total_seconds + excluded.total_seconds, "
                "last_seconds = excluded.last_seconds, last_built_at = excluded.last_built_at",
                (trigger, seconds, seconds, timeutil.iso_utc_now()),
            )
    except Exception as exc:
        logger.debug("Could not record the board build: %s", exc)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Alert instances: the statements of the lifecycle in alerts.py (#424)
# ---------------------------------------------------------------------------


def latest_open_alert(conn, alert_key: str) -> dict | None:
    """The open alert instance with *alert_key*, or None."""
    marker = db.placeholders(1)
    row = conn.execute(
        f"""
        SELECT id, status, down_started_at, alert_level, alert_reason
        FROM alert_instances
        WHERE alert_key = {marker} AND status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (alert_key,),
    ).fetchone()
    return None if row is None else db.row_to_dict(row)


def open_alert_instance(
    conn,
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
) -> int | None:
    """Open an alert instance; its id, or None when one is already open for *alert_key*."""
    now_sql = db.sql_now()
    p = db.placeholders(12).split(",")
    conflict_sql = "ON CONFLICT (alert_key) WHERE status = 'open' DO NOTHING"
    inserted = conn.execute(
        f"""
        INSERT INTO alert_instances
            (alert_key, site_id, site_name, device_id, device_name, alert_level, alert_reason,
             status, down_started_at, last_seen_down_at, resolved_at, total_downtime_seconds,
             created_at, updated_at, probable_cause, is_core)
        VALUES ({p[0]}, {p[1]}, {p[2]}, {p[3]}, {p[4]}, {p[5]}, {p[6]}, {p[7]}, {p[8]}, {p[9]},
                NULL, 0, {now_sql}, {now_sql}, {p[10]}, {p[11]})
        {conflict_sql}
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
            is_core,
        ),
    ).fetchone()
    inserted_id = db.row_to_dict(inserted).get("id")
    return None if inserted_id is None else int(inserted_id)


def refresh_open_alert(
    conn,
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
    now_sql = db.sql_now()
    p = db.placeholders(9).split(",")
    conn.execute(
        f"""
        UPDATE alert_instances
        SET site_name = {p[0]},
            device_name = {p[1]},
            alert_level = {p[2]},
            alert_reason = {p[3]},
            last_seen_down_at = {p[4]},
            down_started_at = LEAST(down_started_at, {p[5]}::timestamptz),
            probable_cause = {p[6]},
            is_core = {p[7]},
            updated_at = {now_sql}
        WHERE id = {p[8]}
        """,
        (
            site_name,
            device_name,
            level,
            reason,
            last_seen_down_at,
            down_started_at,
            probable_cause,
            is_core,
            instance_id,
        ),
    )


def open_alerts_of_site(conn, site_id: str) -> list[dict]:
    """The site's open alert instances, for resolving the recovered ones."""
    marker = db.placeholders(1)
    rows = conn.execute(
        f"""
        SELECT id, alert_key, site_id, site_name, device_id, device_name, down_started_at
        FROM alert_instances
        WHERE site_id = {marker} AND status = 'open'
        """,
        (site_id,),
    ).fetchall()
    return [db.row_to_dict(row) for row in rows]


def resolve_alert_instance(conn, instance_id: int, resolved_at: str, elapsed_seconds: int) -> bool:
    """Resolve an open alert at *resolved_at*; False when it was no longer
    open or started after that."""
    now_sql = db.sql_now()
    p0, p1, p2, p3 = db.placeholders(4).split(",")
    cur = conn.execute(
        f"""
        UPDATE alert_instances
        SET status = 'resolved',
            resolved_at = {p0},
            total_downtime_seconds = COALESCE(total_downtime_seconds, 0) + {p1},
            updated_at = {now_sql}
        WHERE id = {p2} AND status = 'open' AND down_started_at <= {p3}
        """,
        (resolved_at, elapsed_seconds, instance_id, resolved_at),
    )
    return cur.rowcount != 0


def lock_open_alerts_of_site(conn, site_id: str) -> list[dict]:
    """The site's open alerts, locked until the transaction ends (#288): a
    board build resolving one meanwhile waits, so an acknowledgement never
    lands on an alarm that just ended."""
    rows = conn.execute(
        "SELECT id, device_id, device_name, site_name, alert_level, alert_reason, acknowledged_at "
        "FROM alert_instances WHERE site_id = %s AND status = 'open' ORDER BY id FOR UPDATE",
        (site_id,),
    ).fetchall()
    return [db.row_to_dict(row) for row in rows]


def set_acknowledgement(conn, instance_id: int, at: str, by: str, note: str) -> None:
    conn.execute(
        "UPDATE alert_instances SET acknowledged_at = %s, acknowledged_by = %s, ack_note = %s WHERE id = %s",
        (at, by, note, instance_id),
    )


def clear_acknowledgement(conn, instance_id: int) -> None:
    conn.execute(
        "UPDATE alert_instances SET acknowledged_at = NULL, acknowledged_by = '', ack_note = '' WHERE id = %s",
        (instance_id,),
    )


def open_cases_for_devices(conn, device_ids: list[str]) -> dict:
    """``{"devices": {device_id: {"case_numbers": [...], "acknowledged_by": ""}},
    "cases": [...]}`` for the devices with an open alert (#512): each device's
    cases and all of them, newest first across the site, each once.  One
    query, for the map's site panel.  By device, not site: a location on the
    map can be a building of the board's site."""
    if not device_ids:
        return {"devices": {}, "cases": []}
    rows = conn.execute(
        """
        SELECT ai.device_id, ai.acknowledged_by, ac.case_number
        FROM alert_instances ai
        LEFT JOIN alert_cases ac ON ac.alert_instance_id = ai.id
        WHERE ai.status = 'open' AND ai.device_id = ANY(%s)
        ORDER BY ac.created_at DESC NULLS LAST, ac.id DESC
        """,
        (list(device_ids),),
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


def site_alert_rows(conn, site_id: str) -> list[dict]:
    """Every alert instance of a site, newest first, for its alert context."""
    site_marker = db.placeholders(1)
    rows = conn.execute(
        f"""
        SELECT id, device_id, device_name, status, down_started_at, total_downtime_seconds, probable_cause, is_core,
               acknowledged_at, acknowledged_by, ack_note
        FROM alert_instances
        WHERE site_id = {site_marker}
        ORDER BY id DESC
        """,
        (site_id,),
    ).fetchall()
    return [db.row_to_dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Device states (#286)
# ---------------------------------------------------------------------------


def upsert_device_state(conn, device_id: str, site_id: str, row: dict) -> None:
    conn.execute(
        "INSERT INTO device_states (device_id, site_id, state, since, changes, flapping_since, source) "
        "VALUES (%s, %s, %s, %s, %s::timestamptz[], %s, %s) "
        "ON CONFLICT (device_id) DO UPDATE SET site_id = excluded.site_id, state = excluded.state, "
        "since = excluded.since, changes = excluded.changes, flapping_since = excluded.flapping_since, "
        "source = excluded.source, updated_at = CURRENT_TIMESTAMP",
        (device_id, site_id, row["state"], row["since"], row["changes"], row["flapping_since"], row["source"]),
    )


def delete_device_states(conn, device_ids: list[str]) -> None:
    conn.execute("DELETE FROM device_states WHERE device_id = ANY(%s)", (device_ids,))


def delete_unseen_device_states(conn, now: str, retention: timedelta, seen_device_ids: list[str]) -> None:
    """Drop the rows of devices no build has seen for *retention* (gone from the inventory)."""
    conn.execute(
        "DELETE FROM device_states WHERE updated_at < %s::timestamptz - make_interval(secs => %s) "
        "AND NOT (device_id = ANY(%s))",
        (now, retention.total_seconds(), seen_device_ids),
    )


# ---------------------------------------------------------------------------
# Site levels and their changes (#180)
# ---------------------------------------------------------------------------


def lock_site_levels(conn, site_ids: list[str]) -> dict[str, dict]:
    """The stored levels of *site_ids*, locked in site order, so two builds
    running at once neither deadlock nor log the same change twice."""
    rows = conn.execute(
        "SELECT site_id, alert_level, criticality_key, down_key FROM site_alert_levels "
        "WHERE site_id = ANY(%s) ORDER BY site_id FOR UPDATE",
        (sorted(site_ids),),
    ).fetchall()
    return {row["site_id"]: row for row in map(db.row_to_dict, rows)}


def insert_site_level(conn, site_id: str, site_name: str, level: str, at: str, criticality_key, down_key) -> None:
    conn.execute(
        "INSERT INTO site_alert_levels "
        "(site_id, site_name, alert_level, updated_at, first_seen_at, criticality_key, down_key) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (site_id) DO NOTHING",
        (site_id, site_name, level, at, at, criticality_key, down_key),
    )


def record_site_level_change(conn, site_id: str, site_name: str, from_level: str, to_level: str, at: str) -> None:
    """Store the site's new level and log the change, for the feed."""
    conn.execute(
        "UPDATE site_alert_levels SET site_name = %s, alert_level = %s, updated_at = %s WHERE site_id = %s",
        (site_name, to_level, at, site_id),
    )
    conn.execute(
        "INSERT INTO site_level_changes (site_id, site_name, from_level, to_level, changed_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (site_id, site_name, from_level, to_level, at),
    )


def set_site_level_keys(conn, site_id: str, criticality_key, down_key) -> None:
    conn.execute(
        "UPDATE site_alert_levels SET criticality_key = %s, down_key = %s WHERE site_id = %s",
        (criticality_key, down_key, site_id),
    )
