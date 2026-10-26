<!-- One device in the site panel (#394): its status dot, name, case (#512) and role; a
     click opens the rest in place.  A stack's lead (#400) says "Stack of 4"
     and lists its members; *size* is the whole stack's when some members are
     listed elsewhere. -->
<script setup lang="ts">
import {
  deviceAside,
  deviceCase,
  deviceCountNote,
  deviceFacts,
  deviceHealth,
  deviceKey,
  deviceNote,
  memberText,
  type SiteDevice,
} from "~/utils/sitepanel";

const props = withDefaults(defineProps<{ device: SiteDevice; open: boolean; members?: SiteDevice[]; size?: number }>(), {
  members: () => [],
  size: undefined,
});
defineEmits<{ toggle: [key: string] }>();

const health = computed(() => deviceHealth(props.device.status));
// A stack is one device, its lead (#405): the server makes it down when any of its IPs is.
const dot = computed(() => (health.value === "down" ? "critical" : health.value === "active" ? "ok" : "no-data"));
const stackSize = computed(() => (props.members.length ? (props.size ?? props.members.length + 1) : 1));
const note = computed(() => deviceNote(props.device));
const aside = computed(() => deviceAside(props.device, stackSize.value));
const facts = computed(() => deviceFacts(props.device, aside.value));
const countNote = computed(() => deviceCountNote(props.device));
// Its open alert's case (#512), as on the board.
const caseText = computed(() => deviceCase(props.device));
// Counted down, the whole stack is (no number: this site may not have every member).
const membersTitle = computed(() =>
  health.value === "down" && !note.value ? "The whole stack is down, all its members:" : `Stack of ${stackSize.value}:`,
);
</script>

<template>
  <li class="device-row-item" :class="{ open }">
    <button
      class="device-row flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-elevated"
      type="button"
      :data-device-key="deviceKey(device)"
      :aria-expanded="open ? 'true' : 'false'"
      @click="$emit('toggle', deviceKey(device))"
    >
      <span class="legend-dot" :class="`dot-${dot}`" aria-hidden="true" />
      <span class="device-row-name min-w-0 flex-1 truncate font-medium text-highlighted">{{ device.name || "Unnamed device" }}</span>
      <span v-if="caseText" class="device-row-case shrink-0 font-mono text-xs text-toned">{{ caseText }}</span>
      <span class="device-row-role shrink-0 truncate text-xs" :class="note ? 'device-row-note text-warning' : 'text-muted'">{{ aside }}</span>
    </button>
    <div v-if="open" class="device-details space-y-0.5 pb-2 pl-7 pr-2 text-xs text-muted">
      <div v-for="fact in facts" :key="fact" class="device-meta">{{ fact }}</div>
      <div v-if="countNote" class="device-meta device-not-monitored text-warning">{{ countNote }}</div>
      <div v-if="members.length" class="stack-members mt-1.5">
        <div class="device-meta">{{ membersTitle }}</div>
        <div v-for="member in members" :key="deviceKey(member)" class="device-meta stack-member pl-2.5">{{ memberText(member) }}</div>
      </div>
    </div>
  </li>
</template>
