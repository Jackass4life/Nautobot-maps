import createClient from 'openapi-fetch'
import type { paths } from '~/types/api'
import { identityHeaders } from '~/utils/forward'

export default defineNuxtPlugin(() => {
  const config = useRuntimeConfig()
  // Server-side rendering may reach the API at an internal address (e.g.
  // http://api:8000 in compose) that the browser can't (#577).
  const baseUrl = import.meta.server && config.apiBaseServer ? config.apiBaseServer : config.public.apiBase
  // Server-side rendering calls the API for the user (#582): pass on the sign-in
  // proxy's identity headers, but only from a request that carries its secret.
  // Server only: the browser has no private runtime config.
  let headers: Record<string, string> = {}
  if (import.meta.server) {
    const names = config.authForwardHeaders.split(',').map(h => h.trim().toLowerCase()).filter(Boolean)
    headers = identityHeaders(useRequestHeaders([...names, 'x-auth-proxy-secret']), names, config.authProxySecret)
  }
  // The browser sends the sign-in proxy's session cookie to the API too, also
  // when the API is on another origin (it allows credentials from CORS_ORIGINS).
  const api = createClient<paths>({ baseUrl, headers, credentials: 'include' })
  return { provide: { api } }
})
