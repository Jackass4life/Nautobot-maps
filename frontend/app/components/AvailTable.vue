<!-- An availability table (#342): sorted by a column (the worst first),
     searched and filtered by tenant (sites), the first N and "Show more".
     Tenants get the search only when there are more than fit (#481). -->
<script setup lang="ts">
import {
  barPercent,
  FIRST_DIRECTION,
  filterRows,
  formatDowntime,
  moreText,
  sortRows,
  type Row,
  type SortDir,
  type SortKey,
} from "~/utils/availability";
import { CHOICE_SEARCH_FIELDS, choiceItems } from "~/utils/tenants";

const props = defineProps<{
  kind: "tenants" | "sites";
  rows: Row[];
  target: number;
  descriptions: Record<string, string>;
  // Each tenant's status page link id (#500).
  statusLinks: Record<string, string>;
  limit: number;
  title: string;
  help: string;
  tenantChoices?: string[];
}>();

const sort = ref<{ key: SortKey; dir: SortDir }>({ key: "availability", dir: "asc" });
const query = ref("");
const tenants = ref<string[]>([]);
// A tenant is found by its description too (#497).
const tenantItems = computed(() => choiceItems(props.tenantChoices ?? [], props.descriptions));
const shownLimit = ref(props.limit);

const columns = computed<{ key: SortKey | null; label: string; num?: boolean }[]>(() =>
  props.kind === "tenants"
    ? [
        { key: "name", label: "Tenant" },
        { key: "availability", label: "Availability" },
        { key: "sites", label: "Sites", num: true },
        { key: "downtime", label: "Downtime", num: true },
      ]
    : [
        { key: "name", label: "Site" },
        { key: "availability", label: "Availability" },
        { key: "downtime", label: "Downtime", num: true },
        { key: "incidents", label: "Incidents", num: true },
        { key: "devices", label: "Devices", num: true },
        { key: null, label: "Tenants" },
      ],
);
const searchable = computed(() => (props.kind === "sites" ? props.rows.length > 0 : props.rows.length > props.limit));
const sorted = computed(() => sortRows(props.rows, sort.value.key, sort.value.dir));
// A hidden box filters nothing: a new period with fewer tenants hides it (#481).
const view = computed(() =>
  filterRows(sorted.value, { query: searchable.value ? query.value : "", tenants: tenants.value, limit: shownLimit.value }),
);
const more = computed(() => moreText(view.value, props.rows.length));

// A new search or tenant starts again from the worst N.
watch([query, tenants, () => props.rows], () => {
  shownLimit.value = props.limit;
});
function sortBy(key: SortKey) {
  const { key: now, dir } = sort.value;
  sort.value = { key, dir: now === key ? (dir === "asc" ? "desc" : "asc") : FIRST_DIRECTION[key] };
}
function ariaSort(key: SortKey | null) {
  if (key !== sort.value.key) return undefined;
  return sort.value.dir === "asc" ? "ascending" : "descending";
}
</script>

<template>
  <section class="availability-section overflow-hidden rounded-lg border border-default" :data-avail-table="kind">
    <div class="flex flex-wrap items-center gap-3 bg-elevated/50 px-4 py-3">
      <h2 class="flex items-center gap-2 font-semibold text-highlighted" tabindex="-1">
        {{ title }}
        <UBadge color="neutral" variant="subtle" size="sm">{{ rows.length.toLocaleString("en") }}</UBadge>
        <HelpTip :text="help" />
      </h2>
      <div v-if="searchable" class="avail-filters ml-auto flex flex-wrap gap-2">
        <UInput v-model="query" class="avail-search w-56" type="search" icon="i-lucide-search" :placeholder="`Search ${kind}`" :aria-label="`Search ${kind}`" autocomplete="off" size="sm" />
        <USelectMenu v-if="kind === 'sites'" v-model="tenants" class="avail-tenant w-48" :items="tenantItems" value-key="value" :filter-fields="CHOICE_SEARCH_FIELDS" multiple placeholder="All tenants" aria-label="Tenant" size="sm" />
      </div>
    </div>
    <div class="overflow-x-auto border-t border-default">
      <table class="avail-table w-full text-sm">
        <thead>
          <tr class="text-left text-muted">
            <th
              v-for="column in columns"
              :key="column.label"
              :data-sort="column.key ?? undefined"
              :aria-sort="ariaSort(column.key)"
              class="px-4 py-2 font-medium"
              :class="{ 'text-right': column.num }"
            >
              <button v-if="column.key" type="button" class="sort-btn inline-flex items-center gap-1 hover:text-highlighted" @click="sortBy(column.key)">
                {{ column.label }}
                <UIcon
                  :name="ariaSort(column.key) === 'descending' ? 'i-lucide-arrow-down' : ariaSort(column.key) ? 'i-lucide-arrow-up' : 'i-lucide-arrow-up-down'"
                  class="size-3.5"
                  :class="{ 'opacity-40': !ariaSort(column.key) }"
                />
              </button>
              <template v-else>{{ column.label }}</template>
            </th>
          </tr>
        </thead>
        <tbody class="divide-y divide-default">
          <tr v-if="!rows.length">
            <td :colspan="columns.length" class="px-4 py-3 text-muted">No {{ kind }}.</td>
          </tr>
          <tr
            v-for="row in view.shown"
            :key="row.key"
            :data-name="row.name"
            :data-availability="row.availability"
            :data-tenants="kind === 'sites' ? JSON.stringify(row.tenants) : undefined"
          >
            <td class="px-4 py-2">
              <TenantList v-if="kind === 'tenants'" :tenants="[row.name]" :descriptions="descriptions" :links="statusLinks" />
              <span v-else>{{ row.name }}</span>
            </td>
            <td class="pct-cell px-4 py-2">
              <div class="flex items-center gap-3">
                <span class="w-20 tabular-nums" :class="row.availability < target ? 'font-semibold text-error' : ''">
                  {{ row.availability.toFixed(3) }} %
                </span>
                <span class="hidden h-1.5 w-24 overflow-hidden rounded-full bg-elevated sm:block" aria-hidden="true">
                  <span
                    class="block h-full rounded-full"
                    :class="row.availability < target ? 'bg-error' : 'bg-success'"
                    :style="{ width: `${barPercent(row.availability)}%` }"
                  />
                </span>
              </div>
            </td>
            <td v-if="kind === 'tenants'" class="px-4 py-2 text-right tabular-nums">{{ row.sites }}</td>
            <td class="px-4 py-2 text-right tabular-nums">{{ formatDowntime(row.downtime) }}</td>
            <template v-if="kind === 'sites'">
              <td class="px-4 py-2 text-right tabular-nums">{{ row.incidents }}</td>
              <td class="avail-devices whitespace-nowrap px-4 py-2 text-right tabular-nums text-muted">{{ row.devicesText }}</td>
              <td class="avail-tenants px-4 py-2 text-muted">
                <TenantList v-if="row.tenants.length" :tenants="row.tenants" :descriptions="descriptions" :links="statusLinks" />
                <template v-else>—</template>
              </td>
            </template>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="more" class="avail-more border-t border-default px-4 py-2 text-sm text-muted">
      {{ more }}
      <template v-if="view.rest.length">
        <UButton class="avail-more-btn ml-2" size="xs" color="neutral" variant="outline" @click="shownLimit += limit">
          Show {{ Math.min(limit, view.rest.length) }} more
        </UButton>
        <UButton v-if="view.rest.length > limit" class="avail-more-btn ml-1" size="xs" color="neutral" variant="link" @click="shownLimit = Infinity">
          Show all
        </UButton>
      </template>
    </p>
  </section>
</template>
