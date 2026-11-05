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
