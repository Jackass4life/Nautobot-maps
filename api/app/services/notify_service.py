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
Workflows webhook (Adaptive Card) and email (SMTP).  Customers (#707): a status
token's notification addresses get their own, customer-safe mail about their
tenant's sites (``customer_email``, one row per address), with SMTP_HOST alone.
A customer's alarm waits the token's ``notify_after_minutes`` (#729) and is
cancelled when the site is back first; one alarm per outage, and a recovery only
after an alarm that went out.  Webhook URLs and SMTP
passwords are secrets: errors are logged and reported without them.

Ported from nautobot_maps/notify.py (#602): only the imports changed.
"""

import asyncio
import hashlib
import hmac
import html
import json
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import httpx
from loguru import logger

from app.clients import http
from app.core.settings import settings
from app.db import aconnection
from app.db import connection as db
from app.services import itsm_service as itsm
from app.services import status_service as status
from app.utils import timeutil

LEVEL_RANK = {"ok": 0, "no_data": 0, "low": 1, "medium": 2, "critical": 3}
LEVEL_LABEL = {
    "ok": "OK",
    "no_data": "No data",
    "low": "Low",
    "medium": "Medium",
    "critical": "Critical",
    "maintenance": "Maintenance",
}
CHANNELS = ("webhook", "teams", "email", "customer_email")
CUSTOMER_CHANNEL = "customer_email"
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
    if settings.notify_webhook_url:
        channels.append("webhook")
    if settings.notify_teams_webhook_url:
        channels.append("teams")
    if settings.notify_email_to and settings.smtp_host:
        channels.append("email")
    return channels


def customer_mail_on() -> bool:
    """Whether customers' addresses (status tokens', #707) can be mailed."""
    return bool(settings.smtp_host)


def event_kind(from_level: str, to_level: str, tier: str = "") -> str | None:
    """ "alarm" when a site reaches the minimum level or worse (or gets worse
    above it), "recovery" when it drops below it, otherwise None.  The
    minimum is NOTIFY_MIN_LEVEL, or the site's tier's (#293)."""
    # Going into maintenance is planned (#283): no message.  Coming out of it
    # counts from "nothing": still Critical when it ends is an alarm.
    if to_level == "maintenance":
        return None
    minimum = LEVEL_RANK[
        settings.notify_min_level_by_tier.get((tier or "").lower(), settings.notify_min_level)
    ]
    before, after = LEVEL_RANK.get(from_level, 0), LEVEL_RANK.get(to_level, 0)
    if after >= minimum and after > before:
        return "alarm"
    if before >= minimum > after:
        return "recovery"
    return None


def dropped_below_minimum(from_level: str, to_level: str, tier: str = "") -> bool:
    """The level went down and is below the site's minimum now, but was below it before too."""
    if to_level == "maintenance":
        return False
    minimum = LEVEL_RANK[
        settings.notify_min_level_by_tier.get((tier or "").lower(), settings.notify_min_level)
    ]
    before, after = LEVEL_RANK.get(from_level, 0), LEVEL_RANK.get(to_level, 0)
    return after < before and before < minimum


def in_alarm(level: str, tier: str = "") -> bool:
    """Whether *level* is at the site's minimum or worse (not maintenance)."""
    if level == "maintenance":
        return False
    minimum = LEVEL_RANK[
        settings.notify_min_level_by_tier.get((tier or "").lower(), settings.notify_min_level)
    ]
    return LEVEL_RANK.get(level, 0) >= minimum


def tenant_matches(tenants: list[str]) -> bool:
    if not settings.notify_tenants:
        return True
    return any((tenant or "").strip().lower() in settings.notify_tenants for tenant in tenants)


def build_event(kind: str, from_level: str, to_level: str, site: dict, changed_at: str) -> dict:
    """What a message says, from the site's board row at the moment of the change."""
    devices = site.get("down_devices") or []
    cases = sorted({case for device in devices for case in (device.get("case_numbers") or [])})
    # From every device, before the list is cut to MAX_DEVICES_IN_MESSAGE (#285).
    likely_cause = {
        "cores": [
            device.get("device_name") or device.get("device_id") or ""
            for device in devices
            if device.get("core")
        ],
        "behind": sum(1 for device in devices if device.get("probable_cause")),
    }
    site_id = site.get("id") or ""
    return {
        # The same on every channel and every resend: receivers can drop duplicates.
        "id": hashlib.sha256(
            f"{site_id}|{changed_at}|{from_level}|{to_level}".encode()
        ).hexdigest()[:32],
        "type": kind,
        "changed_at": changed_at,
        "level": to_level,
        "previous_level": from_level,
        "site": {
            "id": site_id,
            "name": site.get("name") or "",
            "path": status.site_path(site),
            "address": status.site_address(site),
            "tenants": status.site_tenants(site),
        },
        "reason": site.get("alert_reason") or "",
        "tier": site.get("tier") or "",
        "down_device_count": site.get("down_device_count") or len(devices),
        "device_count": site.get("device_count") or 0,
        "down_devices": [
            {
                "name": device.get("device_name") or device.get("device_id") or "",
                "ip": device.get("device_ip") or "",
                "role": device.get("role") or "",
                "down_since": device.get("down_started_at") or "",
                "cases": device.get("case_numbers") or [],
                # The likely cause (#285): a core device, or what one is behind.
                "core": bool(device.get("core")),
                "probable_cause": device.get("probable_cause") or "",
            }
            for device in devices[:MAX_DEVICES_IN_MESSAGE]
        ],
        "cases": cases,
        "likely_cause": likely_cause,
        "board_url": board_url(),
    }


def board_url() -> str:
    return f"{settings.notify_board_url}/alerts" if settings.notify_board_url else ""


async def enqueue_change(
    conn: aconnection.Connection,
    site_id: str,
    from_level: str,
    to_level: str,
    site: dict | None,
    changed_at: str,
    quiet_recovery: bool = False,
) -> int:
    """Queue messages for one level change, inside the caller's transaction.

    *quiet_recovery*: the level dropped because devices went into
    maintenance (#283), which is planned, not a recovery to announce.
    Returns how many rows were queued: one per configured channel, and one per
    customer address (#707).
    """
    channels = configured_channels()
    if not channels and not customer_mail_on():
        return 0
    tier = (site or {}).get("tier") or ""
    if site_id and customer_mail_on() and not in_alarm(to_level, tier):
        # Back up (or in maintenance) before a customer's alarm went out (#729):
        # it never does, and no recovery follows.
        await conn.execute(
            "UPDATE notification_outbox SET status = 'cancelled' WHERE channel = %s "
            "AND site_id = %s AND kind = 'alarm' AND status = 'pending'",
            (CUSTOMER_CHANNEL, site_id),
        )
    kind = event_kind(from_level, to_level, tier)
    if kind is None and site_id and dropped_below_minimum(from_level, to_level, tier):
        # The alarm went out under another threshold (the site's tier changed
        # since, #293): its end is still a recovery.
        row = (
            await conn.execute(
                "SELECT kind FROM notification_outbox WHERE site_id = %s ORDER BY id DESC LIMIT 1",
                (site_id,),
            )
        ).fetchone()
        if row is not None and row["kind"] == "alarm":
            kind = "recovery"
    if kind is None or (quiet_recovery and kind == "recovery"):
        return 0
    site = site or {"id": site_id}
    queued = 0
    if channels and tenant_matches(status.site_tenants(site)):
        payload = json.dumps(
            build_event(kind, from_level, to_level, site, changed_at), sort_keys=True
        )
        for channel in channels:
            await _queue(conn, channel, kind, site_id, payload, changed_at)
        queued += len(channels)
    if customer_mail_on():
        last = await _last_customer_mails(conn, site_id)
        for recipient, payload, delay in await customer_events(
            conn, kind, from_level, to_level, site, changed_at
        ):
            before = last.get(recipient.lower())
            alarm_out = before is not None and before["kind"] == "alarm"
            if kind == "alarm":
                # One alarm per outage (#729): not while one waits or went out.
                if alarm_out and before["status"] in ("pending", "sent"):
                    continue
                due = (timeutil.parse_iso_datetime(changed_at) or _now()) + timedelta(minutes=delay)
            else:
                # A recovery only for an alarm the customer got.
                if not (alarm_out and before["status"] == "sent"):
                    continue
                due = None
            await _queue(conn, CUSTOMER_CHANNEL, kind, site_id, payload, changed_at, recipient, due)
            queued += 1
    return queued


async def _last_customer_mails(conn: aconnection.Connection, site_id: str) -> dict[str, dict]:
    """Each address's last customer mail about *site_id* (cancelled ones aside), by
    lowercase address: its ``kind`` and ``status``."""
    rows = (
        await conn.execute(
            "SELECT DISTINCT ON (lower(recipient)) lower(recipient) AS address, kind, status "
            "FROM notification_outbox WHERE channel = %s AND site_id = %s "
            "AND status <> 'cancelled' ORDER BY lower(recipient), id DESC",
            (CUSTOMER_CHANNEL, site_id),
        )
    ).fetchall()
    return {row["address"]: row for row in rows}


async def _queue(
    conn: aconnection.Connection,
    channel: str,
    kind: str,
    site_id: str,
    payload: str,
    changed_at: str,
    recipient: str = "",
    due: datetime | None = None,
) -> None:
    """One message to send from *due* on (default: at once, *changed_at*)."""
    await conn.execute(
        "INSERT INTO notification_outbox (channel, kind, site_id, payload_json, recipient, "
        "created_at, next_attempt_at) "
        "VALUES (%s, %s, %s, %s, %s, %s::text::timestamptz, %s::text::timestamptz)",
        (
            channel,
            kind,
            site_id,
            payload,
            recipient,
            changed_at,
            due.isoformat() if due is not None else changed_at,
        ),
    )


async def customer_events(
    conn: aconnection.Connection,
    kind: str,
    from_level: str,
    to_level: str,
    site: dict,
    changed_at: str,
) -> list[tuple[str, str, int]]:
    """``(address, payload, minutes)`` for each customer address to mail about *site*
    (#707): those of the active status tokens of the site's tenants (case-folded, as
    the status page matches them), one per address, with the shortest wait of its
    tokens before an alarm (#729). The payload holds only what the customer's status
    page shows: no devices, IPs, cases or board."""
    tenants = {tenant.strip().casefold(): tenant for tenant in status.site_tenants(site)}
    if not tenants:
        return []
    rows = (
        await conn.execute(
            "SELECT tenant, notify_email, notify_after_minutes FROM api_tokens "
            "WHERE role = 'status' "
            "AND notify_email <> '' AND revoked_at IS NULL "
            "AND (expires_at IS NULL OR expires_at > %s)",
            (_now(),),
        )
    ).fetchall()
    # Each address, the site's tenants it is a customer of, and its wait.
    addresses: dict[str, tuple[str, list[str]]] = {}
    waits: dict[str, int] = {}
    for row in rows:
        key = (row["tenant"] or "").strip().casefold()
        if key not in tenants:
            continue
        # None: saved without a wait, at once.
        wait = row["notify_after_minutes"] or 0
        for address in (part.strip() for part in row["notify_email"].split(",")):
            if not address:
                continue
            spelled, its_tenants = addresses.setdefault(address.lower(), (address, []))
            if tenants[key] not in its_tenants:
                its_tenants.append(tenants[key])
            waits[address.lower()] = min(wait, waits.get(address.lower(), wait))
    if not addresses:
        return []
    links = await _status_links(
        conn, sorted({t.casefold() for _, ts in addresses.values() for t in ts})
    )
    found = []
    for lowered, (spelled, its_tenants) in addresses.items():
        event = customer_event(kind, from_level, to_level, site, changed_at, its_tenants, links)
        found.append((spelled, json.dumps(event, sort_keys=True), waits[lowered]))
    return found


async def _status_links(conn: aconnection.Connection, keys: list[str]) -> dict[str, str]:
    """Each case-folded tenant's status page address, when it has a link and
    NOTIFY_BOARD_URL says where the app is."""
    if not settings.notify_board_url or not keys:
        return {}
    rows = (
        await conn.execute(
            "SELECT tenant_key, link_id FROM status_links WHERE tenant_key = ANY(%s)", (keys,)
        )
    ).fetchall()
    return {
        row["tenant_key"]: f"{settings.notify_board_url}/status/{row['link_id']}" for row in rows
    }


def customer_event(
    kind: str,
    from_level: str,
    to_level: str,
    site: dict,
    changed_at: str,
    tenants: list[str],
    links: dict[str, str],
) -> dict:
    """A customer mail's content (#707): the site, the change and the status page."""
    site_id = site.get("id") or ""
    return {
        "id": hashlib.sha256(
            f"{site_id}|{changed_at}|{from_level}|{to_level}".encode()
        ).hexdigest()[:32],
        "type": kind,
        "changed_at": changed_at,
        "level": to_level,
        "previous_level": from_level,
        "site": {
            "id": site_id,
            "name": site.get("name") or "",
            "address": status.site_address(site),
        },
        "tenants": tenants,
        "status_pages": [links[t.casefold()] for t in tenants if t.casefold() in links],
    }


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
    if event.get("tier"):
        lines.append(f"Tier: {event['tier'].capitalize()}")
    if event["reason"]:
        lines.append(f"Reason: {event['reason']}")
    if event["type"] == "alarm":
        lines.append(
            f"Down: {event['down_device_count']} of {event['device_count']} monitored devices"
        )
        cause = likely_cause_text(event)
        if cause:
            lines.append(f"Likely cause: {cause}")
        for device in event["down_devices"]:
            parts = [device["name"], device["ip"], device["role"]]
            if device.get("core"):
                parts.append("core")
            if device.get("probable_cause"):
                parts.append(f"likely caused by {device['probable_cause']}")
            if device["down_since"]:
                parts.append(f"down since {readable_time(device['down_since'])}")
            if device["cases"]:
                parts.append(f"case {', '.join(device['cases'])}")
            lines.append("- " + " | ".join(part for part in parts if part))
        lines.append(
            f"Cases: {', '.join(_case_text(case) for case in event['cases'])}"
            if event["cases"]
            else "Cases: none yet"
        )
    if event["board_url"]:
        lines.append(f"Board: {event['board_url']}")
    return lines


def _case_text(case: str) -> str:
    """ "INC-1 (https://itsm…/INC-1)" with CASE_URL_TEMPLATE (#289), else the number."""
    url = itsm.case_url(case)
    return f"{case} ({url})" if url else case


def likely_cause_text(event: dict) -> str:
    """ "core-rt01 down; 2 other devices probably behind it", or "" (#285)."""
    # Counted from every device when the event was made (older queued events: the listed ones).
    likely_cause = event.get("likely_cause") or {
        "cores": [device["name"] for device in event["down_devices"] if device.get("core")],
        "behind": sum(1 for device in event["down_devices"] if device.get("probable_cause")),
    }
    cores, behind = likely_cause["cores"], likely_cause["behind"]
    if not cores or not behind:
        return ""
    return f"{', '.join(cores)} down; {behind} other device{'s' if behind != 1 else ''} probably behind it"


def summary_lines(events: list[dict]) -> list[str]:
    lines = []
    for event in events:
        site = event["site"]["name"] or event["site"]["id"]
        level = LEVEL_LABEL.get(event["level"], event["level"])
        mark = "✅" if event["type"] == "recovery" else "🔴"
        extra = ""
        if event["type"] == "alarm":
            cause = likely_cause_text(event)
            extra = (
                f" ({event['down_device_count']} down; likely cause: {cause})"
                if cause
                else f" ({event['down_device_count']} down)"
            )
        tier = f" [{event['tier'].capitalize()}]" if event.get("tier") else ""
        lines.append(f"{mark} {site}{tier}: {level}{extra}")
    url = next((event["board_url"] for event in events if event["board_url"]), "")
    if url:
        lines.append(f"Board: {url}")
    return lines


def customer_title(event: dict) -> str:
    site = event["site"]["name"] or event["site"]["id"]
    level = LEVEL_LABEL.get(event["level"], event["level"])
    if event["type"] == "recovery":
        return f"✅ {site} recovered (now {level})"
    return f"🔴 {site} is {level}"


def customer_lines(event: dict) -> list[str]:
    """A customer mail's body (#707): what their status page shows, nothing internal."""
    lines = [f"Site: {event['site']['name'] or event['site']['id']}"]
    if event["site"]["address"]:
        lines.append(f"Address: {event['site']['address']}")
    if event["tenants"]:
        lines.append(f"Customer: {', '.join(event['tenants'])}")
    lines.append(
        f"Status: {LEVEL_LABEL.get(event['previous_level'], event['previous_level'])} → "
        f"{LEVEL_LABEL.get(event['level'], event['level'])}"
    )
    lines.append(f"Time: {readable_time(event['changed_at'])}")
    lines += _status_page_lines(event["status_pages"])
    return lines


def customer_summary_title(events: list[dict]) -> str:
    return summary_title(events).replace("Nautobot Maps: ", "Your sites: ", 1)


def customer_summary_lines(events: list[dict]) -> list[str]:
    lines = []
    for event in events:
        site = event["site"]["name"] or event["site"]["id"]
        level = LEVEL_LABEL.get(event["level"], event["level"])
        mark = "✅" if event["type"] == "recovery" else "🔴"
        lines.append(f"{mark} {site}: {level} ({readable_time(event['changed_at'])})")
    pages = sorted({page for event in events for page in event["status_pages"]})
    return lines + _status_page_lines(pages)


def _status_page_lines(pages: list[str]) -> list[str]:
    # The key isn't stored, so the link is without it: the customer's own link opens it.
    if not pages:
        return []
    label = "Status page" if len(pages) == 1 else "Status pages"
    return [f"{label} (open it with the link you were given): {', '.join(pages)}"]


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


class SendError(Exception):
    """A channel failed; the message never contains a URL or password."""


def _post(url: str, **kwargs) -> httpx.Response:
    """Not retried (a resent alert would arrive twice): the client, not http.request.
    Called as ``notify._post()`` so tests can replace it."""
    return http.client().post(url, **kwargs)


def _post_json(url: str, body: bytes, headers: dict) -> None:
    try:
        resp = _post(
            url,
            content=body,
            headers={"Content-Type": "application/json", **headers},
            timeout=SEND_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        # httpx's messages include the URL, which holds the webhook's secret.
        raise SendError(type(exc).__name__) from None
    if not 200 <= resp.status_code < 300:
        raise SendError(f"HTTP {resp.status_code}")


def send_webhook(message: dict) -> None:
    body = json.dumps(message, sort_keys=True).encode("utf-8")
    headers = {}
    if settings.notify_webhook_secret:
        digest = hmac.new(
            settings.notify_webhook_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        headers["X-Nautobot-Maps-Signature"] = f"sha256={digest}"
    _post_json(settings.notify_webhook_url, body, headers)


def teams_card(heading: str, lines: list[str], url: str, kind: str) -> dict:
    """An Adaptive Card in the shape a Teams Workflows webhook posts to a channel."""
    color = {"recovery": "Good", "alarm": "Attention"}.get(kind, "Default")
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": heading,
                "weight": "Bolder",
                "size": "Medium",
                "wrap": True,
                "color": color,
            },
            *(
                {"type": "TextBlock", "text": line, "wrap": True, "spacing": "None"}
                for line in lines
            ),
        ],
    }
    if url:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Open the alert board", "url": url}]
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card,
            }
        ],
    }


