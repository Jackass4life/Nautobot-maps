# web/

The new frontend (#572): Nuxt 4, talking only to `api/` over HTTP. It is a separate
project from the current frontend (`frontend/`).

## Commands (run from `web/`)

```bash
npm install
npm run generate:api   # ../api/openapi.json -> types/api.d.ts (generated, never edit by hand)
npm run dev            # http://localhost:3000; /api/ proxied to the API on :8000
npm run typecheck
npm run generate       # static files in .output/public, which api/ serves
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

## Built and served (#594)

- A single-page app (`ssr: false`) built with `nuxt generate` to `.output/public`. `api/`
  serves it from the same origin, as the current app serves `frontend/`: page routes answer
  `200.html`, `/_nuxt/` the content-named files (cached a year). The api image builds it
  (`api/Dockerfile`); no Node process in production.
- The CSP allows no inline scripts: `scripts/no-inline-scripts.mjs` (run by `generate`)
  moves Nuxt's `window.__NUXT__` config into a file under `/_nuxt/` and fails the build on
  any other inline script.
- The API is the page's own origin (`NUXT_PUBLIC_API_BASE` empty), so the sign-in
  proxy's cookie comes along and no CORS is needed.
- A new page: add its path to `PAGES` in `api/app/frontend/controller.py`.

## Sign-in (#582, #592)

`useMe()` (`GET /api/v1/me`) gives the user, and `can_write` by the API's own rules; the
page hides add/delete without it (the API refuses them anyway).
