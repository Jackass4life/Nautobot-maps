import createClient from 'openapi-fetch'
import type { paths } from '~/types/api'

export default defineNuxtPlugin(() => {
  const config = useRuntimeConfig()
  // The API is this page's origin (#594); the sign-in proxy's cookie comes along.
  const api = createClient<paths>({ baseUrl: config.public.apiBase, credentials: 'same-origin' })
  return { provide: { api } }
})
