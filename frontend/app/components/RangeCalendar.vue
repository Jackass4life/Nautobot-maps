<!-- A calendar for picking a range of days (#341; static/js/lib/calendar.js):
     click the first day, then the last; ‹ › change the month; while picking,
     the range follows the pointer.  Keys: arrows by day and week, PageUp /
     PageDown by month, Home / End the week. -->
<script setup lang="ts">
import { addDays, addMonths, clampDay, monthGrid, monthStart, parseDay, WEEKDAYS } from "~/utils/calendar";

const props = defineProps<{ from: string; to: string; min?: string; max?: string; today: string }>();
const emit = defineEmits<{ pick: [from: string, to: string] }>();

const limits = computed(() => ({ min: props.min || "", max: props.max || "" }));
const range = reactive({ from: "", to: "", picking: false });
const focus = ref("");
const month = ref("");
const hovered = ref("");
const root = useTemplateRef<HTMLElement>("root");

// Show *from*–*to*; with no range, open on today.
function set(from: string, to: string) {
  const day = (value: string) => (parseDay(value) ? clampDay(value, limits.value) : "");
  Object.assign(range, { from: day(from), to: day(to), picking: false });
  focus.value = range.to || clampDay(props.today, limits.value);
  month.value = monthStart(focus.value);
}
watch(() => [props.from, props.to], () => set(props.from, props.to), { immediate: true });

const grid = computed(() => monthGrid(month.value, { ...range, ...limits.value, today: props.today }));
const preview = computed(() => {
  if (!range.picking || !hovered.value) return null;
  return [range.from, hovered.value].sort() as [string, string];
});

function focusDay() {
  nextTick(() => root.value?.querySelector<HTMLButtonElement>(`[data-day="${focus.value}"]`)?.focus());
}
function step(by: number) {
  month.value = addMonths(month.value, by);
  focus.value = clampDay(month.value, limits.value);
}
function pick(day: string) {
  if (!range.picking) {
    Object.assign(range, { from: day, to: "", picking: true });
  } else {
    const [from, to] = day < range.from ? [day, range.from] : [range.from, day];
    Object.assign(range, { from, to, picking: false });
  }
  focus.value = day;
  hovered.value = "";
  focusDay();
  if (!range.picking) emit("pick", range.from, range.to);
}
function onKey(event: KeyboardEvent, day: string) {
  const weekday = (parseDay(day)!.getUTCDay() + 6) % 7;
  const moves: Record<string, () => string> = {
    ArrowLeft: () => addDays(day, -1),
    ArrowRight: () => addDays(day, 1),
    ArrowUp: () => addDays(day, -7),
    ArrowDown: () => addDays(day, 7),
    PageUp: () => addMonths(day, -1),
    PageDown: () => addMonths(day, 1),
    Home: () => addDays(day, -weekday),
    End: () => addDays(day, 6 - weekday),
  };
  const move = moves[event.key];
  if (!move) return;
  event.preventDefault();
  focus.value = clampDay(move(), limits.value);
  month.value = monthStart(focus.value);
  focusDay();
}

defineExpose({ focus: focusDay, set });
</script>

<template>
  <div ref="root" class="calendar w-64 select-none">
    <div class="mb-2 flex items-center justify-between">
      <UButton
        class="cal-nav"
        data-step="-1"
        icon="i-lucide-chevron-left"
        color="neutral"
        variant="ghost"
        size="sm"
        aria-label="Previous month"
        :disabled="!grid.prevOk"
        @click="step(-1)"
      />
      <span class="cal-title text-sm font-semibold" aria-live="polite">{{ grid.title }}</span>
      <UButton
        class="cal-nav"
        data-step="1"
        icon="i-lucide-chevron-right"
        color="neutral"
        variant="ghost"
        size="sm"
        aria-label="Next month"
        :disabled="!grid.nextOk"
        @click="step(1)"
      />
    </div>
    <div class="cal-grid grid grid-cols-7 gap-0.5 text-center text-sm" role="group" :aria-label="grid.title" @mouseleave="hovered = ''">
      <span v-for="name in WEEKDAYS" :key="name" class="cal-weekday py-1 text-xs text-muted" aria-hidden="true">{{ name }}</span>
      <span v-for="n in grid.lead" :key="`blank-${n}`" class="cal-blank" />
      <button
        v-for="cell in grid.days"
        :key="cell.day"
        type="button"
        class="cal-day h-8 rounded-md tabular-nums transition-colors disabled:cursor-not-allowed disabled:opacity-30"
        :class="[
          cell.start || cell.end
            ? 'bg-primary font-semibold text-inverted'
            : cell.inRange || (preview && cell.day >= preview[0] && cell.day <= preview[1])
              ? 'bg-primary/15 text-highlighted'
              : 'hover:bg-elevated',
          cell.today && !(cell.start || cell.end) ? 'font-semibold text-primary' : '',
          { 'in-range': cell.inRange, 'range-start': cell.start, 'range-end': cell.end, 'is-today': cell.today },
        ]"
        :data-day="cell.day"
        :tabindex="cell.day === focus ? 0 : -1"
        :aria-label="cell.label"
        :aria-pressed="cell.start || cell.end"
        :disabled="cell.disabled"
        @click="pick(cell.day)"
        @mouseover="range.picking && !cell.disabled ? (hovered = cell.day) : undefined"
        @keydown="onKey($event, cell.day)"
      >{{ cell.date }}</button>
    </div>
  </div>
</template>
