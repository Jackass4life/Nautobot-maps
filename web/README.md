# web/

The new frontend (#572): Nuxt 4, talking only to `api/` over HTTP. It is a separate
project from the current frontend (`frontend/`).

## Commands (run from `web/`)

```bash
npm install
npm run generate:api   # ../api/openapi.json -> types/api.d.ts (generated, never edit by hand)
npm run dev            # http://localhost:3000; API base: NUXT_PUBLIC_API_BASE (default http://localhost:8000)
npm run typecheck
npm run build
```

## Calling the API

`plugins/api.ts` provides a typed [openapi-fetch](https://openapi-ts.dev/openapi-fetch/)
client as `$api`, built from the generated `paths`. Paths, parameters, bodies and
responses are all type-checked:

```ts
const { $api } = useNuxtApp()
const { data, error } = await $api.GET('/api/v1/items/{item_id}', {
  params: { path: { item_id: 1 } },
})
```

Components and composables call the API only through `$api`: no raw `fetch`/`$fetch`.
See `composables/useItems.ts` and `pages/index.vue` for the example resource.

The layout is flat (`srcDir: '.'`): `pages/`, `plugins/`, `composables/`, `types/`
sit in `web/`, not in Nuxt 4's default `app/`.

## API address and container

- `NUXT_PUBLIC_API_BASE`: where the browser calls the API (default `http://localhost:8000`).
- `NUXT_API_BASE_SERVER`: where server-side rendering calls it, when that differs (e.g.
  `http://api:8000` inside compose); empty means the public address. It is never sent to the browser.
- `web/Dockerfile` (#577) builds the Nuxt server (non-root, healthcheck on `/healthz`);
  `deploy/next/compose.yml` runs it with `api/` and PostgreSQL.

## Sign-in (#582)

- `useMe()` (`GET /api/v1/me`) gives the user and role; the page hides add/delete below
  operator (the API refuses them anyway).
- Server-side rendering forwards the sign-in proxy's identity headers to the API:
  `NUXT_AUTH_FORWARD_HEADERS` (default `x-forwarded-user,x-forwarded-groups,x-auth-proxy-secret`;
  match the API's `AUTH_HEADER_*`). The API only believes them from `AUTH_TRUSTED_PROXIES`.
