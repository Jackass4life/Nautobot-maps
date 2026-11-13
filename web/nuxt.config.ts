import { fileURLToPath } from "node:url";
import { PAGE_FILES, workspaceRoutes } from "./utils/workspaces";

// The app's pages on Nuxt (#463, #609).  Built to static files (`bun run generate`)
// that api/ serves from the same origin: no Node process in production.
export default defineNuxtConfig({
  compatibilityDate: "2026-10-01",
  // Flat layout (pages/, components/, plugins/, types/ at the project root).
  srcDir: ".",
  modules: [
    "@nuxt/ui",
    "@nuxt/eslint",
    "./modules/static-ui-colors",
    "./modules/csp-shell",
    "shadcn-nuxt",
  ],
  // shadcn-vue's components (#640), next to Nuxt UI's <U…>: <STable>, <SSonner>.
  // Nuxt UI first; components/ui/ holds what `bun run add:component` added.
  shadcn: { prefix: "S", componentDir: "./components/ui" },
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
    // Icons bundled at build time; never fetched from the Iconify API.  As
    // <svg> markup: the default CSS mode adds a <style> per icon (CSP).
    mode: "svg",
    provider: "none",
    fallbackToApi: false,
    clientBundle: { scan: true },
  },
  // No inline import map in the page (CSP: script-src 'self'); the entry
  // is loaded by its own file name instead.
  experimental: { entryImportMap: false },
  vite: {
    // `bun run dev`: the API from api/ on :8000 (`uv run uvicorn app.api:app`), but not
    // /api/docs, which is a page of this app (#483).
    server: {
      proxy: {
        "^/api/(?!docs(?:[/?#]|$))": { target: "http://127.0.0.1:8000", changeOrigin: true },
      },
    },
    build: {
      // Swagger UI (about 1.5 MB since 5.34, the API page only, loaded there by a
      // dynamic import) is the one chunk above Vite's 500 kB warning.
      chunkSizeWarningLimit: 2000,
      // No report of plugins taking a large share of the build's time: it fired
      // in every build (about 5 s of a short build) and read as a warning in
      // every Docker build. Build time is still in CI's logs.
      rolldownOptions: { checks: { bundlerTimings: false } },
    },
  },
  runtimeConfig: {
    public: {
      // The API is this origin (#594); override with NUXT_PUBLIC_API_BASE at build time.
      apiBase: "",
    },
  },
  // The flat layout puts tests/ and the tool configs beside the pages: not the app's
  // types (vitest runs the tests, as with frontend/'s app/ directory).
  typescript: {
    tsConfig: { exclude: ["../tests/**", "../vitest.config.ts", "../eslint.config.mjs"] },
  },
  // The web image's server (`bun run build`, #672): the single page's shell, rendered at
  // build time without inline scripts (modules/csp-shell.ts); server/ answers every
  // page route with it.
  nitro: { prerender: { routes: ["/200.html"] } },
  hooks: {
    // Each role's workspace (#697): the same page files at /customer, /dov, /nwo;
    // the wall is the board's page with meta.view.
    "pages:extend"(pages) {
      for (const route of workspaceRoutes()) {
        pages.push({
          name: route.name,
          path: route.path,
          file: fileURLToPath(
            new URL(
              route.path.endsWith("/:name") ? "pages/tenants/[name].vue" : PAGE_FILES[route.page],
              import.meta.url,
            ),
          ),
          meta: { workspace: true, ...(route.page === "wall" ? { view: "wall" } : {}) },
        });
      }
    },
  },
  devtools: { enabled: false },
  telemetry: false,
});
