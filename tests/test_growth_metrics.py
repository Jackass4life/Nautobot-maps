"""How big the history gets, in /metrics (#298)."""

import app  # noqa: F401  (initializes the cache pg_database clears)
from nautobot_maps import db, metrics


def scrape() -> str:
    conn = db.get_conn()
    try:
        return metrics.collect(conn)
    finally:
        conn.close()


def test_table_sizes_and_the_oldest_history(pg_database):
    text = scrape()
    assert 'nautobot_maps_table_bytes{table="alert_instances"}' in text
    assert 'nautobot_maps_table_rows_estimate{table="alert_events"}' in text
    samples = [line for line in text.splitlines() if not line.startswith("#")]
    assert not any(line.startswith("nautobot_maps_history_oldest") for line in samples), "no history yet: no sample"
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            conn.execute(
                "INSERT INTO site_level_changes (site_id, site_name, from_level, to_level, changed_at) "
                "VALUES ('s1', 'Oslo', 'ok', 'critical', '2026-01-01T00:00:00Z')"
            )
    finally:
        conn.close()
    assert "nautobot_maps_history_oldest_timestamp_seconds 1767225600" in scrape()


def test_only_this_schema(pg_database):
    """Another schema's tables (other tests, other apps) are not ours."""
    tables = {line.split('"')[1] for line in scrape().splitlines() if line.startswith("nautobot_maps_table_bytes{")}
    assert "alert_instances" in tables and "schema_migrations" in tables
    assert not any(name.startswith("pg_") for name in tables)
