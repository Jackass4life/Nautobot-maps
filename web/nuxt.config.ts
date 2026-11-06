// https://nuxt.com/docs/api/configuration/nuxt-config
// Built to static files (`npm run generate`, #594) that api/ serves from the same
// origin, as the current app serves frontend/: no Node process in production.
export default defineNuxtConfig({
  compatibilityDate: '2025-11-01',
  // Flat layout (plugins/, pages/, types/ at the project root) instead of Nuxt 4's app/.
  srcDir: '.',
  // A single-page app: the API does the work.
  ssr: false,
  app: { buildAssetsDir: '/_nuxt/' },
  // No inline import map in the page (CSP: script-src 'self'); the entry is loaded
  // by its own file name instead.
  experimental: { entryImportMap: false },
  runtimeConfig: {
    public: {
      // The API is this origin; override with NUXT_PUBLIC_API_BASE at build time.
      apiBase: '',
    },
  },
  vite: {
    // `npm run dev`: the API from `uv run uvicorn app.main:app` on :8000.
    server: { proxy: { '/api/': { target: 'http://127.0.0.1:8000', changeOrigin: true } } },
  },
  devtools: { enabled: false },
  telemetry: false,
  typescript: { strict: true, typeCheck: false },
})
