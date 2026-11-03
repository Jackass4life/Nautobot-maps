# Notifications

Nautobot Maps can send a message when a site goes into alarm and when it recovers, so an outage is noticed even when nobody is watching the board. Messages go to **Microsoft Teams**, **email** and/or a **webhook** (for a ticket system, PagerDuty, Opsgenie or your own script).

- **[Using it](#using-it)**: when a message is sent and what it says.
- **[Setting it up](#setting-it-up)**: Teams, email, webhook, and a test.
- **[Details](#details)**: the exact rules, the webhook format, and reliability.

## Using it

### When a message is sent

- **A site goes into alarm**, by default when it becomes **Critical**: "🔴 London HQ is Critical".
- **It recovers**: "✅ London HQ recovered (now OK)", or "now Medium" if it is still in alarm.

Nothing is sent for:
- a site going into **planned maintenance** (if it is still in alarm when the maintenance ends, that is sent then);
- a site where only **access points** are down (unless one of them has its own level set in Nautobot, or is marked critical: then it is an ordinary alarm);
- someone **editing** a tier or a criticality field in Nautobot.

### During a big outage

When more than 5 messages are due at once, they are combined into **summaries** ("8 sites in alarm, 1 recovered") instead of one message per site.

### What a message says

The site, where it is, its customers, the level before and after, why, the down devices (name, IP, role, down since, case), and a link to the board.

## Setting it up

Each channel is on when its settings are filled in in `.env`; use any combination. Then restart the app (`docker compose up -d`) and [send a test](#test).

### Microsoft Teams

1. In the Teams channel: **⋯ → Workflows → "Post to a channel when a webhook request is received"**.
2. Follow the steps and copy the URL it shows.
3. Set it:

```dotenv
NOTIFY_TEAMS_WEBHOOK_URL=https://...
```

Messages are Adaptive Cards for a Teams **Workflows** webhook (Microsoft is retiring the old "Incoming Webhook" connectors).

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

### Webhook (JSON)

```dotenv
NOTIFY_WEBHOOK_URL=https://example.com/hooks/nautobot-maps
NOTIFY_WEBHOOK_SECRET=<long random string>   # optional
```

The format: [the webhook's body](#the-webhooks-body).

### Who gets what

| You want… | Setting |
|---|---|
| Messages from Medium (or Low) too, not only Critical | `NOTIFY_MIN_LEVEL=medium` |
| Important sites to notify sooner | `NOTIFY_MIN_LEVEL_BY_TIER=gold:low,silver:medium` (needs an [importance tier](alert-board.md#importance-tier)) |
| Only some customers' sites | `NOTIFY_TENANTS=Acme Corp,Nordic Net` |
| The link in the message to work | `NOTIFY_BOARD_URL=https://maps.example.com` (the address people open the app on) |
| Summaries sooner or later | `NOTIFY_SUMMARY_THRESHOLD` (default 5) |

Every setting: [configuration](configuration.md#notifications).

### Test

On the **Admin** page (☰ → Admin, admins only), **Send test** under Notifications sends a test message on every channel and shows each one's result.

Or through the API: `POST /api/notifications/test` (admin role; with `AUTH_MODE=disabled` only when `ALLOW_UNAUTHENTICATED_WRITES=true`; also on the API page at `/api/docs`) returns `{"results": {"webhook": "ok", "email": "error: ..."}}`.

## Details

### When exactly

- A site **reaches** `NOTIFY_MIN_LEVEL` or gets worse above it; it **drops below** it again. `NOTIFY_MIN_LEVEL` is `critical` by default; `medium` or `low` send more.
- A site's first level after it appears is not a change.
- A site with only access points down, per a [wireless controller](alert-board.md#access-points), counts as OK here. An access point with its own level set in Nautobot, or marked critical in the app, is an ordinary alarm and is notified like any device.
- A change of level while the down devices stay the same (a tier or criticality field edited) is in the activity feed but not notified ([criticality](alert-board.md#device-and-site-criticality)). Changing a down device's role is not covered: it can notify.
- With `NOTIFY_TENANTS` set, only sites with one of those tenants are notified.
- With an [importance tier](alert-board.md#importance-tier), `NOTIFY_MIN_LEVEL_BY_TIER` (e.g. `gold:low,silver:medium`) sets a lower minimum for important sites; messages then say the tier.
- More than `NOTIFY_SUMMARY_THRESHOLD` (default 5) messages due at once on a channel go out as summaries, at most 25 sites per message so it fits Teams and mail size limits.

Each message has the site, its path, address and tenants, the level before and after, the reason, the down devices (name, IP, role, down since, case) and a link to the board (`NOTIFY_BOARD_URL`).

### The webhook's body

A `POST` with a JSON body: one event (`"type": "alarm"` or `"recovery"`) or a summary (`"type": "summary", "events": [...]`). Every event has an `id` that stays the same on every channel and every resend: use it to ignore duplicates (see [reliability](#reliability)). With a secret, the `X-Nautobot-Maps-Signature` header is `sha256=` followed by the HMAC-SHA256 of the raw body with the secret, so the receiver can check the message came from you.

### Reliability

- A level change and its messages are saved in the same database transaction (`notification_outbox`), so neither gets lost without the other.
- The background scheduler sends what is due within about 30 seconds, before and after each sync, so a slow sync doesn't hold messages up. Only one app process sends at a time, and two senders never take the same message. It needs the scheduler: with `BACKGROUND_SYNC_ENABLED=false` nothing is sent.
- Delivery is **at least once**: if the receiver accepted a message and the app stopped before saving that, the message is sent again. Webhook receivers can drop duplicates by the event `id`.
- A failed message is retried after 1, 2, 5, 10, 15 and then every 30 minutes, and given up after 10 attempts (each message counts its own attempts, also inside a summary). Failures are logged without URLs or passwords.
- `/metrics` shows `nautobot_maps_notifications_pending` and `nautobot_maps_notifications_failed` per channel; a growing pending count or any failure means a channel is broken. Sent and failed messages are kept 30 days.
