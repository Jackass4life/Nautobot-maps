# web/

The app's pages (#463, moved here from `frontend/` in #609): Nuxt 4 with Nuxt UI, built to
static files that `api/` serves from the same origin. No Node process in production.

## Commands (run from `web/`)

```bash
npm ci
npm run generate:api   # ../api/openapi.json -> types/api.d.ts (generated, never edit by hand)
npm run dev            # http://localhost:3000; /api/ proxied to the API on :8000
npm run lint
npm run typecheck
npm test               # vitest: the logic in utils/ (tests/*.test.ts)
npm run generate       # static files in .output/public, which api/ serves
```

## Pages

The map (`/`), the alert board and its wall view (`/alerts`, `/alerts?view=wall`),
availability, circuits, maintenance, admin, a tenant's status page (`/status/<id>`), the help
pages (`/help`) and the API page (`/api/docs`, Swagger UI bundled). `api/` answers each page's
path with the build's `200.html` and `/_nuxt/` with its files.

## Calling the API

Only through the typed client: `plugins/api.ts` provides an
[openapi-fetch](https://openapi-ts.dev/openapi-fetch/) client as `$api`, built from the
generated `paths`, so paths, parameters, bodies and answers are type-checked:

```ts
const report = await unwrap(useNuxtApp().$api.GET("/api/v1/availability", { params: { query } }));
```

`unwrap()` (`utils/api.ts`) gives the data, or throws an error with the API's body and status
(`err.data.error`, `err.statusCode`), which `errorMessage()` reads. `queryOf()` turns a page's
query string into the client's query object. No raw `fetch` or `$fetch`.

## Layout and rules

- Flat (`srcDir: '.'`): `pages/`, `components/`, `composables/`, `utils/`, `plugins/`,
  `types/` sit in `web/`. `tests/` and the tool configs are outside the app's types.
- The CSP allows no inline scripts or styles: `scripts/no-inline-scripts.mjs` (run by
  `generate`) moves Nuxt's `window.__NUXT__` config into a file under `/_nuxt/` and fails the
  build on any other inline script; `modules/static-ui-colors` makes Nuxt UI's colours a
  stylesheet instead of an inline `<style>`.
- `package-lock.json`: write it with npm 10 (`npx -y npm@10 install --package-lock-only`), as
  CI runs npm 10.
