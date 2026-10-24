<!-- The wall screen, /alerts?view=wall (#243; on Nuxt since #463): the sites
     in alarm, every one open, nothing to click; the ones nobody is on at the
     top, those someone is on faded below a dashed line (#271), maintenance
     last (#283).  When it last updated, and loudly when it can't.  A chime and
     a notification for a new Critical, per screen (#290).  No top bar. -->
<script setup lang="ts">
import { emptyBoardText, hasActiveAlarm, siteCaseState, type Board, type BoardSite } from "~/utils/board";
import { errorMessage } from "~/utils/format";
import { inScope, readScope, scopeLabel, scopeParams } from "~/utils/scope";
import {
  WALL_RELOAD_MS,
  WALL_SOUND_KEY,
  WallNotifier,
  newAlarms,
  wallErrorText,
  wallSites,
  wallStatus,
  type NotificationApi,
} from "~/utils/wall";

useHead({ title: "Wall · Nautobot Maps" });

const { data: config } = usePageConfig();
const caseTemplate = computed(() => config.value?.board?.case_url_template || "");

// ?tenant= (repeatable) and ?q=: a wall for some sites only (#364).
const scope = readScope(window.location.search);
const scopeQuery = scopeParams(scope).toString();
const operatorView = `/alerts${scopeQuery ? `?${scopeQuery}` : ""}`;

// Up to 1,200 sites: not deeply reactive.
const board = shallowRef<Board | null>(null);
const sites = shallowRef<BoardSite[]>([]);
const lastLoadedAt = ref<number | null>(null);
const failed = ref("");
const now = ref(Date.now());

const scopeText = computed(() => scopeLabel(scope, sites.value));
const status = computed(() => wallStatus(sites.value, lastLoadedAt.value, now.value, scope ? scopeText.value : ""));
const shown = computed(() => wallSites(sites.value));
// Each row's place: someone on it (faded), the first of those (a dashed
// line), the first in maintenance (a heading).
const rows = computed(() => {
  let handledSeen = false;
  let maintenanceSeen = false;
  return shown.value.map((site) => {
    const inMaintenance = site.alert_level === "maintenance";
    const heading = inMaintenance && !maintenanceSeen;
    if (inMaintenance) maintenanceSeen = true;
    const handled = !inMaintenance && siteCaseState(site).state === "handled";
    const handledFirst = handled && !handledSeen;
    if (handled) handledSeen = true;
    return { site, heading, handled, handledFirst };
  });
});
const empty = computed(() => {
  if (!board.value || shown.value.length) return null;
  const why = emptyBoardText(
    board.value,
    { inScope: sites.value.length, severity: "alarms", scopeText: scope ? scopeText.value : "" },
    sites.value.some(hasActiveAlarm),
  );
  return why.kind === "all-clear" ? { kind: why.kind, text: `No active alarms${scope ? ` for ${scopeText.value}` : ""}.` } : why;
});

// The chime and notifications: off until someone turns them on, per screen.
const soundOn = ref(false);
let audio: AudioContext | null = null;
let announced: Map<string, Set<string>> | null = null;
const notifier = new WallNotifier(() => (window as unknown as { Notification?: NotificationApi }).Notification);

function unlockAudio() {
  const Context = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (Context && !audio) audio = new Context();
  audio?.resume();
}
// Two short tones from the browser itself: no sound file to load.
function chime() {
  if (!audio) return;
  const start = audio.currentTime;
  [880, 660].forEach((frequency, index) => {
    const oscillator = audio!.createOscillator();
    const gain = audio!.createGain();
    const at = start + index * 0.25;
    oscillator.frequency.value = frequency;
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.exponentialRampToValueAtTime(0.3, at + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.22);
    oscillator.connect(gain).connect(audio!.destination);
    oscillator.start(at);
    oscillator.stop(at + 0.24);
  });
}
function setSound(on: boolean) {
  soundOn.value = on;
  if (!on) notifier.clear();
  try {
    if (on) window.localStorage.setItem(WALL_SOUND_KEY, "on");
    else window.localStorage.removeItem(WALL_SOUND_KEY);
  } catch {
    // Not remembered; works for this page.
  }
}
function toggleSound() {
  const on = !soundOn.value;
  if (on) {
    // This click is what lets the browser play sound.
    unlockAudio();
    const api = (window as unknown as { Notification?: NotificationApi & { requestPermission(): Promise<string> } }).Notification;
    // Show what came in while the browser was asking.
    if (api && api.permission === "default") Promise.resolve(api.requestPermission()).then(() => notifier.show(), () => {});
    chime();
  }
  setSound(on);
}
function announce(list: BoardSite[]) {
  const { events, seen } = newAlarms(announced, list);
  announced = seen;
  if (!soundOn.value || !events.length) return;
  chime();
  notifier.add(events);
}

// Loads can overlap (the minute's reload, sync polling); only the newest counts.
let loadSeq = 0;
const SYNC_POLL_MS = 5000;
const SYNC_POLL_MAX = 36;
let syncTimer: ReturnType<typeof setTimeout> | null = null;
let syncPolls = 0;
async function load() {
  const seq = ++loadSeq;
  try {
    const answer = await $fetch<Board>("/api/alerts");
    if (seq !== loadSeq) return;
    board.value = answer;
    sites.value = scope ? answer.alerts.filter((site) => inScope(site, scope)) : answer.alerts;
    lastLoadedAt.value = Date.now();
    failed.value = "";
    announce(sites.value);
    if (syncTimer) clearTimeout(syncTimer);
    syncTimer = null;
    if (answer.sync_pending && syncPolls < SYNC_POLL_MAX) {
      syncPolls += 1;
      syncTimer = setTimeout(load, SYNC_POLL_MS);
    } else if (!answer.sync_pending) syncPolls = 0;
  } catch (err) {
    // The last good board stays on screen (even an empty one), under a banner.
    if (seq === loadSeq) failed.value = errorMessage(err);
  }
}

let reloadTimer: ReturnType<typeof setInterval> | undefined;
let clockTimer: ReturnType<typeof setInterval> | undefined;
onMounted(() => {
  let remembered = false;
  try {
    remembered = window.localStorage.getItem(WALL_SOUND_KEY) === "on";
  } catch {
    remembered = false;
  }
  if (remembered) {
    // Remembered, but a browser plays sound only after a click on this page:
    // the first click anywhere unlocks it.
    setSound(true);
    document.addEventListener("click", unlockAudio, { once: true });
  }
  load();
  reloadTimer = setInterval(load, WALL_RELOAD_MS);
  clockTimer = setInterval(() => (now.value = Date.now()), 15000);
});
onBeforeUnmount(() => {
  clearInterval(reloadTimer);
  clearInterval(clockTimer);
  if (syncTimer) clearTimeout(syncTimer);
  document.removeEventListener("click", unlockAudio);
});
</script>

<template>
  <main class="wall-view space-y-4 px-4 py-4">
    <div class="wall-bar flex items-baseline justify-between gap-4">
      <span id="wall-updated" class="wall-updated text-base font-bold tabular-nums" :class="{ 'wall-stale text-error': status.stale }">{{ status.text }}</span>
      <div class="flex items-center gap-4">
        <UButton
          id="wall-sound"
          class="wall-sound whitespace-nowrap"
          size="sm"
          :color="soundOn ? 'primary' : 'neutral'"
          variant="outline"
          :icon="soundOn ? 'i-lucide-volume-2' : 'i-lucide-volume-x'"
          :aria-pressed="soundOn ? 'true' : 'false'"
          @click="toggleSound"
        >{{ soundOn ? "Sound on" : "Sound off" }}</UButton>
        <a class="wall-exit text-sm text-muted hover:underline" :href="operatorView">Operator view</a>
      </div>
    </div>
    <div v-if="failed" id="wall-error" class="wall-error rounded-lg bg-error px-4 py-3 text-base font-bold text-white" role="alert">
      {{ wallErrorText(failed, lastLoadedAt) }}
    </div>

    <section class="table-shell relative overflow-x-auto rounded-lg border border-default" aria-label="Alert table">
      <table class="board-table w-full text-left text-sm">
        <thead class="bg-elevated/50 text-xs text-muted uppercase">
          <tr>
            <th class="px-3 py-2">Location</th>
            <th class="px-3 py-2">Alert</th>
            <th class="px-3 py-2">Since</th>
            <th class="col-cases px-3 py-2">Cases</th>
          </tr>
        </thead>
        <tbody id="alerts-table-body" class="divide-y divide-default">
          <tr v-if="!board">
            <td colspan="4" class="empty-state px-3 py-10 text-center text-lg text-muted">{{ failed ? "" : "Loading alerts…" }}</td>
          </tr>
          <tr v-else-if="empty">
            <td
              colspan="4"
              class="empty-state px-3 py-10 text-center text-xl"
              :class="{ 'scope-no-match font-bold': empty.kind === 'no-match', 'all-clear text-success': empty.kind === 'all-clear', 'text-muted': empty.kind === 'plain' }"
            >{{ empty.text }}</td>
          </tr>
          <template v-for="row in rows" v-else :key="row.site.id">
            <tr v-if="row.heading" class="maintenance-heading">
              <td colspan="4">In maintenance</td>
            </tr>
            <BoardRow
              :item="row.site"
              :expanded="true"
              :case-template="caseTemplate"
              :now="lastLoadedAt ?? 0"
              :handled="row.handled"
              :handled-first="row.handledFirst"
              wall
            />
          </template>
        </tbody>
      </table>
    </section>
  </main>
</template>
