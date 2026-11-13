<!-- A tenant's status page at its random link /status/<id> (#500), for a
     customer.  Public: the data comes from /api/v1/status/, with the key from a
     share link's "#key=". The view is TenantStatusView (#695). -->
<script setup lang="ts">
import { statusKey, type TenantStatus } from "~/utils/status";
import TenantStatusView from "~/components/TenantStatusView.vue";

const route = useRoute();
// The tenant's random link id (#500), not its name.
const linkId = computed(() => String(route.params.id || "").trim());

useHead({ meta: [{ name: "referrer", content: "no-referrer" }] });

async function load(): Promise<TenantStatus> {
  const key = statusKey(window.location.hash);
  return (await unwrap(
    useNuxtApp().$api.GET("/api/v1/status/{link_id}", {
      params: { path: { link_id: linkId.value } },
      headers: key ? { Authorization: `Bearer ${key}` } : {},
      cache: "no-store",
    }),
  )) as TenantStatus;
}

// A new link pasted into the open tab changes only the "#key=": the browser
// doesn't reload, so load again with the new key (#527).
const view = useTemplateRef<InstanceType<typeof TenantStatusView>>("view");
const reload = () => view.value?.reload();
onMounted(() => window.addEventListener("hashchange", reload));
onBeforeUnmount(() => window.removeEventListener("hashchange", reload));
</script>

<template>
  <TenantStatusView ref="view" :load="load" />
</template>
