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
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
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


def get_conn(connect_timeout: int | None = None):
    """Return a PostgreSQL connection, or ``None`` when persistence is disabled.

    Connecting gives up after DB_CONNECT_TIMEOUT_SECONDS (or *connect_timeout*),
    and every statement after DB_STATEMENT_TIMEOUT_SECONDS (#190).
    """
    if not dialect():
        return None
    if psycopg is None:
        logger.error(
            "NAUTOBOT_MAPS_DATABASE_URL is set but psycopg is unavailable; "
            "install psycopg to enable PostgreSQL persistence"
        )
        return None
    url = settings.NAUTOBOT_MAPS_DATABASE_URL
    # Add to any options the URL already has (e.g. a search_path), not replace them.
    options = conninfo_to_dict(url).get("options") or ""
    options = f"{options} -c statement_timeout={settings.DB_STATEMENT_TIMEOUT_SECONDS * 1000}".strip()
    return psycopg.connect(
        make_conninfo(url, connect_timeout=connect_timeout or settings.DB_CONNECT_TIMEOUT_SECONDS, options=options),
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


def baseline_schema(conn) -> None:
    """Migration 1: the schema as it was when versioned migrations began (#201).

    Idempotent, so databases created before #201 run it once more and are
    recorded as version 1.  **Frozen:** don't edit it; add a new step to
    MIGRATIONS instead (a test checks).
    """
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
    # Each site's alert level at the last board build, and a log of
    # changes, for the alert feed (#180).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS site_alert_levels (
            site_id     TEXT PRIMARY KEY,
            site_name   TEXT NOT NULL DEFAULT '',
            alert_level TEXT NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS site_level_changes (
            id         BIGSERIAL PRIMARY KEY,
            site_id    TEXT NOT NULL,
            site_name  TEXT NOT NULL DEFAULT '',
            from_level TEXT NOT NULL,
            to_level   TEXT NOT NULL,
            changed_at TIMESTAMPTZ NOT NULL
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nautobot_device_cache_location ON nautobot_device_cache(location_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_instances_site_status ON alert_instances(site_id, status)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_alert_events_instance_time ON alert_events(alert_instance_id, event_at)"
    )
    # The alert feed reads the newest events and level changes (#180).
    conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_events_time ON alert_events(event_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_site_level_changes_time ON site_level_changes(changed_at)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_instances_open_key ON alert_instances(alert_key) WHERE status = 'open'"
    )


def add_sync_last_succeeded_at(conn) -> None:
    """When a sync last succeeded, for /metrics and /healthz (#200).

    A failed sync overwrites last_completed_at, so that can't tell.  Syncs
    whose last run succeeded start from their completion time.
    """
    conn.execute("ALTER TABLE inventory_sync_state ADD COLUMN IF NOT EXISTS last_succeeded_at TIMESTAMPTZ")
    conn.execute(
        "UPDATE inventory_sync_state SET last_succeeded_at = last_completed_at "
        "WHERE status = 'idle' AND last_succeeded_at IS NULL"
    )


def add_location_tenant_cache(conn) -> None:
    """Tenants linked to a location by a Nautobot Relationship (#238)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nautobot_location_tenant_cache (
            location_id  TEXT NOT NULL,
            tenant_id    TEXT NOT NULL,
            tenant       TEXT NOT NULL DEFAULT '',
            relationship TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (location_id, tenant_id, relationship)
        )
        """
    )


def add_tenant_cache(conn) -> None:
    """Every Nautobot tenant with its description, for the tenant (i) (#263)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nautobot_tenant_cache (
            tenant_id   TEXT PRIMARY KEY,
            name        TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT ''
        )
        """
    )


def add_notification_outbox(conn) -> None:
    """Messages waiting to be sent, one row per channel (#282)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_outbox (
            id              BIGSERIAL PRIMARY KEY,
            channel         TEXT NOT NULL,
            kind            TEXT NOT NULL,
            site_id         TEXT NOT NULL DEFAULT '',
            payload_json    TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            attempts        INTEGER NOT NULL DEFAULT 0,
            last_error      TEXT NOT NULL DEFAULT '',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            sent_at         TIMESTAMPTZ
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS notification_outbox_due ON notification_outbox (channel, status, next_attempt_at)"
    )


def add_maintenance_windows(conn) -> None:
    """Planned work: a whole site (device_id '') or one device, from-to (#283)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS maintenance_windows (
            id          BIGSERIAL PRIMARY KEY,
            site_id     TEXT NOT NULL,
            device_id   TEXT NOT NULL DEFAULT '',
            starts_at   TIMESTAMPTZ NOT NULL,
            ends_at     TIMESTAMPTZ NOT NULL,
            reason      TEXT NOT NULL DEFAULT '',
            created_by  TEXT NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ended_at    TIMESTAMPTZ,
            ended_by    TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS maintenance_windows_time ON maintenance_windows (ends_at, starts_at)")


def add_api_tokens(conn) -> None:
    """Named API tokens for scripts and MCP clients; only a hash is kept (#297)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_tokens (
            id           BIGSERIAL PRIMARY KEY,
            name         TEXT NOT NULL UNIQUE,
            token_hash   TEXT NOT NULL UNIQUE,
            prefix       TEXT NOT NULL,
            role         TEXT NOT NULL,
            created_by   TEXT NOT NULL DEFAULT '',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at   TIMESTAMPTZ,
            last_used_at TIMESTAMPTZ,
            revoked_at   TIMESTAMPTZ,
            revoked_by   TEXT NOT NULL DEFAULT ''
        )
        """
    )