def send_teams(heading: str, lines: list[str], url: str, kind: str) -> None:
    body = json.dumps(teams_card(heading, lines, url, kind)).encode("utf-8")
    _post_json(settings.notify_teams_webhook_url, body, {})


def send_email(heading: str, lines: list[str], to: str = "") -> None:
    """To NOTIFY_EMAIL_TO, or to *to* alone (a customer, #707: never with others)."""
    message = EmailMessage()
    message["Subject"] = heading
    message["From"] = settings.smtp_from or settings.smtp_username or "nautobot-maps@localhost"
    message["To"] = to or ", ".join(settings.notify_email_to)
    message.set_content("\n".join(lines))
    message.add_alternative(
        f"<h3>{html.escape(heading)}</h3>"
        + "".join(f"<div>{html.escape(line)}</div>" for line in lines),
        subtype="html",
    )
    try:
        with smtplib.SMTP(
            settings.smtp_host, settings.smtp_port, timeout=SEND_TIMEOUT_SECONDS
        ) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise SendError(type(exc).__name__) from None


def deliver(channel: str, events: list[dict], recipient: str = "") -> None:
    """Send one message (or one summary for several events) on *channel*; a customer
    mail to *recipient*."""
    if channel == CUSTOMER_CHANNEL:
        if not recipient:
            raise SendError("no recipient")
        if len(events) == 1:
            send_email(customer_title(events[0]), customer_lines(events[0]), recipient)
        else:
            send_email(customer_summary_title(events), customer_summary_lines(events), recipient)
        return
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


