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
- **Security headers** on every response: a Content-Security-Policy that only runs the app's own scripts and loads images only from the app and the `MAP_TILE_URL` host, plus `nosniff`, `X-Frame-Options: DENY` and a `strict-origin-when-cross-origin` referrer policy.
- **Unprivileged user:** the image runs as `app` (uid 10001).
- **Health check:** a Docker `HEALTHCHECK` probes `/healthz`, so `docker ps` shows `healthy`/`unhealthy`. It never calls Nautobot or LibreNMS, so an upstream outage doesn't mark the app unhealthy.
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

**Growth:** alert history is kept forever unless you set `ALERT_HISTORY_RETENTION_DAYS` (e.g. `365`); the scheduler then prunes once a day.

## Database migrations

Each schema change is applied once and recorded in `schema_migrations`, so the app's workers start without migrating. `python -m nautobot_maps schema-version` shows the database's version and the one the release expects:

```bash
docker compose exec nautobot-maps python -m nautobot_maps schema-version
```

Rolling back to an older release after its database was migrated is refused at startup ("The database schema is version N, newer than this release…"): run the release the database was migrated with, or restore a backup taken before the upgrade.

## Monitoring

`/healthz` is the liveness probe: 200 while the app answers and, when a database is configured, the database too; 503 when a configured database doesn't answer. Without `NAUTOBOT_MAPS_DATABASE_URL` (a map-only setup) it checks only the app. It also reports `inventory_sync_age_seconds` (seconds since the Nautobot sync last succeeded, `null` if never) without failing on it, since restarting the app can't fix a sync that fails upstream.

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
| `nautobot_maps_contract_mismatches{source,endpoint,field}` | Records of the last sync whose field didn't match the [data contract](data-contract.md); 0 series when all match |

`source` is `nautobot_inventory`, `nautobot_inventory_reconcile` (daily full sync) or `librenms_inventory`. Request counts and response times are in the access log. `/metrics` needs no sign-in, even with `AUTH_REQUIRE_VIEWER=true`, since it holds only counts (no site or device names); set `METRICS_ENABLED=false` if even those should stay private.

An example alert, for a sync interval of 5 minutes:

```yaml
- alert: NautobotMapsInventoryStale
  expr: time() - nautobot_maps_sync_last_success_timestamp_seconds{source="nautobot_inventory"} > 3 * 300
  for: 5m
  annotations:
    summary: "No successful Nautobot inventory sync for 15 minutes; the alert board shows old data"
```
