"""Notifications when a site's alert level changes (#282).

A level change that crosses ``NOTIFY_MIN_LEVEL`` (a site becomes Critical,
or drops back below it) is written to ``notification_outbox`` in the same
transaction that records the change, one row per configured channel.  The
background scheduler, which runs in one process at a time, sends what is
due; rows are claimed with ``FOR UPDATE SKIP LOCKED``, so overlapping
senders never take the same row.  Delivery is at least once: a crash after
the receiver accepted a message but before "sent" is saved resends it, so
every event carries a stable ``id`` receivers can use to drop duplicates.
Failures are retried per row with a growing delay and given up after
``MAX_ATTEMPTS``.  More than ``NOTIFY_SUMMARY_THRESHOLD`` messages due at
once for a channel go out as summaries of at most ``SUMMARY_MAX_EVENTS``.

Channels: a generic JSON webhook (optionally signed), a Microsoft Teams
Workflows webhook (Adaptive Card) and email (SMTP).  Webhook URLs and SMTP
passwords are secrets: errors are logged and reported without them.
"""

import hashlib
import hmac
import html
import json
import logging
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import requests

from nautobot_maps import db, settings, timeutil

logger = logging.getLogger(__name__)

LEVEL_RANK = {"ok": 0, "no_data": 0, "low": 1, "medium": 2, "critical": 3}
LEVEL_LABEL = {
    "ok": "OK",
    "no_data": "No data",
    "low": "Low",
    "medium": "Medium",
    "critical": "Critical",
    "maintenance": "Maintenance",
}
CHANNELS = ("webhook", "teams", "email")
MAX_ATTEMPTS = 10
# Delay before attempt n+1 (minutes), capped at the last value.
RETRY_MINUTES = (1, 2, 5, 10, 15, 30)
SEND_TIMEOUT_SECONDS = 10
KEEP_DAYS = 30
MAX_DEVICES_IN_MESSAGE = 20
# Events per summary message: keeps a backlog within webhook, Teams and mail size limits.
SUMMARY_MAX_EVENTS = 25


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def configured_channels() -> list[str]:
    channels = []
    if settings.NOTIFY_WEBHOOK_URL:
        channels.append("webhook")
    if settings.NOTIFY_TEAMS_WEBHOOK_URL:
        channels.append("teams")
    if settings.NOTIFY_EMAIL_TO and settings.SMTP_HOST:
        channels.append("email")
    return channels


def event_kind(from_level: str, to_level: str) -> str | None:
    """ "alarm" when a site reaches the minimum level or worse (or gets worse
    above it), "recovery" when it drops below it, otherwise None."""
    # Going into maintenance is planned (#283): no message.  Coming out of it
    # counts from "nothing": still Critical when it ends is an alarm.
    if to_level == "maintenance":
        return None
    minimum = LEVEL_RANK[settings.NOTIFY_MIN_LEVEL]
    before, after = LEVEL_RANK.get(from_level, 0), LEVEL_RANK.get(to_level, 0)
    if after >= minimum and after > before:
        return "alarm"
    if before >= minimum > after:
        return "recovery"
    return None


def tenant_matches(tenants: list[str]) -> bool:
    if not settings.NOTIFY_TENANTS:
        return True
    return any((tenant or "").strip().lower() in settings.NOTIFY_TENANTS for tenant in tenants)


