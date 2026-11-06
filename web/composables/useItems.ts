import type { components } from '~/types/api'

export type Item = components['schemas']['ItemDto']
export type ItemCreate = components['schemas']['ItemCreateDto']

/** The API's `{error}` message (#592), else the status. */
function errorMessage(error: unknown, status: number): string {
  if (error && typeof error === 'object' && 'error' in error && typeof error.error === 'string') {
    return error.error
  }
  return `Error ${status}`
}

export function useItems() {
  const { $api } = useNuxtApp()

  const list = useAsyncData('items', async () => {
    const { data, error } = await $api.GET('/api/v1/items', { params: { query: { limit: 100 } } })
    if (error) throw createError({ statusCode: 502, statusMessage: 'Could not load items' })
    return data
  }, { default: () => [] as Item[] })

  // Mutations return an error message for the page, or null on success.
  // openapi-fetch returns HTTP errors in `error` but throws on network failures.

  async function create(body: ItemCreate): Promise<string | null> {
    try {
      const { error, response } = await $api.POST('/api/v1/items', { body })
      if (error) return errorMessage(error, response.status)
    }
    catch {
      return 'Could not reach the API'
    }
    await list.refresh()
    return null
  }

  async function remove(itemId: number): Promise<string | null> {
    try {
      const { error, response } = await $api.DELETE('/api/v1/items/{item_id}', {
        params: { path: { item_id: itemId } },
      })
      if (error) return errorMessage(error, response.status)
    }
    catch {
      return 'Could not reach the API'
    }
    await list.refresh()
    return null
  }

  return { items: list.data, pending: list.pending, error: list.error, create, remove }
}
