# deploy/next

Runs the new stack (`api/`, `web/`, #572) with PostgreSQL. It is separate from the
current app's `docker-compose.yml` and the bundled proxy in `deploy/proxy/`.

```bash
cd deploy/next
cp .env.example .env          # set POSTGRES_PASSWORD
docker compose up -d --build --wait
```

- http://localhost:8000: the pages, and the API under `/api/v1` (`/api/docs`,
  `/api/openapi.json`). One container: the api image builds `web/` and serves its pages
  from the same origin, as the current app does (#594).
- The api container brings the database to the newest schema on start, adopting one the
  current app migrated (`python -m app.migrate`; `MIGRATE_ON_START=false` skips it).
- `POSTGRES_PASSWORD` may contain any character: the api gets it as `DATABASE_PASSWORD`,
  not inside `DATABASE_URL`.
- Non-root, with a healthcheck on `/healthz` (the database must answer).
- Every setting of the current app is passed through, with its defaults
  ([docs/configuration.md](../../docs/configuration.md)).
- Sign-in: `AUTH_MODE=disabled` by default (reads and the board's own changes open, see
  `api/README.md`). The bundled nginx/oauth2-proxy isn't wired to this stack yet, so don't
  expose it beyond a test network.
