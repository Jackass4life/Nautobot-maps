import type { components } from '~/types/api'

export type Item = components['schemas']['ItemDto']
export type ItemCreate = components['schemas']['ItemCreateDto']

export function useItems() {
  const { $api } = useNuxtApp()

  const list = useAsyncData('items', async () => {
    const { data, error } = await $api.GET('/api/v1/items', { params: { query: { limit: 100 } } })
    if (error) throw createError({ statusCode: 502, statusMessage: 'Could not load items' })
    return data
  }, { default: () => [] as Item[] })

  async function create(body: ItemCreate): Promise<string | null> {
    const { error, response } = await $api.POST('/api/v1/items', { body })
    if (error) return 'detail' in error && typeof error.detail === 'string' ? error.detail : `Error ${response.status}`
    await list.refresh()
    return null
  }

  async function remove(itemId: number): Promise<void> {
    await $api.DELETE('/api/v1/items/{item_id}', { params: { path: { item_id: itemId } } })
    await list.refresh()
  }

  return { items: list.data, pending: list.pending, error: list.error, create, remove }
}