def build_event(kind: str, from_level: str, to_level: str, site: dict, changed_at: str) -> dict:
    """What a message says, from the site's board row at the moment of the change."""
    devices = site.get("down_devices") or []
    cases = sorted({case for device in devices for case in (device.get("case_numbers") or [])})
    site_id = site.get("id") or ""
    return {
        # The same on every channel and every resend: receivers can drop duplicates.
        "id": hashlib.sha256(f"{site_id}|{changed_at}|{from_level}|{to_level}".encode()).hexdigest()[:32],
        "type": kind,
        "changed_at": changed_at,
        "level": to_level,
        "previous_level": from_level,
        "site": {
            "id": site_id,
            "name": site.get("name") or "",
            "path": site.get("ancestor_path") or site.get("parent") or "",
            "address": site.get("physical_address") or site.get("facility") or "",
            "tenants": site.get("tenants") or ([site["tenant"]] if site.get("tenant") else []),
        },
        "reason": site.get("alert_reason") or "",
        "down_device_count": site.get("down_device_count") or len(devices),
        "device_count": site.get("device_count") or 0,
        "down_devices": [
            {
                "name": device.get("device_name") or device.get("device_id") or "",
                "ip": device.get("device_ip") or "",
                "role": device.get("role") or "",
                "down_since": device.get("down_started_at") or "",
                "cases": device.get("case_numbers") or [],
            }
            for device in devices[:MAX_DEVICES_IN_MESSAGE]
        ],
        "cases": cases,
        "board_url": board_url(),
    }


def board_url() -> str:
    return f"{settings.NOTIFY_BOARD_URL}/alerts" if settings.NOTIFY_BOARD_URL else ""


def enqueue_change(conn, site_id: str, from_level: str, to_level: str, site: dict | None, changed_at: str) -> int:
    """Queue messages for one level change, inside the caller's transaction.

    Returns how many rows were queued (one per configured channel).
    """
    channels = configured_channels()
    kind = event_kind(from_level, to_level)
    if not channels or kind is None:
        return 0
    site = site or {"id": site_id}
    if not tenant_matches(site.get("tenants") or ([site["tenant"]] if site.get("tenant") else [])):
        return 0
    payload = json.dumps(build_event(kind, from_level, to_level, site, changed_at), sort_keys=True)
    for channel in channels:
        conn.execute(
            "INSERT INTO notification_outbox (channel, kind, site_id, payload_json, created_at, next_attempt_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (channel, kind, site_id, payload, changed_at, changed_at),
        )
    return len(channels)


# ---------------------------------------------------------------------------
# Message text
# ---------------------------------------------------------------------------


def title(event: dict) -> str:
    site = event["site"]["name"] or event["site"]["id"]
    level = LEVEL_LABEL.get(event["level"], event["level"])
    if event["type"] == "recovery":
        return f"✅ {site} recovered (now {level})"
    if event["type"] == "test":
        return "Nautobot Maps test notification"
    return f"🔴 {site} is {level}"


def summary_title(events: list[dict]) -> str:
    alarms = sum(1 for event in events if event["type"] == "alarm")
    recoveries = len(events) - alarms
    parts = []
    if alarms:
        parts.append(f"{alarms} site{'s' if alarms != 1 else ''} in alarm")
    if recoveries:
        parts.append(f"{recoveries} recovered")
    return "Nautobot Maps: " + ", ".join(parts)


def readable_time(value: str) -> str:
    """ "2026-10-09 08:09 UTC" for people; the webhook keeps the exact ISO time."""
    parsed = timeutil.parse_iso_datetime(value) if value else None
    if parsed is None:
        return value or ""
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def detail_lines(event: dict) -> list[str]:
    """The message body as lines of plain text."""
    if event["type"] == "test":
        return ["Notifications from Nautobot Maps reach this channel."]
    site = event["site"]
    lines = []
    if site["path"]:
        lines.append(f"Location: {site['path']}")
    if site["address"]:
        lines.append(f"Address: {site['address']}")
    if site["tenants"]:
        lines.append(f"Tenants: {', '.join(site['tenants'])}")
    lines.append(
        f"Level: {LEVEL_LABEL.get(event['previous_level'], event['previous_level'])} → "
        f"{LEVEL_LABEL.get(event['level'], event['level'])}"
    )
    if event["reason"]:
        lines.append(f"Reason: {event['reason']}")
    if event["type"] == "alarm":
        lines.append(f"Down: {event['down_device_count']} of {event['device_count']} monitored devices")
        for device in event["down_devices"]:
            parts = [device["name"], device["ip"], device["role"]]
            if device["down_since"]:
                parts.append(f"down since {readable_time(device['down_since'])}")
            if device["cases"]:
                parts.append(f"case {', '.join(device['cases'])}")
            lines.append("- " + " | ".join(part for part in parts if part))
        lines.append(f"Cases: {', '.join(event['cases'])}" if event["cases"] else "Cases: none yet")
    if event["board_url"]:
        lines.append(f"Board: {event['board_url']}")
    return lines


