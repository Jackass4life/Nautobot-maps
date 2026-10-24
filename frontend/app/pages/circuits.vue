<!-- Circuit mapping (#385; on Nuxt since #463): circuits without a
     termination at a location, and the site their description's address
     points to, in three groups, worst first. -->
<script setup lang="ts">
import { nautobotCircuitUrl, nautobotLocationUrl, searchWords } from "~/utils/format";
import {
  circuitKind,
  GROUPS,
  matches,
  prepare,
  CIRCUITS_STEP,
  summaryText,
  type CircuitGroup,
  type CircuitMapping,
  type SearchableCircuit,
} from "~/utils/circuits";

useHead({ title: "Circuits · Nautobot Maps" });

const { data: config } = usePageConfig();
const nautobotUrl = computed(() => config.value?.nautobot_url || "");

// 1,000s of circuits: not deeply reactive.
const circuits = shallowRef<SearchableCircuit[]>([]);
const summary = shallowRef<Partial<CircuitMapping["summary"]>>({});
const error = ref("");
const loaded = ref(false);
const query = ref("");
// How many each group shows, and which the operator unfolded.
const limits = ref<Partial<Record<CircuitGroup, number>>>({});
const open = ref(new Set<CircuitGroup>());

const words = computed(() => searchWords(query.value));
const shown = computed(() =>
  words.value.length ? circuits.value.filter((circuit) => matches(circuit, words.value)) : circuits.value,
);
const groups = computed(() =>
  GROUPS.map((group) => {
    const members = shown.value.filter((circuit) => circuit.group === group.id);
    const limit = limits.value[group.id] || CIRCUITS_STEP;
    return { ...group, members, visible: members.slice(0, limit), rest: Math.max(0, members.length - limit) };
  }),
);
// A search unfolds the groups it found something in; a plain list keeps the operator's.
function isOpen(group: CircuitGroup) {
  return words.value.length ? groups.value.some((g) => g.id === group && g.members.length) : open.value.has(group);
}
function onToggle(group: CircuitGroup, event: Event) {
  if (words.value.length) return;
  const next = new Set(open.value);
  if ((event.target as HTMLDetailsElement).open) next.add(group);
  else next.delete(group);
  open.value = next;
}
function showMore(group: CircuitGroup) {
  limits.value = { ...limits.value, [group]: (limits.value[group] || CIRCUITS_STEP) + CIRCUITS_STEP };
}
watch(query, () => {
  limits.value = {};
});
const status = computed(() => {
  if (error.value) return error.value;
  if (!loaded.value) return "Loading…";
  return summaryText(summary.value, words.value.length ? shown.value.length : null);
});

onMounted(async () => {
  try {
    const data = await $fetch<CircuitMapping>("/api/circuit-mapping");
    circuits.value = prepare(data.circuits || []);
    summary.value = data.summary || {};
    loaded.value = true;
  } catch (err) {
    error.value = `Could not load the circuits: ${err instanceof Error ? err.message : String(err)}`;
  }
});
</script>

<template>
  <div>
    <AppBar active="circuits" />
    <main class="mx-auto max-w-6xl space-y-4 px-4 py-6">
      <h1 class="text-2xl font-bold text-highlighted">Circuit mapping</h1>
      <p class="max-w-3xl text-sm text-muted">
        Circuits in Nautobot without a termination at a location, and the site their description's address
        points to. Add the termination in Nautobot and the circuit leaves this list at the next sync.
      </p>

      <div class="flex flex-wrap items-center gap-3">
        <UInput
          id="circuit-search"
          v-model="query"
          type="search"
          icon="i-lucide-search"
          placeholder="Search circuit, provider, address or site"
          aria-label="Search circuits"
          autocomplete="off"
          class="w-full sm:w-96"
        />
        <UButton id="circuit-csv" to="/api/circuit-mapping.csv" external download color="neutral" variant="outline" icon="i-lucide-download">
          Download CSV
        </UButton>
        <p id="circuit-status" class="text-sm text-muted" :class="{ 'text-error': error }" role="status">{{ status }}</p>
      </div>

      <div v-if="loaded" id="circuit-groups" class="space-y-3">
        <!-- Keyed by search or not: a cleared search shows the operator's own folds again. -->
        <details
          v-for="group in groups"
          :key="`${group.id}-${words.length ? 'search' : 'list'}`"
          class="circuit-group group overflow-hidden rounded-lg border border-default"
          :data-group="group.id"
          :open="isOpen(group.id)"
          @toggle="onToggle(group.id, $event)"
        >
          <summary class="flex cursor-pointer list-none items-center gap-2 bg-elevated/50 px-4 py-3 font-semibold text-highlighted">
            <UIcon name="i-lucide-chevron-right" class="size-4 text-muted transition-transform group-open:rotate-90" />
            {{ group.title }}
            <UBadge color="neutral" variant="subtle" size="sm">{{ group.members.length.toLocaleString("en") }}</UBadge>
          </summary>
          <p class="border-t border-default px-4 py-2 text-sm text-muted">{{ group.hint }}</p>
          <p v-if="!group.members.length" class="px-4 pb-3 text-sm text-muted">None.</p>
          <ul v-else class="divide-y divide-default border-t border-default">
            <li v-for="circuit in group.visible" :key="circuit.id" class="circuit-row flex flex-wrap gap-x-6 gap-y-1 px-4 py-3">
              <div class="min-w-0 flex-1 basis-80">
                <div>
                  <strong>
                    <a
                      v-if="nautobotUrl && circuit.id"
                      class="circuit-link text-primary hover:underline"
                      :href="nautobotCircuitUrl(nautobotUrl, circuit.id)"
                      target="_blank"
                      rel="noopener noreferrer"
                    >{{ circuit.cid }}</a>
                    <template v-else>{{ circuit.cid }}</template>
                  </strong>
                  <span v-if="circuit.status" class="text-muted"> · {{ circuit.status }}</span>
                </div>
                <div v-if="circuitKind(circuit)" class="text-sm text-muted">{{ circuitKind(circuit) }}</div>
                <div class="text-sm text-muted">{{ circuit.description || "No description" }}</div>
              </div>
              <div class="circuit-sites text-sm">
                <template v-if="circuit.sites.length">
                  <template v-for="(site, index) in circuit.sites" :key="site.id">
                    <span v-if="index">, </span>
                    <!-- On the map: its panel; off the map (no coordinates): Nautobot. -->
                    <a v-if="site.on_map" class="circuit-site text-primary hover:underline" :href="`/?location_id=${encodeURIComponent(site.id)}`">{{ site.name }}</a>
                    <a
                      v-else-if="nautobotUrl"
                      class="circuit-site text-primary hover:underline"
                      :href="nautobotLocationUrl(nautobotUrl, site.id)"
                      target="_blank"
                      rel="noopener noreferrer"
                      title="Not on the map: no coordinates"
                    >{{ site.name }}</a>
                    <template v-else>{{ site.name }}</template>
                  </template>
                </template>
                <span v-else class="text-muted">—</span>
              </div>
            </li>
          </ul>
          <p v-if="group.rest" class="border-t border-default px-4 py-2 text-sm text-muted">
            {{ group.rest.toLocaleString("en") }} more
            <UButton class="circuit-more ml-2" size="xs" color="neutral" variant="outline" @click="showMore(group.id)">
              Show {{ Math.min(CIRCUITS_STEP, group.rest) }} more
            </UButton>
          </p>
        </details>
      </div>
    </main>
  </div>
</template>
