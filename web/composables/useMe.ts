import type { components } from '~/types/api'

export type Me = components['schemas']['MeDto']
type Role = NonNullable<Me['role']>

const ROLE_LEVELS: Record<Role, number> = { viewer: 1, operator: 2, admin: 3 }

export function hasRole(me: Me | null | undefined, role: Role): boolean {
  return !!me?.role && ROLE_LEVELS[me.role] >= ROLE_LEVELS[role]
}

/** Who the API sees (#582). Only hides what the API would refuse anyway. */
export function useMe() {
  const { $api } = useNuxtApp()
  const { data: me } = useAsyncData('me', async () => {
    const { data } = await $api.GET('/api/v1/me')
    return data ?? null
  })
  const canWrite = computed(() => hasRole(me.value, 'operator'))
  return { me, canWrite }
}
