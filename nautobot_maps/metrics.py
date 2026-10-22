"""Prometheus metrics (#200).

Read from PostgreSQL when scraped, so every gunicorn worker answers the same
and nothing has to be shared between processes.  Request counts and latency
are in the access log (#193).
"""

from datetime import UTC, datetime

from nautobot_maps import contract, db, timeutil

SYNC_SOURCES = ("nautobot_inventory", "nautobot_inventory_reconcile", "librenms_inventory")


def _label(value) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _number(value: float) -> str:
    """Full precision: ``:g`` would round a Unix timestamp to 6 digits."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


class Exposition:
    """Collects samples and renders the Prometheus text format."""

    def __init__(self):
        self._lines: list[str] = []

    def gauge(self, name: str, help_text: str, samples: list[tuple[dict, float]]) -> None:
        self._lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
        for labels, value in samples:
            rendered = ",".join(f'{key}="{_label(val)}"' for key, val in labels.items())
            number = _number(value)
            self._lines.append(f"{name}{{{rendered}}} {number}" if rendered else f"{name} {number}")

    def counter(self, name: str, help_text: str, samples: list[tuple[dict, float]]) -> None:
        self._lines += [f"# HELP {name} {help_text}", f"# TYPE {name} counter"]
        for labels, value in samples:
            rendered = ",".join(f'{key}="{_label(val)}"' for key, val in labels.items())
            self._lines.append(f"{name}{{{rendered}}} {_number(value)}")

    def render(self) -> str:
        return "\n".join(self._lines) + "\n"


def _timestamp(value) -> float | None:
    parsed = timeutil.as_utc_datetime(value)
    return parsed.timestamp() if parsed else None


def sync_rows(conn) -> dict:
    rows = conn.execute(
        "SELECT source, status, last_started_at, last_completed_at, last_succeeded_at FROM inventory_sync_state"
    ).fetchall()
    return {row["source"]: row for row in map(db.row_to_dict, rows)}


def collect(conn) -> str:
    """All metrics, from the database behind *conn* (``None``: no database)."""
    out = Exposition()
    out.gauge("nautobot_maps_up", "The app answers.", [({}, 1)])
    out.gauge("nautobot_maps_database_up", "PostgreSQL answered this scrape.", [({}, 1 if conn is not None else 0)])
    if conn is None:
        return out.render()

    syncs = sync_rows(conn)
    success, attempt, duration, failing, running = [], [], [], [], []
    for source in SYNC_SOURCES:
        row = syncs.get(source)
        if row is None:
            continue
        labels = {"source": source}
        if (value := _timestamp(row.get("last_succeeded_at"))) is not None:
            success.append((labels, value))
        started, completed = _timestamp(row.get("last_started_at")), _timestamp(row.get("last_completed_at"))
        if started is not None:
            attempt.append((labels, started))
        if started is not None and completed is not None and completed >= started:
            duration.append((labels, completed - started))
        failing.append((labels, 1 if row.get("status") == "error" else 0))
        running.append((labels, 1 if row.get("status") == "running" else 0))
    out.gauge(
        "nautobot_maps_sync_last_success_timestamp_seconds",
        "When the sync last finished without error (Unix time).",
        success,
    )
    out.gauge("nautobot_maps_sync_last_attempt_timestamp_seconds", "When the sync last started (Unix time).", attempt)
    out.gauge("nautobot_maps_sync_last_duration_seconds", "How long the last finished sync took.", duration)
    out.gauge("nautobot_maps_sync_failing", "1 if the last sync failed.", failing)
    out.gauge("nautobot_maps_sync_running", "1 while a sync runs.", running)

    open_alerts = conn.execute(
        "SELECT alert_level, count(*) AS n FROM alert_instances WHERE status = 'open' GROUP BY alert_level"
    ).fetchall()
    out.gauge(
        "nautobot_maps_open_alerts",
        "Open device alerts by level.",
        [({"level": row["alert_level"]}, row["n"]) for row in map(db.row_to_dict, open_alerts)],
    )
    sites = conn.execute("SELECT alert_level, count(*) AS n FROM site_alert_levels GROUP BY alert_level").fetchall()
    out.gauge(
        "nautobot_maps_sites",
        "Sites by alert level at the last board build.",
        [({"level": row["alert_level"]}, row["n"]) for row in map(db.row_to_dict, sites)],
    )
    # Notifications (#282): a growing pending count or failures mean a channel is broken.
    outbox = conn.execute(
        "SELECT channel, status, count(*) AS n FROM notification_outbox "
        "WHERE status IN ('pending', 'failed') GROUP BY channel, status"
    ).fetchall()
    outbox = [db.row_to_dict(row) for row in outbox]
    out.gauge(
        "nautobot_maps_notifications_pending",
        "Notifications waiting to be sent, by channel.",
        [({"channel": row["channel"]}, row["n"]) for row in outbox if row["status"] == "pending"],
    )
    out.gauge(
        "nautobot_maps_notifications_failed",
        "Notifications given up after all retries (kept 30 days), by channel.",
        [({"channel": row["channel"]}, row["n"]) for row in outbox if row["status"] == "failed"],
    )
    # Board builds (#295): requests should hardly ever build; the scheduler does.
    builds = [
        db.row_to_dict(row)
        for row in conn.execute(
            "SELECT trigger, builds, total_seconds, last_seconds FROM board_build_stats ORDER BY trigger"
        ).fetchall()
    ]
    out.counter(
        "nautobot_maps_board_builds_total",
        "Alert board builds by trigger (scheduler, request, push, change).",
        [({"trigger": row["trigger"]}, row["builds"]) for row in builds],
    )
    out.counter(
        "nautobot_maps_board_build_seconds_total",
        "Time spent building the alert board, by trigger.",
        [({"trigger": row["trigger"]}, row["total_seconds"]) for row in builds],
    )
    out.gauge(
        "nautobot_maps_board_build_last_duration_seconds",
        "How long the last alert board build took, by trigger.",
        [({"trigger": row["trigger"]}, row["last_seconds"]) for row in builds],
    )
    # Growth (#298): every table's size on disk (with indexes) and the
    # planner's row estimate (no count(*) on a large history at each scrape).
    tables = [
        db.row_to_dict(row)
        for row in conn.execute(
            "SELECT c.relname AS name, pg_total_relation_size(c.oid) AS bytes, GREATEST(c.reltuples, 0) AS rows "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relkind = 'r' ORDER BY c.relname"
        ).fetchall()
    ]
    out.gauge(
        "nautobot_maps_table_bytes",
        "Size of each database table on disk, with its indexes.",
        [({"table": row["name"]}, row["bytes"]) for row in tables],
    )
    out.gauge(
        "nautobot_maps_table_rows_estimate",
        "Rows per table, as PostgreSQL estimates them (updated by autovacuum/ANALYZE).",
        [({"table": row["name"]}, row["rows"]) for row in tables],
    )
    oldest = db.row_to_dict(conn.execute("SELECT min(changed_at) AS at FROM site_level_changes").fetchone())
    out.gauge(
        "nautobot_maps_history_oldest_timestamp_seconds",
        "The oldest site level change kept (Unix time): how far back history goes.",
        [({}, value)] if (value := _timestamp(oldest.get("at"))) is not None else [],
    )
    mismatches: dict[tuple, int] = {}
    for check in contract.latest_checks(conn):
        for mismatch in check["mismatches"]:
            key = (check["source"], check["endpoint"], mismatch["field"])
            mismatches[key] = mismatches.get(key, 0) + mismatch["count"]
    out.gauge(
        "nautobot_maps_contract_mismatches",
        "Records of the last sync that did not match the data contract (docs/data-contract.md), "
        "by upstream endpoint and field.",
        [({"source": s, "endpoint": e, "field": f}, n) for (s, e, f), n in sorted(mismatches.items())],
    )
    return out.render()


def inventory_sync_age_seconds(conn) -> int | None:
    """Seconds since the Nautobot inventory sync last succeeded, or None if it never has."""
    row = sync_rows(conn).get("nautobot_inventory") or {}
    succeeded = _timestamp(row.get("last_succeeded_at"))
    if succeeded is None:
        return None
    return max(0, int(datetime.now(UTC).timestamp() - succeeded))


def coordinate_problems(counts: dict[str, int]) -> str:
    """The board's sites with missing or wrong coordinates, by problem (#368)."""
    out = Exposition()
    out.gauge(
        "nautobot_maps_sites_coordinate_problems",
        "Alert board sites with missing or wrong coordinates, by problem (invalid, zero, swapped, missing; far is a hint).",
        [({"problem": problem}, number) for problem, number in counts.items()],
    )
    return out.render()


