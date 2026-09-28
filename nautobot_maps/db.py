"""PostgreSQL persistence: connections, schema and migrations (#165).

Other modules call these as ``db.function()`` so tests can replace them with
``monkeypatch.setattr(db, "get_conn", ...)``.
"""

import hashlib
import logging
from contextlib import contextmanager
from datetime import UTC, datetime

from nautobot_maps import settings

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - optional dependency
    psycopg = None
    dict_row = None

logger = logging.getLogger(__name__)


def dialect() -> str:
    """Return ``"postgres"`` when a PostgreSQL URL is configured, else ``""``."""
    db_url = (settings.NAUTOBOT_MAPS_DATABASE_URL or "").strip()
    if db_url.lower().startswith(("postgres://", "postgresql://")):
        return "postgres"
    return ""


@contextmanager
def transaction(conn):
    with conn.transaction():
        yield conn


def serialize_value(value):
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value


def row_to_dict(row) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return {k: serialize_value(v) for k, v in row.items()}
    try:
        data = dict(row)
        return {k: serialize_value(v) for k, v in data.items()}
    except Exception:
        return {}


def placeholders(count: int) -> str:
    return ",".join("%s" for _ in range(count))


def sql_now() -> str:
    return "CURRENT_TIMESTAMP"


# Defined before init_db(): it runs at import and its migration uses this (#169).
def build_alert_key(site_id: str, device_id: str) -> str:
    """Identify a device's alert on a site.

    The severity is deliberately not part of it: a Medium → Critical change
    updates the open alert instead of resolving it and opening a new one,
    which restarted its downtime (#163).
    """
    raw = f"{site_id.strip()}::{device_id.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def advisory_lock_key(name: str) -> int:
    return int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big", signed=True)


def get_conn():
    """Return a PostgreSQL connection, or ``None`` when persistence is disabled."""
    if not dialect():
        return None
    if psycopg is None:
        logger.error(
            "NAUTOBOT_MAPS_DATABASE_URL is set but psycopg is unavailable; "
            "install psycopg to enable PostgreSQL persistence"
        )
        return None
    return psycopg.connect(
        settings.NAUTOBOT_MAPS_DATABASE_URL,
        row_factory=dict_row,
        autocommit=True,
    )


def try_advisory_lock(name: str):
    conn = get_conn()
    if conn is None:
        return None
    try:
        key = advisory_lock_key(name)
        row = conn.execute(
            "SELECT pg_try_advisory_lock(%s) AS acquired",
            (key,),
        ).fetchone()
        acquired = row_to_dict(row).get("acquired")
        if not acquired:
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


