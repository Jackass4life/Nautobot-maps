import createClient from 'openapi-fetch'
import type { paths } from '~/types/api'

export default defineNuxtPlugin(() => {
  const config = useRuntimeConfig()
  // Server-side rendering may reach the API at an internal address (e.g.
  // http://api:8000 in compose) that the browser can't (#577).
  const baseUrl = import.meta.server && config.apiBaseServer ? config.apiBaseServer : config.public.apiBase
  // Server-side rendering calls the API for the user (#582): pass on the
  // identity headers the sign-in proxy set on the page request. The API only
  // believes them from an AUTH_TRUSTED_PROXIES address (web's, then).
  const headers = import.meta.server
    ? useRequestHeaders(config.authForwardHeaders.split(',').map(h => h.trim().toLowerCase()).filter(Boolean))
    : {}
  const api = createClient<paths>({ baseUrl, headers })
  return { provide: { api } }
})
