# Notifications

Nautobot Maps can send a message when a site goes into alarm and when it recovers, so an outage is noticed even when nobody is watching the board. Messages go to **Microsoft Teams**, **email** and/or a **webhook** (for a ticket system, PagerDuty, Opsgenie or your own script).

- **[Using it](#using-it)**: when a message is sent and what it says.
- **[Customers](#customers)**: a mail to the customer about their own sites.
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

## Customers

A customer can get a mail too, about **their own sites only** (#707). Give their addresses when you make their [status link](status-page.md#who-can-see-it): **Share…** → **Notification email** before **Create customer link** (or Admin → **New token**, role `status`). Several addresses are separated by commas, up to 10.

- **When:** the same rules as above (`NOTIFY_MIN_LEVEL`, its tiers, no mail for planned maintenance, summaries in a big outage), for every site that has the token's tenant. `NOTIFY_TENANTS` is for your own channels only and doesn't limit customers.
- **After a wait of their own** (#729): choose **Mail** next to the address (at once, after 5, 10, 15 or 30 minutes, 1 or 2 hours), or `--notify-after 15` on the command line (any whole number of minutes up to 1440). The alarm mail goes out only once the site has been in alarm that long; back up (or in planned maintenance) sooner, and the customer gets nothing, not even a recovery. **At once** is preselected; a wait can be chosen only together with an address, and can't be changed later: revoke the link's token and make a new one. The mail goes out at the first send after the wait (at least every 30 seconds), and the wait counts from when the app saw the change, so it also depends on how often LibreNMS is read ([when exactly](#when-exactly)).
- **Once per outage:** one alarm mail while the site is down, also if it gets worse (Medium, then Critical); the next only after it has recovered. The recovery mail goes out at once, and only to those who got the alarm.
- **What it says:** what their [status page](status-page.md#what-the-customer-sees) shows about the site, and nothing more (#763): the site and its address, its state in the page's words ("🔴 Aarhus HQ: Outage", "✅ Aarhus HQ recovered: Operational") and since when, the site's description, how many network devices and access points it has and how many are down, the customer's circuits there, and, with `STATUS_PAGE_TENANT_DEVICES=true`, their own devices by name ("aar-rtr01: down since …"). Then the time, and a link to their status page (with `NOTIFY_BOARD_URL`; without the key, so they open it with the link they were given). On a site shared by several customers each sees only their own devices and counts. Never your own equipment's names, IPs, roles, cases, reasons or the board. A summary is titled "Your sites: …", one line per site with its counts.
- **To whom:** each address gets its own mail; no customer sees another's address. One address on several tokens gets one mail, after the shortest of their waits.
- **Needs:** `SMTP_HOST` (and the other `SMTP_*` settings as needed); `NOTIFY_EMAIL_TO` isn't needed.
- **Changing it:** the addresses and the wait can't be edited. Revoke the token and make a new link with the new addresses (the customer gets the new link). A revoked or expired token gets no more mail.

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

On the **Admin** page (☰ → Admin, admins only), **Send test** under Notifications sends a test message on every channel and shows each one's result. With no channel set up, the section says "No channel set up" and which settings make one; **Send test** then says what is missing (for example `NOTIFY_EMAIL_TO` without `SMTP_HOST`).

Or through the API: `POST /api/v1/notifications/test` (admin role; with `AUTH_MODE=disabled` only when `ALLOW_UNAUTHENTICATED_WRITES=true`; also on the API page at `/api/docs`) returns `{"results": {"webhook": "ok", "email": "error: ..."}}`.

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
