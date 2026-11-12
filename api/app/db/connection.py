"""The ported code's database helpers (nautobot_maps/db.py, #600): rows as dicts, the
SQL placeholders and keys it uses. Its connections are ``app.db.aconnection``'s since
psycopg went (#664). Call these as ``db.function()`` so tests can replace them.
"""

import hashlib
from datetime import UTC, datetime

from app.utils import locks


def dialect() -> str:
    """Always PostgreSQL here: the new stack needs its database."""
    return "postgres"


def serialize_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value


def row_to_dict(row: object) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return {k: serialize_value(v) for k, v in row.items()}
    try:
        data = dict(row)  # type: ignore[call-overload]
        return {k: serialize_value(v) for k, v in data.items()}
    except (TypeError, ValueError):
        return {}


def placeholders(count: int) -> str:
    return ",".join("%s" for _ in range(count))


def sql_now() -> str:
    return "CURRENT_TIMESTAMP"


def build_alert_key(site_id: str, device_id: str) -> str:
    """A device's alert on a site; the severity is deliberately not part of it (#163)."""
    raw = f"{site_id.strip()}::{device_id.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def advisory_lock_key(name: str) -> int:
    """``app.utils.locks.advisory_lock_key``: the key `aconnection.try_advisory_lock` takes."""
    return locks.advisory_lock_key(name)