def init_db() -> None:
    """Create persistence tables if they don't exist."""
    conn = get_conn()
    if conn is None:
        return
    try:
        with transaction(conn):
            conn.execute("SELECT pg_advisory_xact_lock(674864467105151045)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS device_criticality_override (
                    nautobot_device_id TEXT PRIMARY KEY,
                    is_critical        INTEGER NOT NULL DEFAULT 1,
                    reason             TEXT    NOT NULL DEFAULT '',
                    updated_by         TEXT    NOT NULL DEFAULT '',
                    updated_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS librenms_device_map (
                    nautobot_device_id  TEXT PRIMARY KEY,
                    librenms_device_id  INTEGER NOT NULL,
                    librenms_hostname   TEXT    NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inventory_sync_state (
                    source               TEXT PRIMARY KEY,
                    last_started_at      TIMESTAMPTZ,
                    last_completed_at    TIMESTAMPTZ,
                    last_successful_sync TIMESTAMPTZ,
                    cache_version        TEXT NOT NULL DEFAULT '',
                    status               TEXT NOT NULL DEFAULT 'idle',
                    error_message        TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nautobot_location_cache (
                    location_id       TEXT PRIMARY KEY,
                    name              TEXT NOT NULL DEFAULT '',
                    slug              TEXT NOT NULL DEFAULT '',
                    status            TEXT NOT NULL DEFAULT '',
                    location_type     TEXT NOT NULL DEFAULT '',
                    parent            TEXT NOT NULL DEFAULT '',
                    parent_id         TEXT NOT NULL DEFAULT '',
                    latitude          DOUBLE PRECISION,
                    longitude         DOUBLE PRECISION,
                    description       TEXT NOT NULL DEFAULT '',
                    physical_address  TEXT NOT NULL DEFAULT '',
                    facility          TEXT NOT NULL DEFAULT '',
                    tenant            TEXT NOT NULL DEFAULT '',
                    tenant_id         TEXT NOT NULL DEFAULT '',
                    tenant_group      TEXT NOT NULL DEFAULT '',
                    asn               BIGINT,
                    time_zone         TEXT,
                    tags_json         TEXT NOT NULL DEFAULT '[]',
                    url               TEXT NOT NULL DEFAULT '',
                    last_updated      TIMESTAMPTZ,
                    synced_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nautobot_device_cache (
                    device_id      TEXT PRIMARY KEY,
                    location_id    TEXT NOT NULL DEFAULT '',
                    name           TEXT NOT NULL DEFAULT '',
                    device_type    TEXT NOT NULL DEFAULT '',
                    manufacturer   TEXT NOT NULL DEFAULT '',
                    role           TEXT NOT NULL DEFAULT '',
                    status         TEXT NOT NULL DEFAULT '',
                    primary_ip     TEXT NOT NULL DEFAULT '',
                    platform       TEXT NOT NULL DEFAULT '',
                    serial         TEXT NOT NULL DEFAULT '',
                    tenant         TEXT NOT NULL DEFAULT '',
                    last_updated   TIMESTAMPTZ,
                    synced_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS librenms_device_status (
                    device_id      INTEGER PRIMARY KEY,
                    hostname       TEXT NOT NULL DEFAULT '',
                    ip             TEXT NOT NULL DEFAULT '',
                    status         INTEGER,
                    status_raw     TEXT NOT NULL DEFAULT '',
                    status_reason  TEXT NOT NULL DEFAULT '',
                    synced_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_instances (
                    id                     BIGSERIAL PRIMARY KEY,
                    alert_key              TEXT NOT NULL,
                    site_id                TEXT NOT NULL,
                    site_name              TEXT NOT NULL DEFAULT '',
                    device_id              TEXT NOT NULL,
                    device_name            TEXT NOT NULL DEFAULT '',
                    alert_level            TEXT NOT NULL DEFAULT 'unknown',
                    alert_reason           TEXT NOT NULL DEFAULT '',
                    status                 TEXT NOT NULL DEFAULT 'open',
                    down_started_at        TIMESTAMPTZ NOT NULL,
                    last_seen_down_at      TIMESTAMPTZ NOT NULL,
                    resolved_at            TIMESTAMPTZ,
                    total_downtime_seconds BIGINT NOT NULL DEFAULT 0,
                    created_at             TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at             TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_events (
                    id                BIGSERIAL PRIMARY KEY,
                    alert_instance_id BIGINT NOT NULL REFERENCES alert_instances(id) ON DELETE CASCADE,
                    event_type        TEXT NOT NULL,
                    event_at          TIMESTAMPTZ NOT NULL,
                    alert_level       TEXT NOT NULL DEFAULT 'unknown',
                    alert_reason      TEXT NOT NULL DEFAULT '',
                    snapshot_json     TEXT NOT NULL DEFAULT '{}',
                    created_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_cases (
                    id                BIGSERIAL PRIMARY KEY,
                    alert_instance_id BIGINT NOT NULL REFERENCES alert_instances(id) ON DELETE CASCADE,
                    case_number       TEXT NOT NULL,
                    created_by        TEXT NOT NULL DEFAULT '',
                    created_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(alert_instance_id, case_number)
                )
                """
            )
            primary_ip_column_missing = row_to_dict(
                conn.execute(
                    """
                    SELECT NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'nautobot_device_cache'
                          AND column_name = 'primary_ip'
                    ) AS missing
                    """
                ).fetchone()
            ).get("missing")
            conn.execute(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'nautobot_location_cache'
                          AND column_name = 'time_zone'
                          AND is_nullable = 'NO'
                    ) THEN
                        ALTER TABLE nautobot_location_cache ALTER COLUMN time_zone DROP NOT NULL;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'nautobot_device_cache'
                          AND column_name = 'primary_ip'
                    ) THEN
                        ALTER TABLE nautobot_device_cache ADD COLUMN primary_ip TEXT NOT NULL DEFAULT '';
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'inventory_sync_state'
                          AND column_name = 'cache_version'
                    ) THEN
                        ALTER TABLE inventory_sync_state ADD COLUMN cache_version TEXT NOT NULL DEFAULT '';
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'librenms_device_status'
                          AND column_name = 'ip'
                    ) THEN
                        ALTER TABLE librenms_device_status ADD COLUMN ip TEXT NOT NULL DEFAULT '';
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'nautobot_location_cache'
                          AND column_name = 'parent_id'
                    ) THEN
                        -- Filled by the full resync the cache-version bump triggers (#158).
                        ALTER TABLE nautobot_location_cache ADD COLUMN parent_id TEXT NOT NULL DEFAULT '';
                    END IF;
                END;
                $$;
                """
            )
            if primary_ip_column_missing:
                mark_nautobot_inventory_sync_pending(conn)
            migrate_open_alert_keys(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_instances_key ON alert_instances(alert_key)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_nautobot_device_cache_location ON nautobot_device_cache(location_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alert_instances_site_status ON alert_instances(site_id, status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alert_events_instance_time ON alert_events(alert_instance_id, event_at)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_instances_open_key ON alert_instances(alert_key) WHERE status = 'open'"
            )
    finally:
        conn.close()
    logger.info("Nautobot Maps persistence initialised (postgres)")


def migrate_open_alert_keys(conn) -> None:
    """Re-key open alerts to site + device (#163); a no-op once done.

    Keys used to include the severity.  Should a device have several open
    alerts, the one that started first is kept and the others are closed
    without adding downtime (they covered the same outage).
    """
    rows = [
        row_to_dict(row)
        for row in conn.execute(
            "SELECT id, alert_key, site_id, device_id FROM alert_instances "
            "WHERE status = 'open' ORDER BY down_started_at ASC, id ASC"
        ).fetchall()
    ]
    kept: set[tuple[str, str]] = set()
    rekeyed = closed = 0
    for row in rows:
        device = (row.get("site_id") or "", row.get("device_id") or "")
        if device in kept:
            conn.execute(
                "UPDATE alert_instances SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (row["id"],),
            )
            closed += 1
            continue
        kept.add(device)
        new_key = build_alert_key(*device)
        if row.get("alert_key") != new_key:
            # Only one open row per device remains, so the unique open-key index holds.
            conn.execute("UPDATE alert_instances SET alert_key = %s WHERE id = %s", (new_key, row["id"]))
            rekeyed += 1
    if rekeyed or closed:
        logger.info("Alert keys migrated to site + device: %d re-keyed, %d duplicates closed", rekeyed, closed)


def mark_nautobot_inventory_sync_pending(conn) -> None:
    marker = placeholders(1)
    conn.execute(
        f"""
        UPDATE inventory_sync_state
        SET last_started_at = NULL,
            last_completed_at = NULL,
            last_successful_sync = NULL,
            cache_version = '',
            status = 'pending',
            error_message = ''
        WHERE source = {marker}
        """,
        ("nautobot_inventory",),
    )