# The top bar's live status (#419): what each source's last sync says, in plain
# words.  Data older than this many sync intervals (and at least the floor) is stale.
STALE_INTERVALS = 3
STALE_FLOOR_SECONDS = 15 * 60
APP_STATUS_SOURCES = (
    ("nautobot_inventory", "Nautobot", "INVENTORY_SYNC_INTERVAL_SECONDS"),
    ("librenms_inventory", "LibreNMS", "LIBRENMS_SYNC_INTERVAL_SECONDS"),
)
_STATE_RANK = {"ok": 0, "stale": 1, "down": 2}


def _iso(timestamp: float | None) -> str | None:
    return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z") if timestamp is not None else None


def app_status(rows: dict | None, *, librenms: bool, intervals: dict, now: float) -> dict:
    """How fresh the app's data is: ``state`` (ok, stale or down), ``updated_at``
    (the oldest source's last good sync: the data is only as fresh as that) and
    one entry per source.  *rows*: ``sync_rows``; None when the database
    didn't answer.  Calls nothing upstream."""
    if rows is None:
        return {
            "state": "down",
            "updated_at": None,
            "sources": [{"name": "Database", "state": "down", "message": "Can't reach the database"}],
        }
    sources, successes = [], []
    for key, name, interval_setting in APP_STATUS_SOURCES:
        if key == "librenms_inventory" and not librenms:
            continue
        row = rows.get(key) or {}
        succeeded = _timestamp(row.get("last_succeeded_at"))
        successes.append(succeeded)
        stale_after = max(STALE_INTERVALS * int(intervals.get(interval_setting) or 0), STALE_FLOOR_SECONDS)
        if row.get("status") == "error":
            state, message = "down", f"Can't reach {name}"
        elif succeeded is None:
            state, message = "stale", f"No data from {name} yet"
        elif now - succeeded > stale_after:
            state, message = "stale", f"{name} data is {int((now - succeeded) // 60)} min old"
        else:
            state, message = "ok", f"{name} is up to date"
        sources.append(
            {
                "name": name,
                "state": state,
                "message": message,
                "last_success": _iso(succeeded),
            }
        )
    return {
        "state": max((source["state"] for source in sources), key=_STATE_RANK.get, default="ok"),
        "updated_at": _iso(None if None in successes else min(successes, default=None)),
        "sources": sources,
    }
