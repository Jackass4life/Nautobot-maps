<!-- The period button and its popover (#341, #353): quick choices, or the
     first and last day on the calendar.  Availability and the maintenance
     history. -->
<script setup lang="ts">
import { formatRange, QUICK_CHOICES, quickPeriod, type QuickChoice } from "~/utils/calendar";

const props = defineProps<{ from: string; to: string; max?: string; today: string }>();
const emit = defineEmits<{ pick: [from: string, to: string] }>();
const open = ref(false);

function choose(from: string, to: string) {
  open.value = false;
  emit("pick", from, to);
}
function quick(choice: QuickChoice) {
  const { from, to } = quickPeriod(choice, props.today);
  // A month that hasn't ended runs to today.
  choose(from, props.max && to > props.max ? props.max : to);
}
</script>

<template>
  <UPopover v-model:open="open" :content="{ align: 'start' }">
    <UButton id="period-button" color="neutral" variant="outline" trailing-icon="i-lucide-calendar" aria-haspopup="dialog">
      {{ formatRange(from, to) }}
    </UButton>
    <template #content>
      <div id="period-popover" class="space-y-3 p-3" role="dialog" aria-label="Choose a period">
        <div class="flex flex-wrap gap-1" role="group" aria-label="Quick choices">
          <UButton
            v-for="choice in QUICK_CHOICES"
            :key="choice.id"
            class="period-preset"
            size="xs"
            color="neutral"
            variant="soft"
            @click="quick(choice.id)"
          >{{ choice.label }}</UButton>
        </div>
        <RangeCalendar :from="from" :to="to" :max="max" :today="today" @pick="choose" />
        <p class="text-xs text-muted">Click the first day, then the last.</p>
      </div>
    </template>
  </UPopover>
</template>
