<!-- A tenant's status (#291; on Nuxt since #463): its sites and their state.
     Shown at a customer's random link /status/<id> (#500) and, in the app, at
     /tenants/<name> for those with tenant.status.view (#695).  `load` fetches
     one answer; the view refreshes it every minute. -->
<script setup lang="ts">
import { ApiRequestError } from "~/utils/api";
import { formatFeedTime } from "~/utils/format";
import { errorText, headline, splitSites, type TenantStatus } from "~/utils/status";

const props = defineProps<{
  load: () => Promise<TenantStatus>;
  // Shown until the answer names the tenant.
  fallbackTitle?: string;
}>();

const REFRESH_MS = 60 * 1000;

const data = shallowRef<TenantStatus | null>(null);
const error = ref("");
const now = ref(new Date());
// Whether the viewer opened the operational fold: kept across the refresh.
const operationalOpen = ref(false);

const shown = computed(() =>
  data.value ? splitSites(data.value.sites) : { attention: [], operational: [] },
);
const title = computed(() => data.value?.tenant || props.fallbackTitle || "Status");

useHead({
  title: () => (data.value ? `${data.value.tenant} · Status` : "Status"),
});

// Each load's number: only the newest one's answer is shown, and only it
// plans the next (a new key, #527, starts a new one).
let generation = 0;

// Load the status; whether to try again later (not for a refused one).
// Undefined when a newer load has started meanwhile.
async function refresh(mine: number): Promise<boolean | undefined> {
  try {
    const answer = await props.load();
    if (mine !== generation) return undefined;
    data.value = answer;
    error.value = "";
    now.value = new Date();
    return true;
  } catch (err) {
    if (mine !== generation) return undefined;
    // unwrap() throws ApiRequestError with the answer's status (#613: not ofetch's FetchError).
    const code = err instanceof ApiRequestError ? err.statusCode : 0;
    error.value = errorText(code);
    data.value = null;
    return code === 0 || code >= 500;
  }
}

let timer: ReturnType<typeof setTimeout> | undefined;
async function tick() {
  clearTimeout(timer);
  const mine = ++generation;
  if ((await refresh(mine)) && mine === generation) timer = setTimeout(tick, REFRESH_MS);
}
onMounted(tick);
onBeforeUnmount(() => {
  clearTimeout(timer);
  generation++;
});
// The page asks for a new load (a new key, #527; another tenant).
defineExpose({ reload: tick });

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
    <!-- The tenant's description after its name, as its (i) elsewhere (#482).
         The heading is the name alone (what a screen reader announces); the
         line's size is the description's, so a wrapped one keeps its spacing. -->
    <div class="mt-1 text-xl">
      <h1 id="status-tenant" class="inline text-3xl font-bold text-highlighted">{{ title }}</h1
      ><span v-if="data?.description" id="status-description" class="text-muted">
        – {{ data.description }}</span
      >
    </div>
    <p
      id="status-headline"
      role="status"
      aria-live="polite"
      class="mt-2 text-lg font-semibold"
      :class="
        error
          ? 'text-error'
          : data?.summary.affected
            ? 'text-error'
            : data
              ? 'text-success'
              : 'text-muted'
      "
    >
      {{ error || (data ? headline(data) : "Loading…") }}
    </p>

    <template v-if="data">
      <UCard v-if="shown.attention.length" class="mt-6" :ui="{ body: 'p-0 sm:p-0' }">
        <ul id="status-sites" class="divide-y divide-default">
          <StatusSiteRow
            v-for="site in shown.attention"
            :key="site.name"
            :site="site"
            :now="now"
            :dot="DOT"
          />
        </ul>
      </UCard>

      <!-- A large tenant's operational sites, folded into one line (#407). -->
      <details
        v-if="shown.operational.length"
        class="status-fold group mt-4 rounded-lg border border-default"
        :open="operationalOpen"
        @toggle="operationalOpen = ($event.target as HTMLDetailsElement).open"
      >
        <summary
          class="flex cursor-pointer list-none items-center gap-2 px-4 py-3 font-medium text-toned"
        >
          <UIcon
            name="i-lucide-chevron-right"
            class="size-4 text-muted transition-transform group-open:rotate-90"
          />
          {{ shown.operational.length.toLocaleString("en") }} site{{
            shown.operational.length === 1 ? "" : "s"
          }}
          operational
        </summary>
        <ul class="divide-y divide-default border-t border-default">
          <StatusSiteRow
            v-for="site in shown.operational"
            :key="site.name"
            :site="site"
            :now="now"
            :dot="DOT"
          />
        </ul>
      </details>

      <p id="status-updated" class="mt-4 text-sm text-muted"
        >Updated {{ formatFeedTime(data.checked_at, now) }}</p
      >
    </template>
  </main>
</template>
