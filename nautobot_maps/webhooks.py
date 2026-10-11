"""Device status from other monitoring tools (#292).

Prometheus Alertmanager (its webhook) and a generic webhook tell the app a
device is down; each firing alert is a row in ``webhook_device_alerts``
until it is resolved, or not refreshed within WEBHOOK_ALERT_TTL_HOURS (a
lost "resolved").  The board matches them to Nautobot devices like
LibreNMS: by name, then primary IP.  Additive: a device is never marked up.
"""

import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from nautobot_maps import db, inventory, settings, timeutil

logger = logging.getLogger(__name__)

MAX_ALERTS = 1000
MAX_KEY_LENGTH = 255
MAX_SUMMARY_LENGTH = 500
SOURCE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")


class WebhookError(ValueError):
    """A payload that can't be read; ``message`` says why (written here, safe to show)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def device_key(value) -> str:
    """The host or IP in *value*: ``sw1:9100``, ``[2001:db8::1]:9100`` and
    ``https://sw1/metrics`` are ``sw1`` and ``2001:db8::1``; lower case."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if "://" in text:
        try:
            text = urlsplit(text).hostname or ""
        except ValueError:
            return ""
    elif text.startswith("[") and "]" in text:
        text = text[1 : text.index("]")]
    elif text.count(":") == 1:
        text = text.split(":", 1)[0]
    text = text.strip().lower()
    return text if len(text) <= MAX_KEY_LENGTH else ""


