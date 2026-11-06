"""Time helpers, as the current app's nautobot_maps/timeutil.py (#592).

Called as ``timeutil.iso_utc_now()`` everywhere, so tests can freeze time
with ``monkeypatch.setattr(timeutil, "iso_utc_now", ...)``.
"""

from datetime import UTC, datetime, timedelta


def iso_utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def now() -> datetime:
    """``iso_utc_now()`` as an aware datetime (frozen with it in tests)."""
    parsed = parse_iso_datetime(iso_utc_now())
    assert parsed is not None
    return parsed


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def as_utc_datetime(value: object) -> datetime | None:
    """A database or ISO time as an aware datetime; a naive one is UTC (#350)."""
    parsed = value if isinstance(value, datetime) else parse_iso_datetime(value)  # type: ignore[arg-type]
    return parsed.replace(tzinfo=UTC) if parsed is not None and parsed.tzinfo is None else parsed


def iso(value: object) -> object:
    """A database value as the current app answers it: a datetime as ISO UTC with "Z"."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value


def row(mapping: object) -> dict:
    """A database row as a dict, datetimes as ``iso()`` (db.row_to_dict)."""
    if mapping is None:
        return {}
    return {key: iso(value) for key, value in dict(mapping).items()}  # type: ignore[call-overload]


def max_last_updated(items: list, fallback: str | None = None) -> str | None:
    latest = parse_iso_datetime(fallback)
    result = fallback
    for item in items:
        candidate = parse_iso_datetime((item or {}).get("last_updated"))
        if candidate and (latest is None or candidate > latest):
            latest = candidate
            result = candidate.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return result


def next_watermark(value: str | None) -> str | None:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return value
    return (parsed + timedelta(microseconds=1)).astimezone(UTC).isoformat().replace("+00:00", "Z")


def max_iso_datetime_value(*values: str | None) -> str | None:
    latest = None
    result = None
    for value in values:
        candidate = parse_iso_datetime(value)
        if candidate and (latest is None or candidate > latest):
            latest = candidate
            result = candidate.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return result
