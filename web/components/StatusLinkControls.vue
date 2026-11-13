<!-- "Share…" next to a tenant on Availability (#530): its status page link.
     status.links.view (NWO, admin): the link, to copy.  status.links.manage:
     a customer link (a status token with the tenant's page, the key shown once),
     replacing a link that leaked, deleting it (#695).  The tenant's name opens
     its status in the app. -->
<script setup lang="ts">
import {
  EXPIRY_CHOICES,
  expiryBody,
  randomSuffix,
  shareUrl,
  statusTokenName,
  type NewToken,
} from "~/utils/admin";
import { copyText } from "~/utils/clipboard";
import { ApiRequestError } from "~/utils/api";
import { apiError, errorMessage } from "~/utils/format";

const props = defineProps<{ tenant: string }>();
const { can } = useCan();
const manage = computed(() => can("status.links.manage"));

const open = ref(false);
const expiry = ref("365");
const busy = ref(false);
const error = ref("");
// The link just made, with its key: shown only now.
const made = ref("");
const replaced = ref("");
const announce = ref("");
// The tenant's link today, without a key: "" while unread, null when it has none.
const current = ref<string | null>("");
const deleted = ref("");
// "Replace link" and "Delete link" ask once more; a few seconds later they forget.
const confirming = ref<"" | "replace" | "delete">("");
let confirmTimer: ReturnType<typeof setTimeout> | undefined;
const CONFIRM_MS = 4000;

function confirmFirst(what: "replace" | "delete"): boolean {
  if (confirming.value === what) {
    confirming.value = "";
    return true;
  }
  confirming.value = what;
  clearTimeout(confirmTimer);
  confirmTimer = setTimeout(() => (confirming.value = ""), CONFIRM_MS);
  return false;
}

async function readLink() {
  current.value = "";
  try {
    const answer = (await unwrap(
      useNuxtApp().$api.GET("/api/v1/status-links/{tenant}", {
        params: { path: { tenant: props.tenant } },
        cache: "no-store",
      }),
    )) as { status_link: string };
    current.value = shareUrl(window.location.origin, answer.status_link, "");
  } catch (err) {
    // 404: no link yet (reading never makes one, #687).
    if (err instanceof ApiRequestError && err.statusCode === 404) current.value = null;
    else error.value = errorMessage(err);
  }
}

watch(open, (now) => {
  if (now) {
    readLink();
    return;
  }
  // Closed: the key is gone from the page.
  made.value = replaced.value = deleted.value = error.value = announce.value = "";
  confirming.value = "";
});

async function createLink() {
  busy.value = true;
  error.value = "";
  try {
    let answer: { token: NewToken } | null = null;
    for (const extra of ["", randomSuffix()]) {
      try {
        answer = (await unwrap(
          useNuxtApp().$api.POST("/api/v1/tokens", {
            body: {
              name: statusTokenName(props.tenant, new Date(), extra),
              role: "status",
              tenant: props.tenant,
              ...expiryBody(expiry.value),
            } as never,
          }),
        )) as { token: NewToken };
        break;
      } catch (err) {
        // Today's name is taken: once more, with a few random letters.
        if (extra || !apiError(err).includes("already exists")) throw err;
      }
    }
    const token = answer!.token;
    if (!token.status_link)
      throw new Error("The token was made, but its link could not be read: try again");
    made.value = shareUrl(window.location.origin, token.status_link, token.token);
    current.value = shareUrl(window.location.origin, token.status_link, "");
    deleted.value = "";
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    busy.value = false;
  }
}

async function replaceLink() {
  if (!confirmFirst("replace")) return;
  busy.value = true;
  error.value = "";
  try {
    const answer = (await unwrap(
      useNuxtApp().$api.POST("/api/v1/status-links/{tenant}/replace", {
        params: { path: { tenant: props.tenant } },
      }),
    )) as { status_link: string };
    replaced.value = current.value = shareUrl(window.location.origin, answer.status_link, "");
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    busy.value = false;
  }
}

async function deleteLink() {
  if (!confirmFirst("delete")) return;
  busy.value = true;
  error.value = "";
  try {
    const answer = (await unwrap(
      useNuxtApp().$api.DELETE("/api/v1/status-links/{tenant}", {
        params: { path: { tenant: props.tenant } },
      }),
    )) as { message: string };
    deleted.value = answer.message;
    current.value = null;
    made.value = replaced.value = "";
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    busy.value = false;
  }
}

async function copy(text: string) {
  announce.value = (await copyText(text))
    ? "Copied to the clipboard"
    : "Could not copy: select the link and copy it";
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
      :title="manage ? 'Share the status page with the customer' : 'The status page link'"
      >Share…</UButton
    >
    <template #content>
      <div
        class="share-status-panel w-80 space-y-3 p-3 text-sm"
        role="group"
        :aria-label="`Share ${tenant}'s status page`"
      >
        <p class="font-semibold text-highlighted">{{ tenant }}'s status page</p>

        <!-- The link as it is (no key): for everyone here (status.links.view). -->
        <section class="share-current space-y-2">
          <p v-if="current === ''" class="text-muted">Loading the link…</p>
          <p v-else-if="current === null" class="share-none text-muted">{{
            deleted || "No link yet."
          }}</p>
          <div v-else class="flex gap-2">
            <UInput
              class="share-current-link min-w-0 flex-1"
              size="sm"
              :model-value="current"
              readonly
              aria-label="Status page link (without a key)"
              @focus="($event.target as HTMLInputElement).select()"
            />
            <UButton class="share-copy-link" size="sm" icon="i-lucide-copy" @click="copy(current)"
              >Copy</UButton
            >
          </div>
        </section>

        <template v-if="manage">
          <section v-if="!made" class="space-y-2 border-t border-default pt-3">
            <p class="text-muted"
              >A link for the customer, with a key of its own. It works until it expires or you
              revoke it.</p
            >
            <div class="flex items-center gap-2">
              <USelect
                v-model="expiry"
                class="share-expiry w-32"
                size="sm"
                :items="EXPIRY_CHOICES"
                aria-label="Works for"
              />
              <UButton
                class="share-create"
                size="sm"
                :loading="busy"
                :disabled="busy"
                @click="createLink"
                >Create customer link</UButton
              >
            </div>
          </section>
          <section v-else class="space-y-2 border-t border-default pt-3">
            <p class="font-medium">Shown only now: copy it and send it to the customer.</p>
            <div class="flex gap-2">
              <UInput
                class="share-link min-w-0 flex-1"
                size="sm"
                :model-value="made"
                readonly
                aria-label="Customer link"
                @focus="($event.target as HTMLInputElement).select()"
              />
              <UButton class="share-copy" size="sm" icon="i-lucide-copy" @click="copy(made)"
                >Copy</UButton
              >
            </div>
          </section>

          <section v-if="current" class="space-y-2 border-t border-default pt-3">
            <template v-if="!replaced">
              <p class="text-muted"
                >If a link leaked: a new one. The old link stops working at once; the customers'
                keys keep working with the new link.</p
              >
              <UButton
                class="share-replace"
                size="sm"
                :color="confirming === 'replace' ? 'error' : 'neutral'"
                :variant="confirming === 'replace' ? 'solid' : 'outline'"
                :disabled="busy"
                @click="replaceLink"
                >{{
                  confirming === "replace" ? "Sure? The old link stops now" : "Replace link…"
                }}</UButton
              >
            </template>
            <template v-else>
              <p class="font-medium"
                >New link (the old one no longer works). Send it with each customer's key:</p
              >
              <div class="flex gap-2">
                <UInput
                  class="share-new-link min-w-0 flex-1"
                  size="sm"
                  :model-value="replaced"
                  readonly
                  aria-label="New status page link"
                />
                <UButton size="sm" icon="i-lucide-copy" @click="copy(replaced)">Copy</UButton>
              </div>
            </template>
          </section>

          <section v-if="current" class="space-y-2 border-t border-default pt-3">
            <p class="text-muted">Customers using this link lose access at once.</p>
            <UButton
              class="share-delete"
              size="sm"
              :color="confirming === 'delete' ? 'error' : 'neutral'"
              :variant="confirming === 'delete' ? 'solid' : 'outline'"
              :disabled="busy"
              @click="deleteLink"
              >{{
                confirming === "delete" ? "Sure? Customers lose access now" : "Delete link…"
              }}</UButton
            >
          </section>
        </template>

        <p v-if="error" class="share-error text-error" role="alert">{{ error }}</p>
        <p class="sr-only" role="status">{{ announce }}</p>
        <p v-if="announce" class="text-xs text-muted" aria-hidden="true">{{ announce }}</p>
      </div>
    </template>
  </UPopover>
</template>
