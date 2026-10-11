# Other monitoring sources

By default a device's status comes from Nautobot and, if configured, LibreNMS. **Prometheus Alertmanager** and a **generic webhook** can also mark devices down (#292). Anything that can send an HTTP request can use the generic webhook, for example Zabbix media types, Uptime Kuma or a script.

How a device is matched and counted:
- Like LibreNMS, an alert is matched to a Nautobot device by **name** (the part before the first dot, case-insensitive), or by **primary IP** when the alert names an IP.
- A matched device counts as **down** for as long as the alert fires. It is additive: these sources never mark a device up that Nautobot has down.
- Down since = when the alert started.
- The board, history, notifications and the map treat it like any other down device. In device history its source is `alertmanager` or the webhook's `source`.

Both endpoints need an **operator** [API token](authentication.md#api-tokens) and the database:

```sh
docker compose exec nautobot-maps python -m nautobot_maps token create alertmanager --role operator
```

Each answer says what happened:

```json
{"firing": 3, "resolved": 1, "skipped": 0, "matched": 3}
```

`matched` counts the firing alerts that name a known Nautobot device. If it stays 0, the label or name doesn't match.

## Prometheus Alertmanager

Send the alerts that mean "this device is down" to the app. A route keeps other alerts out:

```yaml
route:
  routes:
    - matchers: ['alertname=~"InstanceDown|HostDown|DeviceDown"']
      receiver: nautobot-maps
      continue: true
      repeat_interval: 4h      # below WEBHOOK_ALERT_TTL_HOURS (24)
receivers:
  - name: nautobot-maps
    webhook_configs:
      - url: https://maps.example.com/api/alertmanager
        send_resolved: true
        http_config:
          authorization:
            credentials: nmt_...
```

Which label names the device:
- The device is taken from the first label in `ALERTMANAGER_DEVICE_LABELS` that the alert has (default `instance,hostname,host,device`).
- A port (`sw1:9100`, `[2001:db8::1]:9100`) or a URL (`https://sw1/metrics`, blackbox targets) is stripped.
- If your `instance` label is an exporter address rather than the device, add a label such as `device` to the alert rule and put it first: `ALERTMANAGER_DEVICE_LABELS=device,instance`.

How the app treats each alert:
- `ALERTMANAGER_ALERTNAMES` (comma-separated) is a second filter in the app; empty accepts everything Alertmanager sends.
- A firing alert stays until Alertmanager sends it as resolved (`send_resolved: true`).
- If it isn't repeated for `WEBHOOK_ALERT_TTL_HOURS`, it stops counting, in case a "resolved" was lost.
- Alerts without a device label are skipped (`skipped` in the answer).

## Generic webhook

`POST /api/device-status` with a JSON object, or a list of up to 1000:

```json
{"device": "sw1.example.com", "status": "down", "source": "zabbix", "id": "problem-123", "summary": "ICMP unreachable", "started_at": "2026-10-09T11:58:00Z"}
```

| Field | Required | Meaning |
|---|---|---|
| `device` | yes | Hostname or IP of the device |
| `status` | yes | `down` or `up` |
| `source` | no | Your tool's name, lower case (default `webhook`) |
| `id` | no | The alert's ID in your tool. An `up` with the same `source` and `id` ends it. Default: the device, so one `down` and one `up` per device |
| `summary` | no | Short text, stored with the alert |
| `started_at` | no | When it went down (ISO 8601; default now) |

As with Alertmanager, a `down` that isn't repeated within `WEBHOOK_ALERT_TTL_HOURS` stops counting.

```sh
curl -X POST https://maps.example.com/api/device-status \
  -H "Authorization: Bearer nmt_..." -H "Content-Type: application/json" \
  -d '{"device": "sw1", "status": "down", "source": "uptime-kuma"}'
```

## Zabbix

There is no built-in Zabbix API client yet. Use the generic webhook from a Zabbix **webhook media type**:
- trigger problem → `"status": "down"`;
- recovery → `"status": "up"`;
- `{HOST.HOST}` as `device`, `{EVENT.ID}` as `id`.
