<!-- "Share…" next to a tenant on Availability (#530), for admins: a customer
     link (a status token with the tenant's page, the key shown once) and
     replacing a link that leaked.  The tenant's name still opens its page. -->
<script setup lang="ts">
import { EXPIRY_CHOICES, expiryBody, randomSuffix, shareUrl, statusTokenName, type NewToken } from "~/utils/admin";
import { copyText } from "~/utils/clipboard";
import { apiError, errorMessage } from "~/utils/format";

const props = defineProps<{ tenant: string }>();

const open = ref(false);
const expiry = ref("365");
const busy = ref(false);
const error = ref("");
// The link just made, with its key: shown only now.
const made = ref("");
const replaced = ref("");
const announce = ref("");
// "Replace link" asks once more; a few seconds later it forgets.
const confirming = ref(false);
let confirmTimer: ReturnType<typeof setTimeout> | undefined;
const CONFIRM_MS = 4000;

watch(open, (now) => {
  if (now) return;
  // Closed: the key is gone from the page.
  made.value = replaced.value = error.value = announce.value = "";
  confirming.value = false;
});

async function createLink() {
  busy.value = true;
  error.value = "";
  try {
    let answer: { token: NewToken } | null = null;
    for (const extra of ["", randomSuffix()]) {
      try {
        answer = await $fetch<{ token: NewToken }>("/api/tokens", {
          method: "POST",
          body: {
            name: statusTokenName(props.tenant, new Date(), extra),
            role: "status",
            tenant: props.tenant,
            ...expiryBody(expiry.value),
          },
        });
        break;
      } catch (err) {
        // Today's name is taken: once more, with a few random letters.
        if (extra || !apiError(err).includes("already exists")) throw err;
      }
    }
    const token = answer!.token;
    if (!token.status_link) throw new Error("The token was made, but its link could not be read: try again");
    made.value = shareUrl(window.location.origin, token.status_link, token.token);
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    busy.value = false;
  }
}

async function replaceLink() {
  if (!confirming.value) {
    confirming.value = true;
    clearTimeout(confirmTimer);
    confirmTimer = setTimeout(() => (confirming.value = false), CONFIRM_MS);
    return;
  }
  confirming.value = false;
  busy.value = true;
  error.value = "";
  try {
    const answer = await $fetch<{ status_link: string }>(
      `/api/status-links/${encodeURIComponent(props.tenant)}/replace`,
      { method: "POST" },
    );
    replaced.value = shareUrl(window.location.origin, answer.status_link, "");
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    busy.value = false;
  }
}

async function copy(text: string) {
  announce.value = (await copyText(text)) ? "Copied to the clipboard" : "Could not copy: select the link and copy it";
}
onBeforeUnmount(() => clearTimeout(confirmTimer));
</script>

<template>
  <UPopover v-model:open="open" :content="{ align: 'start' }">
    <UButton
      class="share-status"
      size="xs"
      color="neutral"
      variant="ghost"
      icon="i-lucide-share-2"
      :aria-label="`Share ${tenant}'s status page`"
      title="Share the status page with the customer"
    >Share…</UButton>
    <template #content>
      <div class="share-status-panel w-80 space-y-3 p-3 text-sm" role="group" :aria-label="`Share ${tenant}'s status page`">
        <p class="font-semibold text-highlighted">{{ tenant }}'s status page</p>

        <section v-if="!made" class="space-y-2">
          <p class="text-muted">A link for the customer, with a key of its own. It works until it expires or you revoke it.</p>
          <div class="flex items-center gap-2">
            <USelect v-model="expiry" class="share-expiry w-32" size="sm" :items="EXPIRY_CHOICES" aria-label="Works for" />
            <UButton class="share-create" size="sm" :loading="busy" :disabled="busy" @click="createLink">Create customer link</UButton>
          </div>
        </section>
        <section v-else class="space-y-2">
          <p class="font-medium">Shown only now: copy it and send it to the customer.</p>
          <div class="flex gap-2">
            <UInput class="share-link min-w-0 flex-1" size="sm" :model-value="made" readonly aria-label="Customer link" @focus="($event.target as HTMLInputElement).select()" />
            <UButton class="share-copy" size="sm" icon="i-lucide-copy" @click="copy(made)">Copy</UButton>
          </div>
        </section>

        <section class="space-y-2 border-t border-default pt-3">
          <template v-if="!replaced">
            <p class="text-muted">If a link leaked: a new one. The old link stops working at once; the customers' keys keep working with the new link.</p>
            <UButton
              class="share-replace"
              size="sm"
              :color="confirming ? 'error' : 'neutral'"
              :variant="confirming ? 'solid' : 'outline'"
              :disabled="busy"
              @click="replaceLink"
            >{{ confirming ? "Sure? The old link stops now" : "Replace link…" }}</UButton>
          </template>
          <template v-else>
            <p class="font-medium">New link (the old one no longer works). Send it with each customer's key:</p>
            <div class="flex gap-2">
              <UInput class="share-new-link min-w-0 flex-1" size="sm" :model-value="replaced" readonly aria-label="New status page link" />
              <UButton size="sm" icon="i-lucide-copy" @click="copy(replaced)">Copy</UButton>
            </div>
          </template>
        </section>

        <p v-if="error" class="share-error text-error" role="alert">{{ error }}</p>
        <p class="sr-only" role="status">{{ announce }}</p>
        <p v-if="announce" class="text-xs text-muted" aria-hidden="true">{{ announce }}</p>
      </div>
    </template>
  </UPopover>
</template>
