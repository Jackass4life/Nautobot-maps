"""Time helpers (#165).

Called as ``timeutil.iso_utc_now()`` everywhere, so tests can freeze time
with ``monkeypatch.setattr(timeutil, "iso_utc_now", ...)``.
"""

from datetime import UTC, datetime, timedelta


def iso_utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


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
