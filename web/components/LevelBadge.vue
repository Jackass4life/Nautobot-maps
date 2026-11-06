<!-- An alert level as a small badge (#311): its colour and its name; the
     reason ("2/7 devices offline (29%)") on hover and on keyboard focus. -->
<script setup lang="ts">
import { levelLabel } from "~/utils/board";

const props = defineProps<{ level: string | null | undefined; reason?: string | null }>();
const value = computed(() => props.level || "no_data");
</script>

<template>
  <UTooltip v-if="reason" :text="reason">
    <span
      class="alert-badge"
      :class="`alert-${value}`"
      tabindex="0"
      role="img"
      :aria-label="`${levelLabel(value)}: ${reason}`"
    >{{ levelLabel(value) }}</span>
  </UTooltip>
  <span v-else class="alert-badge" :class="`alert-${value}`">{{ levelLabel(value) }}</span>
</template>
