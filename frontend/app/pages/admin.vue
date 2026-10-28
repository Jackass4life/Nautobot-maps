<!-- Administration (#530): what the API can do, the web pages can do too.
     API tokens, the test notification and the wireless controllers' check,
     one section each, quiet by default; a section with nothing to show is
     left out.  Only for those who may administer (page-config's admin). -->
<script setup lang="ts">
import { channelLine, wirelessLine, type WirelessCheck } from "~/utils/admin";
import { apiError, errorMessage } from "~/utils/format";

useHead({ title: "Admin · Nautobot Maps" });

const { data: config, status: configStatus } = usePageConfig();
const admin = computed(() => config.value?.admin ?? null);

// The test notification, per channel.
const sending = ref(false);
const sent = shallowRef<ReturnType<typeof channelLine>[] | null>(null);
const sendError = ref("");
async function sendTest() {
  sending.value = true;
  sendError.value = "";
  sent.value = null;
  try {
    const answer = await $fetch<{ results: Record<string, string> }>("/api/notifications/test", { method: "POST" });
    sent.value = Object.entries(answer.results).map(([channel, result]) => channelLine(channel, result));
  } catch (err) {
    // 502: a channel failed; the answer still says which.
    const results = (err as { data?: { results?: Record<string, string> } } | null)?.data?.results;
    if (results) sent.value = Object.entries(results).map(([channel, result]) => channelLine(channel, result));
    else sendError.value = errorMessage(err);
  } finally {
    sending.value = false;
  }
}

// The wireless controllers, read now.
const checking = ref(false);
const checked = shallowRef<WirelessCheck | null>(null);
const checkError = ref("");
async function checkWireless() {
  checking.value = true;
  checkError.value = "";
  try {
    checked.value = await $fetch<WirelessCheck>("/api/wireless/check", { method: "POST" });
  } catch (err) {
    checkError.value = apiError(err) || errorMessage(err);
  } finally {
    checking.value = false;
  }
}
</script>

<template>
  <div>
    <AppBar active="admin" />
    <main class="mx-auto max-w-6xl space-y-4 px-4 py-6">
      <h1 class="text-2xl font-bold text-highlighted">Admin</h1>
      <p v-if="configStatus === 'pending'" class="text-sm text-muted">Loading…</p>
      <p v-else-if="!admin" id="admin-refused" class="max-w-3xl text-sm text-muted">
        Only admins can use this page: API tokens, test notifications and the wireless controllers' check.
      </p>
      <template v-else>
        <AdminTokens />

        <section v-if="admin.notification_channels.length" id="admin-notifications" class="space-y-3 rounded-lg border border-default px-4 py-3">
          <div class="flex flex-wrap items-center gap-3">
            <h2 class="font-semibold text-highlighted">Notifications</h2>
            <p class="text-sm text-muted">{{ admin.notification_channels.map((c) => channelLine(c, "ok").channel).join(", ") }}</p>
            <UButton id="notify-test" class="ml-auto" size="sm" color="neutral" variant="outline" icon="i-lucide-send" :loading="sending" :disabled="sending" @click="sendTest">
              Send test
            </UButton>
          </div>
          <ul v-if="sent" id="notify-results" class="space-y-1 text-sm" aria-live="polite">
            <li v-for="line in sent" :key="line.channel" class="flex gap-2">
              <UIcon :name="line.ok ? 'i-lucide-check' : 'i-lucide-x'" class="mt-0.5 size-4 shrink-0" :class="line.ok ? 'text-success' : 'text-error'" />
              <span><span class="font-medium">{{ line.channel }}:</span> {{ line.text }}</span>
            </li>
          </ul>
          <p v-if="sendError" class="text-sm text-error" role="alert">{{ sendError }}</p>
        </section>

        <section v-if="admin.wireless_controllers" id="admin-wireless" class="space-y-3 rounded-lg border border-default px-4 py-3">
          <div class="flex flex-wrap items-center gap-3">
            <h2 class="font-semibold text-highlighted">Wireless controllers</h2>
            <p class="text-sm text-muted">{{ admin.wireless_controllers }} in the controllers file</p>
            <UButton id="wireless-check" class="ml-auto" size="sm" color="neutral" variant="outline" icon="i-lucide-radio-tower" :loading="checking" :disabled="checking" @click="checkWireless">
              Check now
            </UButton>
          </div>
          <ul v-if="checked" id="wireless-results" class="space-y-1 text-sm" aria-live="polite">
            <li v-for="result in checked.controllers" :key="result.url" class="flex gap-2">
              <UIcon :name="result.error ? 'i-lucide-x' : 'i-lucide-check'" class="mt-0.5 size-4 shrink-0" :class="result.error ? 'text-error' : 'text-success'" />
              <span>
                <span class="font-medium">{{ result.controller }}:</span> {{ wirelessLine(result) }}
                <span v-if="result.unmatched_examples?.length" class="block text-xs text-muted">
                  Not in Nautobot (serial or name), e.g.: {{ result.unmatched_examples.join(", ") }}
                </span>
              </span>
            </li>
            <li v-if="checked.duplicate_count" class="text-muted">
              {{ checked.duplicate_count.toLocaleString("en") }} access point(s) listed by more than one controller (the newest answer wins).
            </li>
          </ul>
          <p v-if="checkError" class="text-sm text-error" role="alert">{{ checkError }}</p>
        </section>
      </template>
    </main>
  </div>
</template>
