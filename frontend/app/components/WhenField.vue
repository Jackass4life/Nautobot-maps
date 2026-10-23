<!-- "When" for a maintenance window (#343; static/js/lib/when.js): now for
     some hours, or planned days on the calendar with times in 15-minute
     steps, in the browser's time zone. -->
<script setup lang="ts">
import { addDays } from "~/utils/calendar";
import { DURATIONS, localDay, TIME_OPTIONS, whenSummary, type WhenValues } from "~/utils/when";

const values = defineModel<WhenValues>({ required: true });
const today = localDay(new Date());
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "your time zone";
const summary = computed(() => whenSummary({ ...values.value, when: "planned" }));
const durations = DURATIONS.map((minutes) => ({ value: minutes, label: `${minutes / 60} hour${minutes === 60 ? "" : "s"}` }));

function set<K extends keyof WhenValues>(key: K, value: WhenValues[K]) {
  values.value = { ...values.value, [key]: value };
}
function onPick(from: string, to: string) {
  values.value = { ...values.value, startDay: from, endDay: to };
}
</script>

<template>
  <fieldset class="when-fieldset space-y-2">
    <legend class="mb-1 text-sm font-semibold text-highlighted">When</legend>
    <label class="flex items-center gap-2 text-sm">
      <input
        type="radio"
        name="maint-when"
        value="now"
        class="accent-(--ui-primary)"
        :checked="values.when === 'now'"
        @change="set('when', 'now')"
      >
      Now, for
      <USelect
        class="maint-duration w-28"
        size="sm"
        :items="durations"
        :model-value="values.duration"
        aria-label="How long"
        @update:model-value="set('duration', Number($event))"
      />
    </label>
    <label class="flex items-center gap-2 text-sm">
      <input
        type="radio"
        name="maint-when"
        value="planned"
        class="accent-(--ui-primary)"
        :checked="values.when === 'planned'"
        @change="set('when', 'planned')"
      >
      Planned
    </label>
    <div v-if="values.when === 'planned'" class="maint-planned flex flex-wrap gap-6 pt-2">
      <RangeCalendar
        class="when-calendar"
        :from="values.startDay"
        :to="values.endDay"
        :min="today"
        :max="addDays(today, 365)"
        :today="today"
        @pick="onPick"
      />
      <div class="when-times max-w-xs space-y-3 text-sm">
        <p class="when-days font-medium text-highlighted" aria-live="polite">{{ summary }}</p>
        <div class="flex flex-wrap items-center gap-3">
          <label class="flex items-center gap-2">From
            <USelect
              class="when-start-time w-24"
              size="sm"
              :items="TIME_OPTIONS"
              :model-value="values.startTime"
              aria-label="Start time"
              @update:model-value="set('startTime', String($event))"
            />
          </label>
          <label class="flex items-center gap-2">To
            <USelect
              class="when-end-time w-24"
              size="sm"
              :items="TIME_OPTIONS"
              :model-value="values.endTime"
              aria-label="End time"
              @update:model-value="set('endTime', String($event))"
            />
          </label>
        </div>
        <p class="when-hint text-xs text-muted">
          Click the first and the last day (the same day twice for one day; a To before the From runs overnight).
          Times in {{ zone }}.
        </p>
      </div>
    </div>
  </fieldset>
</template>
