import type { components } from '~/types/api'

export type Me = components['schemas']['MeDto']

/** Who the API sees (#582). `can_write` comes from the API's own rules (#592), so the
 *  page hides only what the API would refuse anyway. */
export function useMe() {
  const { $api } = useNuxtApp()
  const { data: me } = useAsyncData('me', async () => {
    const { data } = await $api.GET('/api/v1/me')
    return data ?? null
  })
  const canWrite = computed(() => !!me.value?.can_write)
  return { me, canWrite }
}
