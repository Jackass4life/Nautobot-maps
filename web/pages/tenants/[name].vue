<!-- A tenant's status in the app, by its name (#695): for those signed in with
     tenant.status.view (customer, viewer, NWO, admin), from
     GET /api/v1/tenants/{name}/status: no link or key involved. -->
<script setup lang="ts">
import type { TenantStatus } from "~/utils/status";

const route = useRoute();
const name = computed(() => String(route.params.name || "").trim());

async function load(): Promise<TenantStatus> {
  return (await unwrap(
    useNuxtApp().$api.GET("/api/v1/tenants/{tenant}/status", {
      params: { path: { tenant: name.value } },
      cache: "no-store",
    }),
  )) as TenantStatus;
}
</script>

<template>
  <div>
    <AppBar active="availability" />
    <!-- Another tenant's name in the address: a new view. -->
    <TenantStatusView :key="name" :load="load" :fallback-title="name" />
  </div>
</template>
