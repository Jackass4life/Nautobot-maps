# Notifications

Nautobot Maps can tell people when a site's alert level changes, so an outage is noticed without someone watching the board. Messages go to a **webhook**, **Microsoft Teams** and/or **email**.

## When a message is sent

- A site **reaches** `NOTIFY_MIN_LEVEL` or gets worse above it: "🔴 London HQ is Critical".
- A site **drops below** it again: "✅ London HQ recovered (now OK)" (or "now Medium" if it is still in alarm).

`NOTIFY_MIN_LEVEL` is `critical` by default; `medium` or `low` send more. A site going into a maintenance window sends nothing; if it is still in alarm when the window ends, that is sent then. A site's first level after it appears is not a change. With `NOTIFY_TENANTS` set, only sites with one of those tenants are notified.

When more than `NOTIFY_SUMMARY_THRESHOLD` (default 5) messages are due at once on a channel, for example during a wide outage, they go out as **summaries** ("8 sites in alarm, 1 recovered") instead of one per site, at most 25 sites per message so it fits Teams and mail size limits.

Each message has the site, its path, address and tenants, the level before and after, the reason, the down devices (name, IP, role, down since, case) and a link to the board (`NOTIFY_BOARD_URL`, the address people open the app on).

## Channels

A channel is on when its setting is filled in; any combination works. After changing them, restart the app (`docker compose up -d`).

### Webhook (JSON)

```dotenv
NOTIFY_WEBHOOK_URL=https://example.com/hooks/nautobot-maps
NOTIFY_WEBHOOK_SECRET=<long random string>   # optional
```

A `POST` with a JSON body: one event (`"type": "alarm"` or `"recovery"`) or a summary (`"type": "summary", "events": [...]`). Every event has an `id` that stays the same on every channel and every resend: use it to ignore duplicates (see *Reliability*). With a secret, the `X-Nautobot-Maps-Signature` header is `sha256=` followed by the HMAC-SHA256 of the raw body with the secret, so the receiver can check the message came from you. Use it for your ITSM system, PagerDuty or Opsgenie (via their webhook integrations) or your own scripts.

### Microsoft Teams

```dotenv
NOTIFY_TEAMS_WEBHOOK_URL=https://...
```

Messages are Adaptive Cards for a Teams **Workflows** webhook (Microsoft is retiring the old "Incoming Webhook" connectors). To create one: in the Teams channel, **⋯ → Workflows → "Post to a channel when a webhook request is received"**, follow the steps, and copy the URL it shows.

### Email

```dotenv
NOTIFY_EMAIL_TO=noc@example.com, oncall@example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=nautobot-maps
SMTP_PASSWORD=...
SMTP_FROM=nautobot-maps@example.com
SMTP_STARTTLS=true
```

Plain text and HTML. Without `SMTP_USERNAME` it sends without logging in (an internal relay). The sender is `SMTP_FROM`, else `SMTP_USERNAME`, else `nautobot-maps@localhost`.

## Test

`POST /api/notifications/test` (admin role; with `AUTH_MODE=disabled` only when `ALLOW_UNAUTHENTICATED_WRITES=true`; in the API explorer at `/docs`) sends a test message on every configured channel and returns `{"results": {"webhook": "ok", "email": "error: ..."}}`.

## Reliability

- A level change and its messages are saved in the same database transaction (`notification_outbox`), so neither gets lost without the other.
- The background scheduler sends what is due within about 30 seconds, before and after each sync, so a slow sync doesn't hold messages up. Only one app process sends at a time, and two senders never take the same message. It needs the scheduler: with `BACKGROUND_SYNC_ENABLED=false` nothing is sent.
- Delivery is **at least once**: if the receiver accepted a message and the app stopped before saving that, the message is sent again. Webhook receivers can drop duplicates by the event `id`.
- A failed message is retried after 1, 2, 5, 10, 15 and then every 30 minutes, and given up after 10 attempts (each message counts its own attempts, also inside a summary). Failures are logged without URLs or passwords.
- `/metrics` shows `nautobot_maps_notifications_pending` and `nautobot_maps_notifications_failed` per channel; a growing pending count or any failure means a channel is broken. Sent and failed messages are kept 30 days.
