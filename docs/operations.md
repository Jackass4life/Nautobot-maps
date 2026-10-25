# Running in production

## Run a released version

`docker compose up --build` builds the image from your checkout. In production, run a published release instead, so every host runs exactly what CI built and tested, and a rollback is one line:

```yaml
# docker-compose.override.yml
services:
  nautobot-maps:
    image: ghcr.io/jackass4life/nautobot-maps:0.1.0   # the version to run
    build: !reset null
```

```bash
docker compose pull nautobot-maps && docker compose up -d
```

To roll back, set the previous version and run the same commands. The app refuses to start on a database that a newer release has already migrated; restore the backup taken before the upgrade (see [backup and restore](#backup-and-restore)). Releases are listed on the repository's Releases page and in `CHANGELOG.md`. After upgrading Nautobot or LibreNMS, check the log for "Data contract" warnings or `GET /api/contract`: they name any field that no longer looks as the app expects ([data contract](data-contract.md)).

## Local overrides (ports, volumes, …)

Don't edit `docker-compose.yml` for machine-specific settings: `git pull` would then refuse to update it. Put them in `docker-compose.override.yml` next to it; Docker Compose merges that file automatically, and it is git-ignored.

```yaml
# docker-compose.override.yml
services:
  nautobot-maps:
    # Port 5000 is taken on this machine: publish the app on 9000 instead.
    ports: !override
      - "127.0.0.1:9000:5000"
```

- `!override` replaces the port list instead of adding to it (Docker Compose v2.24.4 or newer).
- Ports are `host:container`: the app inside the container keeps listening on 5000.
- Check the merged result with `docker compose config`.

## What the container does

- **Logs** go to `docker compose logs`: the app's messages (unexpected errors with their stack trace) and an access log line per request with the signed-in user, status and response time in ms (successful `/healthz` probes are left out). The bundled `docker-compose.yml` caps each container's log at 5 × 10 MB. `LOG_FORMAT=json` gives one JSON object per line.
- **Security headers** on every response: a Content-Security-Policy that only runs the app's own scripts and styles (no inline ones: an HTML injection can't restyle the page, #370) and loads images only from the app and the `MAP_TILE_URL` host, plus `nosniff`, `X-Frame-Options: DENY` and a `strict-origin-when-cross-origin` referrer policy.
- **Compression** (#349): text answers over 1 KB (pages, JSON, CSS, JS, CSV) are gzipped for browsers that accept it, with `Vary: Accept-Encoding`. At 1,200 sites the board's data (about 1.3 MB) and the map's (0.8 MB) shrink to about a tenth, which matters with many boards and wall screens refreshing. Streamed exports, partial and not-modified answers, and a new API token's answer are left as they are. The access log's response size is then the compressed size. To compress in your proxy instead, set `COMPRESS_RESPONSES=false` there and, for nginx, turn it on for proxied JSON (by default nginx compresses only HTML, and nothing proxied):
  ```nginx
  gzip on;
  gzip_proxied any;
  gzip_types application/json application/javascript text/css text/csv image/svg+xml;
  gzip_vary on;
  ```
- **Unprivileged user:** the image runs as `app` (uid 10001).
- **Health check:** a Docker `HEALTHCHECK` probes `/healthz`, so `docker ps` shows `healthy`/`unhealthy`. It never calls Nautobot or LibreNMS, so an upstream outage doesn't mark the app unhealthy.
- **Shared cache:** the bundled `docker-compose.yml` runs a `redis` service and sets `CACHE_TYPE=RedisCache`, so the 4 Gunicorn workers share one cache (the board, map levels, geocoding) and the MCP rate limit. Redis holds nothing that isn't rebuilt: it needs no volume or backup. With your own Redis, set `CACHE_REDIS_URL`. `CACHE_TYPE=SimpleCache` gives each worker its own cache again: it works, but each worker rebuilds and limits on its own.
- **Migrations:** before starting the app the container runs `python -m nautobot_maps migrate` (see [database migrations](#database-migrations)).

## Backup and restore

PostgreSQL holds two kinds of data:

- **Rebuilt automatically:** the Nautobot and LibreNMS inventory caches. Losing them costs one full sync.
- **Not rebuilt from anywhere:** alert history (when devices went down and came back), case numbers, criticality overrides and site level changes. **Back these up.**

With the bundled `docker-compose.yml` everything lives in the `postgres_data` volume; `docker compose down -v`, or losing the host, deletes it.

**Back up** (e.g. nightly from cron on the host; the dump is small):

```bash
docker compose exec -T postgres pg_dump -Fc -U nautobot_maps nautobot_maps > nautobot-maps-$(date +%F).dump
```

**Restore** (stop the app first so nothing writes meanwhile):

```bash
docker compose stop nautobot-maps
docker compose exec -T postgres pg_restore --clean --if-exists --no-owner -U nautobot_maps -d nautobot_maps < nautobot-maps-2026-09-29.dump
docker compose start nautobot-maps
```

Use your `POSTGRES_USER` / `POSTGRES_DB` if you changed them.

**Upgrading PostgreSQL to a new major version** (e.g. `postgres:16-alpine` → `postgres:17-alpine`): the new version can't read the old data directory, so dump, recreate, restore:

1. Take a backup as above.
2. `docker compose down`, then `docker volume rm <project>_postgres_data` (see `docker volume ls`).
3. Change the image in `docker-compose.yml` (or `docker-compose.override.yml`).
4. `docker compose up -d postgres`, restore the dump as above, then `docker compose up -d`.

**Growth:** alert history is kept for two years by default (`ALERT_HISTORY_RETENTION_DAYS=730`; `0` keeps it forever). The scheduler prunes once a day: resolved alerts with their events and cases, and site level changes. Open alerts are never pruned.

| What | On disk, with indexes |
|---|---|
| One device incident (down, then up): its alert, two events, and the site's level changes | ≈ 1.2 KB |
| 100 incidents a day | ≈ 45 MB a year |
| 1,000 incidents a day | ≈ 450 MB a year |

These figures were measured on PostgreSQL 17 with 20,000 incidents. Notes, cases and longer reasons add a little. A flapping device is one incident (#286), not one per change.

How to choose:
- **Upgrading from a version that kept everything:** the first daily prune deletes history older than two years. To keep it, set `ALERT_HISTORY_RETENTION_DAYS=0` (or a longer period) before upgrading, or back up first.
- The [availability report](availability.md) refuses periods older than the retention (its calendar doesn't offer those days), so keep at least as long as you report on, e.g. two years for year-on-year.
- Back up before lowering it: pruning deletes for good.

Watch it in `/metrics`:
- `nautobot_maps_table_bytes{table}` and `nautobot_maps_table_rows_estimate{table}`: size per table;
- `nautobot_maps_history_oldest_timestamp_seconds`: how far back history goes, which stops moving back once retention prunes.

## Database migrations

Each schema change is applied once and recorded in `schema_migrations`, so the app's workers start without migrating. `python -m nautobot_maps schema-version` shows the database's version and the one the release expects:

```bash
docker compose exec nautobot-maps python -m nautobot_maps schema-version
```

Rolling back to an older release after its database was migrated is refused at startup ("The database schema is version N, newer than this release…"): run the release the database was migrated with, or restore a backup taken before the upgrade.

## Monitoring

`/healthz` is the liveness probe: 200 while the app answers and, when a database is configured, the database too; 503 when a configured database doesn't answer. Without `NAUTOBOT_MAPS_DATABASE_URL` (a map-only setup) it checks only the app. It also reports `inventory_sync_age_seconds` (seconds since the Nautobot sync last succeeded, `null` if never) without failing on it, since restarting the app can't fix a sync that fails upstream. For people, the top bar on every page says the same in plain words, from `GET /api/app-status` (`state`: ok, stale or down, and why per source; #419).

`/metrics` serves Prometheus metrics, read from the database when scraped (so every worker gives the same answer):

| Metric | Meaning |
|---|---|
| `nautobot_maps_sync_last_success_timestamp_seconds{source}` | When the sync last finished without error |
| `nautobot_maps_sync_last_attempt_timestamp_seconds{source}` | When it last started |
| `nautobot_maps_sync_last_duration_seconds{source}` | How long the last finished sync took |
| `nautobot_maps_sync_failing{source}` / `nautobot_maps_sync_running{source}` | 1 if the last sync failed / while one runs |
| `nautobot_maps_open_alerts{level}` | Open device alerts by level |
| `nautobot_maps_sites{level}` | Sites by level at the last board build |
| `nautobot_maps_database_up` | The database answered this scrape |
| `nautobot_maps_notifications_pending{channel}` / `nautobot_maps_notifications_failed{channel}` | Notifications waiting to be sent / given up ([notifications](notifications.md)) |
| `nautobot_maps_board_builds_total{trigger}` / `nautobot_maps_board_build_seconds_total{trigger}` | Alert board builds and the time they took, by trigger: `scheduler` (after a sync), `push` (LibreNMS or webhook alert), `change` (case, acknowledgement, maintenance), `request` (a page found no stored board) |
| `nautobot_maps_board_build_last_duration_seconds{trigger}` | How long the last build took |
| `nautobot_maps_table_bytes{table}` / `nautobot_maps_table_rows_estimate{table}` | Each table's size on disk (with indexes) and PostgreSQL's row estimate ([growth](#backup-and-restore)) |
| `nautobot_maps_history_oldest_timestamp_seconds` | The oldest site level change kept: how far back history goes |
| `nautobot_maps_sites_coordinate_problems{problem}` | Board sites with missing or wrong coordinates: `invalid`, `zero`, `swapped`, `far`, `missing` ([below](#sites-with-missing-or-wrong-coordinates)) |
| `nautobot_maps_wireless_access_points{controller,state}` / `nautobot_maps_wireless_access_points_matched{controller}` | Access points each [wireless controller](sources.md#wireless-controllers-access-points-catalyst-center) instance listed at its last answer, up or down, and how many match a Nautobot device; each instance's sync is in the `sync` series as `source="wireless:catalyst_center:<name>"` (#508) |
| `nautobot_maps_contract_mismatches{source,endpoint,field}` | Records of the last sync whose field didn't match the [data contract](data-contract.md); 0 series when all match |

`source` is `nautobot_inventory`, `nautobot_inventory_reconcile` (daily full sync) or `librenms_inventory`. Request counts and response times are in the access log. `/metrics` needs no sign-in, even with `AUTH_REQUIRE_VIEWER=true`, since it holds only counts (no site or device names); set `METRICS_ENABLED=false` if even those should stay private.

**The board is built once, not per request:**
- The scheduler builds it after each sync and stores it in the cache. It stays stored a little longer than `CACHE_TTL`, until the next build replaces it.
- A case, acknowledgement or maintenance change rebuilds it in the background.
- Only one build runs at a time across all workers. A page that finds no stored board waits for the running build instead of starting another.

With the shared Redis cache, `nautobot_maps_board_builds_total{trigger="request"}` should hardly grow. If it does, the board expires between syncs: check that the scheduler runs (`BACKGROUND_SYNC_ENABLED`).

An example alert, for a sync interval of 5 minutes:

```yaml
- alert: NautobotMapsInventoryStale
  expr: time() - nautobot_maps_sync_last_success_timestamp_seconds{source="nautobot_inventory"} > 3 * 300
  for: 5m
  annotations:
    summary: "No successful Nautobot inventory sync for 15 minutes; the alert board shows old data"
```

## Sites with missing or wrong coordinates

A site without coordinates is missing from the map, and one with wrong coordinates pulls the map's first view out over the sea (#368). The map shows a line under its count when the alert board has any, e.g. "12 sites aren't on the map · 2 have wrong coordinates · **Check**"; **Check** lists them worst first (the first 50, the rest in the CSV), each with a link to fix it in Nautobot and **Show** for those on the map.

| Problem | Meaning |
|---|---|
| `invalid` | Impossible coordinates: latitude beyond ±90 or longitude beyond ±180 |
| `zero` | At 0,0: the coordinates were never set |
| `swapped` | Far from the other sites in its region, but not with latitude and longitude swapped |
| `missing` | No coordinates: on the board, not on the map |
| `far` | Far from the other sites in its region: a hint to check (a lone site abroad can be right) |

**"Far" is measured within the site's region** (#416), not from the middle of the whole network, so a network on several continents isn't flagged. The region is the closest part of the site's Nautobot location tree ("EMEA › DNK", then "EMEA") with at least 3 sites with coordinates; with none, the whole network. A site is far when it is more than 4 × the region's typical distance from the region's middle (the median point), and at least 1,000 km (2,000 km for the whole network). Each one says what it is compared with: "9,194 km from the other sites in EMEA › DNK". A page for some sites only (`?tenant=`) still compares them with every site in their region.

`far` is only a hint: the line counts the sites that are missing or wrong, and the far ones come last in the list under their own heading ("2 sites are far from the others" only when nothing else is wrong).

Only the board's sites are checked, and one without coordinates (or at 0,0) only when it has devices, in maintenance or down ones included: regions and buildings above the sites have none by design. The middle is a median per axis, so a region across the 180° meridian gets wrong results: read `far` there with care (and set `MAP_DEFAULT_VIEW`, #362). It reads the stored board, so it costs no Nautobot call; the map checks again every 15 minutes. API: `GET /api/locations/coordinate-check` (JSON, with counts) and `/api/locations/coordinate-check.csv`, both taking `tenant=` and `q=` like the map: on a map for some sites only, the line, the list and the CSV cover those sites. The metric is left out until a board is stored (a scrape never builds one).