def summary_lines(events: list[dict]) -> list[str]:
    lines = []
    for event in events:
        site = event["site"]["name"] or event["site"]["id"]
        level = LEVEL_LABEL.get(event["level"], event["level"])
        mark = "✅" if event["type"] == "recovery" else "🔴"
        extra = f" ({event['down_device_count']} down)" if event["type"] == "alarm" else ""
        lines.append(f"{mark} {site}: {level}{extra}")
    url = next((event["board_url"] for event in events if event["board_url"]), "")
    if url:
        lines.append(f"Board: {url}")
    return lines


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


class SendError(Exception):
    """A channel failed; the message never contains a URL or password."""


def _post_json(url: str, body: bytes, headers: dict) -> None:
    try:
        resp = requests.post(
            url,
            data=body,
            headers={"Content-Type": "application/json", **headers},
            timeout=SEND_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        # requests' messages include the URL, which holds the webhook's secret.
        raise SendError(type(exc).__name__) from None
    if not 200 <= resp.status_code < 300:
        raise SendError(f"HTTP {resp.status_code}")


def send_webhook(message: dict) -> None:
    body = json.dumps(message, sort_keys=True).encode("utf-8")
    headers = {}
    if settings.NOTIFY_WEBHOOK_SECRET:
        digest = hmac.new(settings.NOTIFY_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-Nautobot-Maps-Signature"] = f"sha256={digest}"
    _post_json(settings.NOTIFY_WEBHOOK_URL, body, headers)


def teams_card(heading: str, lines: list[str], url: str, kind: str) -> dict:
    """An Adaptive Card in the shape a Teams Workflows webhook posts to a channel."""
    color = {"recovery": "Good", "alarm": "Attention"}.get(kind, "Default")
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": heading, "weight": "Bolder", "size": "Medium", "wrap": True, "color": color},
            *({"type": "TextBlock", "text": line, "wrap": True, "spacing": "None"} for line in lines),
        ],
    }
    if url:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Open the alert board", "url": url}]
    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}
        ],
    }


def send_teams(heading: str, lines: list[str], url: str, kind: str) -> None:
    body = json.dumps(teams_card(heading, lines, url, kind)).encode("utf-8")
    _post_json(settings.NOTIFY_TEAMS_WEBHOOK_URL, body, {})


def send_email(heading: str, lines: list[str]) -> None:
    message = EmailMessage()
    message["Subject"] = heading
    message["From"] = settings.SMTP_FROM or settings.SMTP_USERNAME or "nautobot-maps@localhost"
    message["To"] = ", ".join(settings.NOTIFY_EMAIL_TO)
    message.set_content("\n".join(lines))
    message.add_alternative(
        f"<h3>{html.escape(heading)}</h3>" + "".join(f"<div>{html.escape(line)}</div>" for line in lines),
        subtype="html",
    )
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=SEND_TIMEOUT_SECONDS) as smtp:
            if settings.SMTP_STARTTLS:
                smtp.starttls()
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise SendError(type(exc).__name__) from None


def deliver(channel: str, events: list[dict]) -> None:
    """Send one message (or one summary for several events) on *channel*."""
    if len(events) == 1:
        heading, lines = title(events[0]), detail_lines(events[0])
        kind, url = events[0]["type"], events[0]["board_url"]
        webhook_body = events[0]
    else:
        heading, lines = summary_title(events), summary_lines(events)
        kind = "alarm" if any(event["type"] == "alarm" for event in events) else "recovery"
        url = next((event["board_url"] for event in events if event["board_url"]), "")
        webhook_body = {"type": "summary", "events": events}
    if channel == "webhook":
        send_webhook(webhook_body)
    elif channel == "teams":
        send_teams(heading, lines, url, kind)
    elif channel == "email":
        send_email(heading, lines)
    else:
        raise SendError(f"unknown channel {channel}")


