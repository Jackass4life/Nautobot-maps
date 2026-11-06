# deploy/next

Runs the new stack (`api/`, `web/`, #572) with PostgreSQL. It is separate from the
current app's `docker-compose.yml` and the bundled proxy in `deploy/proxy/`.

```bash
cd deploy/next
cp .env.example .env          # set POSTGRES_PASSWORD
docker compose up -d --build --wait
```

- web: http://localhost:3000, api: http://localhost:8000 (`/api/docs`, `/api/openapi.json`).
- The api container applies migrations on start (`MIGRATE_ON_START=false` skips that).
- `POSTGRES_PASSWORD` may contain any character: the api gets it as `DATABASE_PASSWORD`,
  not inside `DATABASE_URL`.
- Both images run as non-root and have healthchecks (`/api/healthz`, web `/healthz`).
- `API_PUBLIC_URL` is what the browser calls; server-side rendering in web uses
  `http://api:8000` inside the compose network (`NUXT_API_BASE_SERVER`).
- Not behind a sign-in proxy yet: don't expose it beyond a test network.
