# Other monitoring sources

By default a device's status comes from Nautobot and, if configured, LibreNMS. **Prometheus Alertmanager** and a **generic webhook** can also mark devices down (#292). Anything that can send an HTTP request can use the generic webhook, for example Zabbix media types, Uptime Kuma or a script.

How a device is matched and counted:
- Like LibreNMS, an alert is matched to a Nautobot device by **name** (the part before the first dot, case-insensitive), or by **primary IP** when the alert names an IP.
- A matched device counts as **down** for as long as the alert fires. It is additive: these sources never mark a device up that Nautobot has down.
- Down since = when the alert started.
- The board, history, notifications and the map treat it like any other down device. On the board its line says who reported it: "alertmanager: Target down".

Both endpoints need an **operator** [API token](authentication.md#api-tokens) and the database:

```sh
docker compose exec nautobot-maps python -m nautobot_maps token create alertmanager --role operator
```

Each answer says what happened:

```json
{"firing": 3, "resolved": 1, "skipped": 0, "matched": 3}
```

`matched` counts the firing alerts that name a known Nautobot device. If it stays 0, the label or name doesn't match.

## Wireless controllers: access points

The wireless controllers know best whether an access point is up (#508). The app reads every access point from any number of **Cisco Catalyst Center** and **HPE Aruba Networking New Central** instances, matches each one to its Nautobot device, and counts the ones the controller reports down:
- on the [status page](status-page.md): "2 of 40 access points down", also at a site that is otherwise fine;
- on the board's data (`down_access_point_count`).

An access point that is down **never raises a site's level or sends a notification**: at 10,000 access points, single ones drop all day, and an alarm for each would bury real outages. Nautobot's own status still counts as it always has.

**Matching.** By **serial** (capitals don't matter), else by **name** (the part before the first dot), against the Nautobot devices. By name only for a device whose role is an access point, and only for a name one access point carries: `ap-01` at two sites matches neither. Which of them are access points is still `ACCESS_POINT_ROLES` ([configuration](configuration.md)). An access point in maintenance, or at a site in maintenance, isn't down.

**Several instances, of either kind.** List them in one YAML file, with read-only accounts, `config/wireless.yml` next to `docker-compose.yml` (the app reads it as `/config/wireless.yml`, #517):

```yaml
# config/wireless.yml
catalyst_center:
  - name: dk                      # a-z, 0-9, _ and -; shown as "Catalyst Center dk"
    url: https://dnac-dk.example.com
    username: maps-ro
    password: "..."
    verify_ssl: true              # true, false, or a path to a CA bundle
  - name: se
    url: https://dnac-se.example.com
    username: maps-ro
    password: "..."
aruba_central:                    # New Central (not classic Central)
  - name: eu                      # shown as "Aruba Central eu"
    region: de1                   # us1, de1, gb1, in1, au1, cn1, ...; or base_url: https://...
    client_id: "..."              # a Central-scoped API client from the GreenLake workspace
    client_secret: "..."
  - name: us
    region: us1
    client_id: "..."
    client_secret: "..."
```

**Every key in the file** (anything else stops startup, naming it):

| Key | Catalyst Center (`catalyst_center`) | Aruba New Central (`aruba_central`) |
|---|---|---|
| `name` | **required**: 1–32 of `a-z`, `0-9`, `_`, `-`, starting with a letter or digit (capitals become small); unique per kind; shown as "Catalyst Center dk" | **required**, the same; shown as "Aruba Central eu" |
| `url` | **required**: `https://` address of the instance | — |
| `username` / `password` | **required**: a read-only user (the Observer role is enough) | — |
| `region` | — | the cluster, e.g. `us1`, `de1`, `gb1`, `in1`, `au1` (`cn1` gets its own domain); **this or `base_url`** |
| `base_url` | — | the API's `https://` address, when it isn't `https://<region>.api.central.arubanetworks.com`; **this or `region`** |
| `client_id` / `client_secret` | — | **required**: a Central-scoped API client from GreenLake |
| `token_url` | — | default `https://sso.common.cloud.hpe.com/as/token.oauth2` (HPE's sign-in, always verified) |
| `verify_ssl` | `true` (default), `false`, or a CA bundle path such as `/config/certs/internal-ca.pem` | the same, for the `base_url` (and a custom `token_url`) |

For New Central, create the API client under **Manage Workspace → Personal API clients** in HPE GreenLake, for the **Central** instance (a workspace-scoped client can't read Central). It needs only to read monitoring. There is no refresh token to keep: the app asks HPE's sign-in service (`https://sso.common.cloud.hpe.com/as/token.oauth2`, or `token_url`) for a new token, which lasts two hours, when the old one is about to expire.

```sh
WIRELESS_CONTROLLERS_FILE=/config/wireless.yml
WIRELESS_SYNC_INTERVAL_SECONDS=300     # how often each one is read (at least 60)
```

`docker-compose.yml` mounts the `config/` folder read-only at `/config`, and it is git-ignored and kept out of the image, so the file's passwords are never committed or built in. In Docker the app runs as user and group **10001**: keep the file yours and let the app read it through its group, then start the app.

```bash
sudo chgrp -R 10001 config && sudo chmod 2750 config
chmod 640 config/wireless.yml
docker compose up -d --force-recreate nautobot-maps
```

A file the app can't read stops it at startup, saying what to run ([config/README.md](../config/README.md)). After editing the file, run the last command again. Without Docker, `WIRELESS_CONTROLLERS_FILE` is just the file's path.

Every URL must be `https` (credentials travel over it; only `localhost` may be `http`, for the demo). HPE's sign-in service is always verified, whatever `verify_ssl` says: that setting is for the Central or Catalyst Center address. A mistake in the file (a missing password or client secret, a name used twice, both or neither of region and base_url, an unknown setting) stops the app at startup with a message that names the instance, and never shows the password or secret.

**How it reads them.** Every instance is read on its own, in bulk, by the background scheduler, never one request per site:
- Catalyst Center: one sign-in (`POST /dna/system/api/v1/auth/token`), then the access points in pages of 500 (`GET /dna/intent/api/v1/network-device?family=Unified AP`): about 20 requests for 10,000 access points. Online is `Reachable`.
- New Central: a token (as above), then `GET /network-monitoring/v1/aps` in pages of 1,000, following its `next` cursor: about 10 requests for 10,000. Online is `ONLINE`.

Up to four instances are read at once. The board is rebuilt only when an access point went down or came back.

**When one can't be reached:**
- The others carry on: a slow or failing instance never holds them up or wipes their access points.
- Its last answer counts for three intervals (at least 15 minutes), then its access points are simply not counted as down.
- The top bar says "Can't reach Catalyst Center dk" in amber (not red: the alarms don't depend on it), or "Can't reach 2 sources" when several fail, and its menu lists each one.

**The same access point in two instances** (moved, or during a migration): the newest answer wins. When two are equally new, up wins, so a stale copy never shows it down.

**Check the setup:**

```sh
docker compose exec nautobot-maps python -m nautobot_maps wireless-check
```

```
Catalyst Center dk (https://dnac-dk.example.com): 4210 access points, 12 down, 4198 match a Nautobot device
  not in Nautobot (serial or name), e.g.: lab-ap99
Catalyst Center se (https://dnac-se.example.com): can't read it: 401 Client Error: Unauthorized
Aruba Central eu (https://de1.api.central.arubanetworks.com): 3870 access points, 4 down, 3870 match a Nautobot device
```

It reads each instance now and exits with status 1 when one fails. Access points that match nothing have a serial or name in the controller that no Nautobot device has. `/metrics` has the same per instance: `nautobot_maps_wireless_access_points{controller,state}` and `nautobot_maps_wireless_access_points_matched{controller}` ([operations](operations.md)).

The fields read are in the data contract: [Catalyst Center](data-contract.md#cisco-catalyst-center), [New Central](data-contract.md#hpe-aruba-networking-new-central).

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
