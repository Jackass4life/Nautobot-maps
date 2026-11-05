// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: '2025-11-01',
  // Flat layout (plugins/, pages/, types/ at the project root) instead of Nuxt 4's app/.
  srcDir: '.',
  devtools: { enabled: true },
  runtimeConfig: {
    // Server-side rendering only; empty: use public.apiBase. Override with NUXT_API_BASE_SERVER.
    apiBaseServer: '',
    // Request headers server-side rendering passes on to the API (#582): the sign-in
    // proxy's identity headers. Match the API's AUTH_HEADER_USER / AUTH_HEADER_GROUPS.
    // Override with NUXT_AUTH_FORWARD_HEADERS (CSV).
    authForwardHeaders: 'x-forwarded-user,x-forwarded-groups,x-auth-proxy-secret',
    public: {
      // Override with NUXT_PUBLIC_API_BASE.
      apiBase: 'http://localhost:8000',
    },
  },
  typescript: { strict: true, typeCheck: false },
})