async def send_pending() -> int:
    """Send every message that is due, per channel; returns how many were sent."""
    if not db.dialect():
        return 0
    sent = 0
    async with aconnection.connect() as conn:
        for channel in CHANNELS:
            sent += await _send_channel(conn, channel)
        await conn.execute(
            "DELETE FROM notification_outbox WHERE status IN ('sent', 'failed', 'cancelled') "
            "AND created_at < %s",
            (_now() - timedelta(days=KEEP_DAYS),),
        )
    return sent


async def _send_channel(conn: aconnection.Connection, channel: str) -> int:
    now = _now()
    async with conn.transaction():
        rows = (
            await conn.execute(
                "SELECT id, payload_json, attempts, recipient FROM notification_outbox "
                "WHERE channel = %s AND status = 'pending' AND next_attempt_at <= %s "
                "ORDER BY id FOR UPDATE SKIP LOCKED",
                (channel, now),
            )
        ).fetchall()
        if not rows:
            return 0
        # Each recipient on its own (customers, #707; one group for the others).
        by_recipient: dict[str, list] = {}
        for row in rows:
            by_recipient.setdefault(row["recipient"], []).append(row)
        batches = []
        for recipient, its_rows in by_recipient.items():
            # A burst (e.g. a wide outage) goes out as summaries, not one message per
            # site, each small enough for the channel.
            if len(its_rows) > settings.notify_summary_threshold:
                batches += [
                    (recipient, its_rows[start : start + SUMMARY_MAX_EVENTS])
                    for start in range(0, len(its_rows), SUMMARY_MAX_EVENTS)
                ]
            else:
                batches += [(recipient, [row]) for row in its_rows]
        sent = 0
        for recipient, batch_rows in batches:
            batch = [json.loads(row["payload_json"]) for row in batch_rows]
            ids = [row["id"] for row in batch_rows]
            try:
                # Sent from a thread: SMTP and the posts are blocking calls.
                await asyncio.to_thread(deliver, channel, batch, recipient)
            except Exception as exc:
                error = str(exc) if isinstance(exc, SendError) else type(exc).__name__
                logger.warning(
                    "Notification on {} failed for {} message(s): {}", channel, len(ids), error
                )
                # Each row keeps its own retry count: a fresh row in a summary
                # with an old one gets all its attempts.
                for row in batch_rows:
                    attempts = row["attempts"] + 1
                    failed = attempts >= MAX_ATTEMPTS
                    if failed:
                        logger.warning(
                            "Notification {} on {} given up after {} attempts",
                            row["id"],
                            channel,
                            attempts,
                        )
                    await conn.execute(
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
            await conn.execute(
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
