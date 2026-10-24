<!-- The map panel's quiet bottom (#392): the filters folded behind one
     button, a chip per filter in use (so it is clear why sites are missing),
     the count, and notes only when they apply (the slot). -->
<script setup lang="ts">
import { FILTERS, type Criteria, type FilterField } from "~/utils/map";

const criteria = defineModel<Criteria>({ required: true });
const props = defineProps<{ choices: Criteria; count: string }>();
const emit = defineEmits<{ clear: [] }>();

const open = ref(false);
const chips = computed(() =>
  FILTERS.flatMap(({ field, label }) => criteria.value[field].map((value) => ({ field, label, value }))),
);
// The common statuses as buttons (#311), only when some site has them and there is a choice.
const QUICK = ["Active", "Planned"];
const quickStatuses = computed(() =>
  props.choices.status.length < 2 ? [] : ["", ...QUICK.filter((status) => props.choices.status.includes(status))],
);
// A filter with fewer than two choices is no choice.
const shown = computed(() => FILTERS.filter(({ field }) => props.choices[field].length >= 2));

function set(field: FilterField, values: string[]) {
  criteria.value = { ...criteria.value, [field]: values };
}
function remove(field: FilterField, value: string) {
  set(field, criteria.value[field].filter((other) => other !== value));
}
function quick(status: string) {
  const selected = criteria.value.status;
  set("status", !status ? [] : selected.includes(status) ? selected.filter((v) => v !== status) : [...selected, status]);
}
function quickActive(status: string) {
  return status ? criteria.value.status.includes(status) : !criteria.value.status.length;
}
// The menu's search field, named for screen readers (Nuxt UI passes it on to the <input>).
function searchInput(label: string) {
  return { placeholder: "Search…", "aria-label": `Search ${label}` };
}
// "Acme Corp +1".
function shortLabel(values: string[], all: string) {
  if (!values.length) return all;
  return values.length === 1 ? values[0] : `${values[0]} +${values.length - 1}`;
}
</script>

<template>
  <div id="sidebar-footer" class="space-y-2 border-t border-default pt-3">
    <div v-if="chips.length" id="filter-chips" class="filter-chips flex flex-wrap gap-1" role="group" aria-label="Active filters">
      <UButton
        v-for="chip in chips"
        :key="chip.field + chip.value"
        class="filter-chip"
        size="xs"
        color="primary"
        variant="soft"
        trailing-icon="i-lucide-x"
        :aria-label="`Remove filter ${chip.label}: ${chip.value}`"
        @click="remove(chip.field, chip.value)"
      >{{ chip.value }}</UButton>
    </div>
    <div class="footer-row flex items-center justify-between gap-2">
      <UButton
        id="filters-toggle"
        class="filters-toggle"
        size="sm"
        color="neutral"
        variant="ghost"
        icon="i-lucide-sliders-horizontal"
        :aria-expanded="open ? 'true' : 'false'"
        aria-controls="filter-section"
        @click="open = !open"
      ><span>Filters<span v-if="chips.length" id="filter-count" class="filter-count"> · {{ chips.length }}</span></span></UButton>
      <span id="location-count" class="text-sm text-muted">{{ count }}</span>
    </div>
    <div v-show="open" id="filter-section" class="space-y-2">
      <div v-if="quickStatuses.length" class="quick-filters flex gap-1" role="group" aria-label="Status">
        <UButton
          v-for="status in quickStatuses"
          :key="status"
          class="quick-filter-btn"
          :class="{ active: quickActive(status) }"
          :data-quick-status="status"
          size="xs"
          :color="quickActive(status) ? 'primary' : 'neutral'"
          :variant="quickActive(status) ? 'soft' : 'outline'"
          :aria-pressed="quickActive(status) ? 'true' : 'false'"
          @click="quick(status)"
        >{{ status || "All" }}</UButton>
      </div>
      <USelectMenu
        v-for="filter in shown"
        :key="filter.field"
        :model-value="criteria[filter.field]"
        :items="choices[filter.field]"
        multiple
        :placeholder="filter.all"
        :aria-label="filter.label"
        :search-input="searchInput(filter.label)"
        class="w-full"
        @update:model-value="set(filter.field, $event as string[])"
      >
        <span class="truncate" :class="{ 'text-dimmed': !criteria[filter.field].length }" :title="criteria[filter.field].join(', ')">
          {{ shortLabel(criteria[filter.field], filter.all) }}
        </span>
      </USelectMenu>
      <UButton id="clear-filters" size="sm" color="neutral" variant="outline" @click="emit('clear')">Clear filters</UButton>
    </div>
    <slot />
  </div>
</template>
