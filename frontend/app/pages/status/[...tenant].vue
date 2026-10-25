<!-- A tenant's status page (#291; on Nuxt since #463): its sites and their
     state, for a customer.  Public: the data comes from /api/status/, with
     the key from a share link's "#key=". -->
<script setup lang="ts">
import { FetchError } from "ofetch";
import { formatFeedTime } from "~/utils/format";
import { errorText, headline, splitSites, statusKey, type TenantStatus } from "~/utils/status";

const REFRESH_MS = 60 * 1000;
const route = useRoute();
const tenant = computed(() => {
  const value = route.params.tenant;
  return (Array.isArray(value) ? value.join("/") : String(value || "")).trim();
});

const data = shallowRef<TenantStatus | null>(null);
const error = ref("");
const now = ref(new Date());
// Whether the viewer opened the operational fold: kept across the refresh.
const operationalOpen = ref(false);

const shown = computed(() => (data.value ? splitSites(data.value.sites) : { attention: [], operational: [] }));
const title = computed(() => data.value?.tenant || tenant.value);

useHead({
  title: () => `${title.value} · Status`,
  meta: [{ name: "referrer", content: "no-referrer" }],
});

// Load the status; whether to try again later (not for a refused link).
async function refresh(): Promise<boolean> {
  const key = statusKey(window.location.hash);
  try {
    data.value = await $fetch<TenantStatus>(`/api/status/${encodeURIComponent(tenant.value)}`, {
      headers: key ? { Authorization: `Bearer ${key}` } : {},
      cache: "no-store",
    });
    error.value = "";
    now.value = new Date();
    return true;
  } catch (err) {
    const code = err instanceof FetchError ? (err.statusCode ?? 0) : 0;
    error.value = errorText(code);
    data.value = null;
    return code === 0 || code >= 500;
  }
}

let timer: ReturnType<typeof setTimeout> | undefined;
async function tick() {
  if (await refresh()) timer = setTimeout(tick, REFRESH_MS);
}
onMounted(tick);
onBeforeUnmount(() => clearTimeout(timer));

const DOT: Record<string, string> = {
  outage: "bg-error",
  degraded: "bg-warning",
  maintenance: "bg-info",
  operational: "bg-success",
};
</script>

<template>
  <main class="mx-auto max-w-2xl px-4 py-10">
    <p class="text-xs font-semibold uppercase tracking-wider text-muted">Service status</p>
    <!-- The tenant's description after its name, as its (i) elsewhere (#482). -->
    <h1 id="status-tenant" class="mt-1 text-3xl font-bold text-highlighted">
      {{ title }}<span v-if="data?.description" id="status-description" class="text-xl font-normal text-muted"> – {{ data.description }}</span>
    </h1>
    <p
      id="status-headline"
      role="status"
      aria-live="polite"
      class="mt-2 text-lg font-semibold"
      :class="error ? 'text-error' : data?.summary.affected ? 'text-error' : data ? 'text-success' : 'text-muted'"
    >
      {{ error || (data ? headline(data) : "Loading…") }}
    </p>

    <template v-if="data">
      <UCard v-if="shown.attention.length" class="mt-6" :ui="{ body: 'p-0 sm:p-0' }">
        <ul id="status-sites" class="divide-y divide-default">
          <StatusSiteRow v-for="site in shown.attention" :key="site.name" :site="site" :now="now" :dot="DOT" />
        </ul>
      </UCard>

      <!-- A large tenant's operational sites, folded into one line (#407). -->
      <details
        v-if="shown.operational.length"
        class="status-fold group mt-4 rounded-lg border border-default"
        :open="operationalOpen"
        @toggle="operationalOpen = ($event.target as HTMLDetailsElement).open"
      >
        <summary class="flex cursor-pointer list-none items-center gap-2 px-4 py-3 font-medium text-toned">
          <UIcon name="i-lucide-chevron-right" class="size-4 text-muted transition-transform group-open:rotate-90" />
          {{ shown.operational.length.toLocaleString("en") }} site{{ shown.operational.length === 1 ? "" : "s" }}
          operational
        </summary>
        <ul class="divide-y divide-default border-t border-default">
          <StatusSiteRow v-for="site in shown.operational" :key="site.name" :site="site" :now="now" :dot="DOT" />
        </ul>
      </details>

      <p id="status-updated" class="mt-4 text-sm text-muted">Updated {{ formatFeedTime(data.checked_at, now) }}</p>
    </template>
  </main>
</template>
