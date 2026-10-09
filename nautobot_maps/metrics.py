"""Prometheus metrics (#200).

Read from PostgreSQL when scraped, so every gunicorn worker answers the same
and nothing has to be shared between processes.  Request counts and latency
are in the access log (#193).
"""

from datetime import UTC, datetime

from nautobot_maps import db, timeutil

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

    def render(self) -> str:
        return "\n".join(self._lines) + "\n"


def _timestamp(value) -> float | None:
    parsed = timeutil.parse_iso_datetime(value) if isinstance(value, str) else value
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


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
    return out.render()


def inventory_sync_age_seconds(conn) -> int | None:
    """Seconds since the Nautobot inventory sync last succeeded, or None if it never has."""
    row = sync_rows(conn).get("nautobot_inventory") or {}
    succeeded = _timestamp(row.get("last_succeeded_at"))
    if succeeded is None:
        return None
    return max(0, int(datetime.now(UTC).timestamp() - succeeded))
