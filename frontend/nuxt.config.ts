// The app's pages on Nuxt (#463).  Built to static files (`npm run generate`)
// that FastAPI serves from the same origin: no Node process in production.
export default defineNuxtConfig({
  compatibilityDate: "2026-10-01",
  modules: ["@nuxt/ui", "@nuxt/eslint", "./modules/static-ui-colors"],
  // A single-page app: the API does the work, as the old pages' scripts did.
  ssr: false,
  css: ["~/assets/css/main.css"],
  app: {
    buildAssetsDir: "/_nuxt/",
    head: {
      htmlAttrs: { lang: "en" },
      meta: [{ name: "viewport", content: "width=device-width, initial-scale=1" }],
    },
  },
  ui: {
    // The theme follows the app's own choice (plugins/theme.client.ts): the
    // colour-mode module would put an inline script in the page (CSP).
    colorMode: false,
    // Offline-safe: no web fonts fetched from Google.
    fonts: false,
  },
  icon: {
    // Icons bundled at build time; never fetched from the Iconify API.
    provider: "none",
    fallbackToApi: false,
    clientBundle: { scan: true },
  },
  // `npm run dev`: the API from the app on :5055 (python app.py).
  nitro: {
    devProxy: {
      "/api": { target: "http://127.0.0.1:5055/api", changeOrigin: true },
    },
  },
  // No inline import map in the page (CSP: script-src 'self'); the entry
  // is loaded by its own file name instead.
  experimental: { entryImportMap: false },
  devtools: { enabled: false },
  telemetry: false,
});