def add_librenms_push_columns(conn) -> None:
    """A LibreNMS alert push (#284): when it last refreshed a device, and
    whether the board still has to be rebuilt for it."""
    conn.execute("ALTER TABLE librenms_device_status ADD COLUMN IF NOT EXISTS pushed_at TIMESTAMPTZ")
    conn.execute(
        "ALTER TABLE librenms_device_status ADD COLUMN IF NOT EXISTS push_pending BOOLEAN NOT NULL DEFAULT FALSE"
    )


def librenms_push_seq(conn) -> None:
    """LibreNMS pushes (#284) after review: a sequence number per refresh, so a
    board rebuild acknowledges exactly what it was built from (replaces
    push_pending); an observation number per LibreNMS request, so an older
    answer never overwrites a newer one; an index for pushes that name a
    hostname."""
    conn.execute("CREATE SEQUENCE IF NOT EXISTS librenms_push_seq")
    # Which LibreNMS answer is newer: numbered before each request (a clock
    # can tie or step back; a sequence can't).
    conn.execute("CREATE SEQUENCE IF NOT EXISTS librenms_observation_seq")
    conn.execute("ALTER TABLE librenms_device_status ADD COLUMN IF NOT EXISTS observed_seq BIGINT")
    conn.execute("ALTER TABLE librenms_device_status ADD COLUMN IF NOT EXISTS push_seq BIGINT")
    # Pushes still waiting for their rebuild keep waiting.
    conn.execute("UPDATE librenms_device_status SET push_seq = nextval('librenms_push_seq') WHERE push_pending")
    conn.execute("ALTER TABLE librenms_device_status DROP COLUMN push_pending")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS librenms_device_status_hostname_lower ON librenms_device_status (lower(hostname))"
    )


# Numbered schema changes, each applied once, in order, and recorded in
# schema_migrations (#201).  Never edit or reorder a step that may have been
# applied; append a new one.
MIGRATIONS = (
    (1, "baseline schema", baseline_schema),
    (2, "inventory_sync_state.last_succeeded_at", add_sync_last_succeeded_at),
    (3, "nautobot_location_tenant_cache", add_location_tenant_cache),
    (4, "nautobot_tenant_cache", add_tenant_cache),
    (5, "notification_outbox", add_notification_outbox),
    (6, "maintenance_windows", add_maintenance_windows),
    (7, "api_tokens", add_api_tokens),
    (8, "librenms_device_status push columns", add_librenms_push_columns),
    (9, "librenms_push_seq", librenms_push_seq),
)
SCHEMA_VERSION = MIGRATIONS[-1][0]
MIGRATION_LOCK_KEY = 674864467105151045


def schema_version(conn) -> int | None:
    """The database's schema version; None before versioned migrations (#201)."""
    exists = row_to_dict(conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL AS e").fetchone())
    if not exists.get("e"):
        return None
    row = row_to_dict(conn.execute("SELECT COALESCE(max(version), 0) AS v FROM schema_migrations").fetchone())
    return int(row.get("v") or 0)


def check_not_newer(version: int | None) -> None:
    if version is not None and version > SCHEMA_VERSION:
        raise RuntimeError(
            f"The database schema is version {version}, newer than this release of Nautobot Maps knows "
            f"({SCHEMA_VERSION}). Was the app rolled back? Run the release the database was migrated with, "
            "or restore a backup taken before the upgrade."
        )


def init_db() -> None:
    """Create or migrate the schema (#201).

    A database that is already current costs one query, so every worker can
    call this on startup; the container runs ``python -m nautobot_maps
    migrate`` first, so migrations don't run inside worker startup.  Refuses
    a database newer than this code.
    """
    conn = get_conn()
    if conn is None:
        return
    try:
        current = schema_version(conn)
        check_not_newer(current)
        if current != SCHEMA_VERSION:
            with transaction(conn):
                # Waiting for another worker's migration, or a migration on a big
                # table, may take longer than a request's statement timeout.
                conn.execute("SET LOCAL statement_timeout = 0")
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,))
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version    INTEGER PRIMARY KEY,
                        name       TEXT NOT NULL,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                # Another process may have migrated while this one waited.
                current = schema_version(conn) or 0
                check_not_newer(current)
                for version, name, step in MIGRATIONS:
                    if version > current:
                        step(conn)
                        conn.execute("INSERT INTO schema_migrations (version, name) VALUES (%s, %s)", (version, name))
                        logger.info("Database migration %d applied: %s", version, name)
    finally:
        conn.close()
    logger.info("Nautobot Maps persistence initialised (postgres, schema version %d)", SCHEMA_VERSION)


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