def _time(value) -> datetime | None:
    """An ISO time, None when missing; Alertmanager's ``0001-01-01`` is missing."""
    parsed = timeutil.parse_iso_datetime(value) if isinstance(value, str) else None
    if parsed is None or parsed.year < 2000:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _text(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def parse_alertmanager(body) -> tuple[list[dict], int]:
    """``(events, skipped)`` from an Alertmanager webhook (version 4).

    The device is the first of ALERTMANAGER_DEVICE_LABELS an alert has;
    with ALERTMANAGER_ALERTNAMES set, other alerts are skipped.
    """
    if not isinstance(body, dict) or not isinstance(body.get("alerts"), list):
        raise WebhookError("Expected an Alertmanager webhook: a JSON object with an alerts list")
    if len(body["alerts"]) > MAX_ALERTS:
        raise WebhookError(f"At most {MAX_ALERTS} alerts per request")
    names = settings.ALERTMANAGER_ALERTNAMES
    events, skipped = [], 0
    for alert in body["alerts"]:
        labels = alert.get("labels") if isinstance(alert, dict) else None
        if not isinstance(labels, dict):
            skipped += 1
            continue
        alertname = labels.get("alertname") if isinstance(labels.get("alertname"), str) else ""
        if names and alertname.strip().lower() not in names:
            skipped += 1
            continue
        device = next(
            (key for key in (device_key(labels.get(label)) for label in settings.ALERTMANAGER_DEVICE_LABELS) if key),
            "",
        )
        state = {"firing": "down", "resolved": "up"}.get(alert.get("status"))
        if not device or state is None:
            skipped += 1
            continue
        key = _text(alert.get("fingerprint"), MAX_KEY_LENGTH)
        if not key:
            key = hashlib.sha256(json.dumps(labels, sort_keys=True, default=str).encode()).hexdigest()
        annotations = alert.get("annotations") if isinstance(alert.get("annotations"), dict) else {}
        summary = _text(annotations.get("summary"), MAX_SUMMARY_LENGTH) or _text(
            annotations.get("description"), MAX_SUMMARY_LENGTH
        )
        events.append(
            {
                "source": "alertmanager",
                "key": key,
                "device": device,
                "status": state,
                "summary": summary or alertname[:MAX_SUMMARY_LENGTH],
                "started_at": _time(alert.get("startsAt")),
            }
        )
    return events, skipped


def parse_generic(body) -> list[dict]:
    """Events from the generic webhook: one object or a list of them, each
    ``{"device", "status": "down"|"up", "source"?, "id"?, "summary"?, "started_at"?}``."""
    items = body if isinstance(body, list) else [body]
    if not items or len(items) > MAX_ALERTS:
        raise WebhookError(f"Expected 1 to {MAX_ALERTS} events")
    events = []
    for index, item in enumerate(items):
        where = f"event {index}: " if isinstance(body, list) else ""
        if not isinstance(item, dict):
            raise WebhookError(f"{where}expected a JSON object")
        device = device_key(item.get("device"))
        if not device:
            raise WebhookError(f"{where}device (a hostname or IP) is required")
        state = item.get("status")
        if state not in ("down", "up"):
            raise WebhookError(f"{where}status must be down or up")
        source = item.get("source", "webhook")
        if not isinstance(source, str) or not SOURCE_PATTERN.match(source.strip().lower()):
            raise WebhookError(f"{where}source: up to 40 lower-case letters, digits, dots, dashes, underscores")
        key = item.get("id", device)
        if not isinstance(key, (str, int)) or isinstance(key, bool) or not str(key).strip():
            raise WebhookError(f"{where}id must be a string or number")
        started = item.get("started_at")
        if started is not None and _time(started) is None:
            raise WebhookError(f"{where}started_at must be an ISO 8601 time")
        events.append(
            {
                "source": source.strip().lower(),
                "key": str(key).strip()[:MAX_KEY_LENGTH],
                "device": device,
                "status": state,
                "summary": _text(item.get("summary"), MAX_SUMMARY_LENGTH),
                "started_at": _time(started),
            }
        )
    return events


def store(conn, events: list[dict]) -> None:
    """A firing alert is stored or refreshed; a resolved one is removed."""
    now = timeutil.parse_iso_datetime(timeutil.iso_utc_now())
    with db.transaction(conn):
        for event in events:
            if event["status"] == "up":
                conn.execute(
                    "DELETE FROM webhook_device_alerts WHERE source = %s AND alert_key = %s",
                    (event["source"], event["key"]),
                )
                continue
            conn.execute(
                "INSERT INTO webhook_device_alerts "
                "(source, alert_key, device_key, summary, started_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (source, alert_key) DO UPDATE SET device_key = excluded.device_key, "
                "summary = excluded.summary, updated_at = excluded.updated_at, "
                "started_at = COALESCE(webhook_device_alerts.started_at, excluded.started_at)",
                (event["source"], event["key"], event["device"], event["summary"], event["started_at"] or now, now),
            )


def _keys(device: str) -> tuple[str, str]:
    """``("host", "")`` or ``("", "ip")``: how a device key is matched."""
    if inventory.is_ip_literal(device):
        return "", inventory.librenms_ip_key(device)
    return inventory.librenms_host_key(device), ""


def read_index(conn) -> dict:
    """``{"by_host": {...}, "by_ip": {...}}`` of the current alerts; per device the earliest."""
    cutoff = timeutil.parse_iso_datetime(timeutil.iso_utc_now()) - timedelta(hours=settings.WEBHOOK_ALERT_TTL_HOURS)
    rows = conn.execute(
        "SELECT source, device_key, summary, started_at FROM webhook_device_alerts "
        "WHERE updated_at > %s ORDER BY started_at, source, alert_key",
        (cutoff,),
    ).fetchall()
    index: dict = {"by_host": {}, "by_ip": {}}
    for row in map(db.row_to_dict, rows):
        started = row["started_at"]
        alert = {
            "source": row["source"],
            "summary": row["summary"] or "",
            "started_at": started.astimezone(UTC).isoformat().replace("+00:00", "Z")
            if isinstance(started, datetime)
            else str(started or ""),
        }
        host, ip = _keys(row["device_key"])
        if host:
            index["by_host"].setdefault(host, alert)
        if ip:
            index["by_ip"].setdefault(ip, alert)
    return index


def load_index() -> dict:
    """The current alerts' index; empty without a database or on an error."""
    conn = db.get_conn()
    if conn is None:
        return {}
    try:
        return read_index(conn)
    except Exception as exc:
        logger.warning("Could not read webhook device alerts: %s", type(exc).__name__)
        return {}
    finally:
        conn.close()


def find(index: dict, device: dict) -> dict | None:
    """The alert for a Nautobot *device* (by name, then primary IP), or None."""
    if not index:
        return None
    alert = index["by_host"].get(inventory.librenms_host_key(device.get("name") or ""))
    if alert is None:
        ip = inventory.librenms_ip_key(device.get("primary_ip") or "")
        alert = index["by_ip"].get(ip) if ip else None
    return alert


def matched(events: list[dict]) -> int:
    """How many of the firing *events* match a Nautobot device (a wrong label shows as 0)."""
    firing = [event for event in events if event["status"] == "down"]
    if not firing:
        return 0
    hosts, ips = set(), set()
    for device in inventory.read_devices():
        hosts.add(inventory.librenms_host_key(device.get("name") or ""))
        ip = inventory.librenms_ip_key(device.get("primary_ip") or "")
        if ip:
            ips.add(ip)
    count = 0
    for event in firing:
        host, ip = _keys(event["device"])
        count += bool((host and host in hosts) or (ip and ip in ips))
    return count