# ---------------------------------------------------------------------------
# Sending what is due
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return timeutil.parse_iso_datetime(timeutil.iso_utc_now())


def retry_delay(attempts: int) -> timedelta:
    return timedelta(minutes=RETRY_MINUTES[min(attempts, len(RETRY_MINUTES)) - 1])


def send_pending(conn=None) -> int:
    """Send every message that is due, per channel; returns how many were sent."""
    owns_conn = conn is None
    if owns_conn:
        conn = db.get_conn()
    if conn is None:
        return 0
    sent = 0
    try:
        for channel in CHANNELS:
            sent += _send_channel(conn, channel)
        conn.execute(
            "DELETE FROM notification_outbox WHERE status IN ('sent', 'failed') AND created_at < %s",
            (_now() - timedelta(days=KEEP_DAYS),),
        )
    finally:
        if owns_conn:
            conn.close()
    return sent


def _send_channel(conn, channel: str) -> int:
    now = _now()
    with db.transaction(conn):
        rows = conn.execute(
            "SELECT id, payload_json, attempts FROM notification_outbox "
            "WHERE channel = %s AND status = 'pending' AND next_attempt_at <= %s "
            "ORDER BY id FOR UPDATE SKIP LOCKED",
            (channel, now),
        ).fetchall()
        if not rows:
            return 0
        rows = [db.row_to_dict(row) for row in rows]
        # A burst (e.g. a wide outage) goes out as summaries, not one message per
        # site, each small enough for the channel.
        if len(rows) > settings.NOTIFY_SUMMARY_THRESHOLD:
            batches = [rows[start : start + SUMMARY_MAX_EVENTS] for start in range(0, len(rows), SUMMARY_MAX_EVENTS)]
        else:
            batches = [[row] for row in rows]
        sent = 0
        for batch_rows in batches:
            batch = [json.loads(row["payload_json"]) for row in batch_rows]
            ids = [row["id"] for row in batch_rows]
            try:
                deliver(channel, batch)
            except Exception as exc:
                error = str(exc) if isinstance(exc, SendError) else type(exc).__name__
                logger.warning("Notification on %s failed for %s message(s): %s", channel, len(ids), error)
                # Each row keeps its own retry count: a fresh row in a summary
                # with an old one gets all its attempts.
                for row in batch_rows:
                    attempts = row["attempts"] + 1
                    failed = attempts >= MAX_ATTEMPTS
                    if failed:
                        logger.warning("Notification %s on %s given up after %s attempts", row["id"], channel, attempts)
                    conn.execute(
                        "UPDATE notification_outbox SET attempts = %s, last_error = %s, status = %s, "
                        "next_attempt_at = %s WHERE id = %s",
                        (
                            attempts,
                            error[:300],
                            "failed" if failed else "pending",
                            now + retry_delay(attempts),
                            row["id"],
                        ),
                    )
                continue
            conn.execute(
                "UPDATE notification_outbox SET status = 'sent', sent_at = %s, attempts = attempts + 1, "
                "last_error = '' WHERE id = ANY(%s)",
                (now, ids),
            )
            sent += len(ids)
    return sent


def send_test() -> dict[str, str]:
    """Send a test message on every configured channel now; ``{channel: "ok" | error}``."""
    event = {
        "id": "test",
        "type": "test",
        "changed_at": timeutil.iso_utc_now(),
        "level": "",
        "previous_level": "",
        "site": {"id": "", "name": "", "path": "", "address": "", "tenants": []},
        "reason": "",
        "down_device_count": 0,
        "device_count": 0,
        "down_devices": [],
        "cases": [],
        "board_url": board_url(),
    }
    results = {}
    for channel in configured_channels():
        try:
            deliver(channel, [event])
            results[channel] = "ok"
        except SendError as exc:
            results[channel] = f"error: {exc}"
        except Exception as exc:
            results[channel] = f"error: {type(exc).__name__}"
    return results
