import createClient from 'openapi-fetch'
import type { paths } from '~/types/api'

export default defineNuxtPlugin(() => {
  const config = useRuntimeConfig()
  // Server-side rendering may reach the API at an internal address (e.g.
  // http://api:8000 in compose) that the browser can't (#577).
  const baseUrl = import.meta.server && config.apiBaseServer ? config.apiBaseServer : config.public.apiBase
  const api = createClient<paths>({ baseUrl })
  return { provide: { api } }
})
