// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: '2025-11-01',
  // Flat layout (plugins/, pages/, types/ at the project root) instead of Nuxt 4's app/.
  srcDir: '.',
  devtools: { enabled: true },
  runtimeConfig: {
    // Server-side rendering only; empty: use public.apiBase. Override with NUXT_API_BASE_SERVER.
    apiBaseServer: '',
    public: {
      // Override with NUXT_PUBLIC_API_BASE.
      apiBase: 'http://localhost:8000',
    },
  },
  typescript: { strict: true, typeCheck: false },
})
