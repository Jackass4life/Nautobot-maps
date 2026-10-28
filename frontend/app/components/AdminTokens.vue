<!-- The Admin page's API tokens (#530): the active ones, newest first; the
     revoked and expired folded, with Purge; a new token, its key shown once;
     revoke per row.  Everything the API's /api/tokens does. -->
<script setup lang="ts">
import {
  day,
  EXPIRY_CHOICES,
  expiryBody,
  inactiveText,
  purgedText,
  ROLE_HINTS,
  ROLES,
  shareUrl,
  TOKEN_SEARCH_FROM,
  tokenMatches,
  validName,
  type NewToken,
  type Role,
  type Tenant,
  type Token,
} from "~/utils/admin";
import { copyText } from "~/utils/clipboard";
import { errorMessage } from "~/utils/format";
import { CHOICE_SEARCH_FIELDS, choiceItems } from "~/utils/tenants";

const tokens = shallowRef<Token[]>([]);
const inactive = ref(0);
const showAll = ref(false);
const loaded = ref(false);
const error = ref("");
const message = ref("");
const query = ref("");
const busy = ref(false);

const shown = computed(() => tokens.value.filter((token) => tokenMatches(token, query.value)));
const searchable = computed(() => tokens.value.length > TOKEN_SEARCH_FROM);

async function load() {
  try {
    const answer = await $fetch<{ tokens: Token[]; inactive: number }>(`/api/tokens${showAll.value ? "?all=true" : ""}`);
    tokens.value = answer.tokens;
    inactive.value = answer.inactive;
    error.value = "";
  } catch (err) {
    error.value = `Could not load the tokens: ${errorMessage(err)}`;
  } finally {
    loaded.value = true;
  }
}
onMounted(load);
watch(showAll, load);

// A two-step confirm, as Maintenance's End: the button asks "Sure?" and
// forgets after a few seconds.  Which one is asking: a token's id, or "purge".
const confirming = ref<number | "purge" | null>(null);
let confirmTimer: ReturnType<typeof setTimeout> | undefined;
function confirmFirst(what: number | "purge"): boolean {
  if (confirming.value === what) {
    confirming.value = null;
    return true;
  }
  confirming.value = what;
  clearTimeout(confirmTimer);
  confirmTimer = setTimeout(() => (confirming.value = null), 4000);
  return false;
}
onBeforeUnmount(() => clearTimeout(confirmTimer));

async function revoke(token: Token) {
  if (!confirmFirst(token.id)) return;
  busy.value = true;
  try {
    await $fetch(`/api/tokens/${token.id}/revoke`, { method: "POST" });
    // Said once the list shows it.
    await load();
    message.value = `Revoked ${token.name}: it stops working from the next request.`;
  } catch (err) {
    message.value = `Could not revoke ${token.name}: ${errorMessage(err)}`;
  } finally {
    busy.value = false;
  }
}

async function purge() {
  if (!confirmFirst("purge")) return;
  busy.value = true;
  try {
    const answer = await $fetch<{ purged: number }>("/api/tokens/purge", { method: "POST", body: {} });
    await load();
    message.value = purgedText(answer.purged);
  } catch (err) {
    message.value = `Could not purge: ${errorMessage(err)}`;
  } finally {
    busy.value = false;
  }
}

// A new token.
const creating = ref(false);
const form = reactive({ name: "", role: "operator" as Role, tenant: "", expiry: "365" });
const formError = ref("");
const created = shallowRef<NewToken | null>(null);
const announce = ref("");
const roleItems = ROLES.map((role) => ({ label: role, value: role, description: ROLE_HINTS[role] }));
const tenants = shallowRef<Tenant[] | null>(null);
const tenantItems = computed(() =>
  choiceItems(
    (tenants.value ?? []).map((tenant) => tenant.name),
    Object.fromEntries((tenants.value ?? []).map((tenant) => [tenant.name, tenant.description])),
  ),
);
// The tenants Nautobot has, read the first time a status token is made.
watch(
  () => form.role,
  async (role) => {
    if (role !== "status" || tenants.value) return;
    try {
      tenants.value = (await $fetch<{ tenants: Tenant[] }>("/api/tenants")).tenants;
    } catch (err) {
      formError.value = `Could not load the tenants: ${errorMessage(err)}`;
    }
  },
);
const canCreate = computed(() => validName(form.name) && (form.role !== "status" || Boolean(form.tenant)));

function startCreating() {
  creating.value = true;
  created.value = null;
  formError.value = "";
}
function closeCreated() {
  // The key leaves the page.
  created.value = null;
  announce.value = "";
}
async function create() {
  formError.value = "";
  busy.value = true;
  try {
    const answer = await $fetch<{ token: NewToken }>("/api/tokens", {
      method: "POST",
      body: {
        name: form.name.trim(),
        role: form.role,
        ...(form.role === "status" ? { tenant: form.tenant } : {}),
        ...expiryBody(form.expiry),
      },
    });
    created.value = answer.token;
    creating.value = false;
    Object.assign(form, { name: "", role: "operator", tenant: "", expiry: "365" });
    await load();
  } catch (err) {
    formError.value = errorMessage(err);
  } finally {
    busy.value = false;
  }
}
const createdLink = computed(() =>
  created.value?.status_link ? shareUrl(window.location.origin, created.value.status_link, created.value.token) : "",
);
async function copy(text: string) {
  announce.value = (await copyText(text)) ? "Copied to the clipboard" : "Could not copy: select it and copy it";
}

const STATE_CLASS: Record<string, string> = { active: "", revoked: "text-muted line-through", expired: "text-muted" };
</script>

<template>
  <section id="admin-tokens" class="overflow-hidden rounded-lg border border-default">
    <div class="flex flex-wrap items-center gap-3 bg-elevated/50 px-4 py-3">
      <h2 class="flex items-center gap-2 font-semibold text-highlighted">
        API tokens
        <UBadge color="neutral" variant="subtle" size="sm">{{ tokens.length.toLocaleString("en") }}</UBadge>
        <HelpTip text="Bearer tokens for scripts, LibreNMS, Alertmanager, AI assistants (MCP) and customers' status pages. A token's key is shown once, when it is made; revoking one takes effect on the next request." />
      </h2>
      <div class="ml-auto flex flex-wrap items-center gap-2">
        <UInput v-if="searchable" v-model="query" class="token-search w-56" size="sm" type="search" icon="i-lucide-search" placeholder="Search tokens" aria-label="Search tokens" autocomplete="off" />
        <UButton id="token-new" size="sm" icon="i-lucide-plus" :disabled="creating" @click="startCreating">New token</UButton>
      </div>
    </div>

    <!-- The key, once. -->
    <div v-if="created" id="token-created" class="space-y-2 border-t border-default bg-success/10 px-4 py-3 text-sm">
      <p class="font-medium text-highlighted">{{ created.name }}: shown only now. Copy it and store it.</p>
      <div class="flex flex-wrap gap-2">
        <UInput class="token-secret min-w-0 flex-1 font-mono" size="sm" :model-value="created.token" readonly aria-label="The new token" @focus="($event.target as HTMLInputElement).select()" />
        <UButton class="token-copy" size="sm" icon="i-lucide-copy" @click="copy(created.token)">Copy</UButton>
      </div>
      <template v-if="createdLink">
        <p>The link to send {{ created.tenant }}'s customer, with this key:</p>
        <div class="flex flex-wrap gap-2">
          <UInput class="token-share min-w-0 flex-1" size="sm" :model-value="createdLink" readonly aria-label="Customer link" />
          <UButton size="sm" icon="i-lucide-copy" @click="copy(createdLink)">Copy</UButton>
        </div>
      </template>
      <UButton size="sm" color="neutral" variant="outline" @click="closeCreated">Done</UButton>
      <p class="sr-only" role="status">{{ announce }}</p>
      <p v-if="announce" class="text-xs text-muted" aria-hidden="true">{{ announce }}</p>
    </div>

    <form v-if="creating" id="token-form" class="grid gap-3 border-t border-default px-4 py-3 text-sm sm:grid-cols-2" @submit.prevent="create">
      <UFormField label="Name" hint="Letters, digits, spaces, . _ -" required>
        <UInput v-model="form.name" class="token-name w-full" :maxlength="100" placeholder="librenms" autocomplete="off" />
      </UFormField>
      <UFormField label="Role" :hint="ROLE_HINTS[form.role]">
        <USelect v-model="form.role" class="token-role w-full" :items="roleItems" />
      </UFormField>
      <UFormField v-if="form.role === 'status'" label="Tenant" hint="Its status page only; as Nautobot has it" required>
        <USelectMenu
          v-model="form.tenant"
          class="token-tenant w-full"
          :items="tenantItems"
          value-key="value"
          :filter-fields="CHOICE_SEARCH_FIELDS"
          :loading="!tenants"
          placeholder="Pick a tenant"
        />
      </UFormField>
      <UFormField label="Works for">
        <USelect v-model="form.expiry" class="token-expiry w-full" :items="EXPIRY_CHOICES" />
      </UFormField>
      <div class="flex items-center gap-2 sm:col-span-2">
        <UButton id="token-create" type="submit" size="sm" :loading="busy" :disabled="!canCreate || busy">Create token</UButton>
        <UButton size="sm" color="neutral" variant="ghost" @click="creating = false">Cancel</UButton>
        <p v-if="formError" class="token-form-error text-error" role="alert">{{ formError }}</p>
      </div>
    </form>

    <p v-if="error" class="border-t border-default px-4 py-3 text-sm text-error" role="alert">{{ error }}</p>
    <div v-else class="overflow-x-auto border-t border-default">
      <table id="token-table" class="w-full text-sm">
        <thead>
          <tr class="text-left text-muted">
            <th class="px-4 py-2 font-medium">Name</th>
            <th class="px-4 py-2 font-medium">Role</th>
            <th class="px-4 py-2 font-medium">Expires</th>
            <th class="px-4 py-2 font-medium">Last used</th>
            <th class="px-4 py-2"><span class="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-default">
          <tr v-if="loaded && !shown.length">
            <td colspan="5" class="px-4 py-3 text-muted">{{ query ? "No token matches." : "No active tokens." }}</td>
          </tr>
          <tr v-for="token in shown" :key="token.id" class="token-row" :data-name="token.name" :data-state="token.state">
            <td class="px-4 py-2">
              <span class="font-medium text-highlighted" :class="STATE_CLASS[token.state]">{{ token.name }}</span>
              <span class="block text-xs text-muted">{{ token.prefix }}… · by {{ token.created_by || "unknown" }}</span>
            </td>
            <td class="px-4 py-2">
              {{ token.role }}<span v-if="token.tenant" class="text-muted"> · {{ token.tenant }}</span>
              <UBadge v-if="token.state !== 'active'" class="ml-1" color="neutral" variant="subtle" size="sm">{{ token.state }}</UBadge>
            </td>
            <td class="px-4 py-2 tabular-nums">{{ day(token.expires_at) }}</td>
            <td class="px-4 py-2 tabular-nums">{{ day(token.last_used_at) }}</td>
            <td class="px-4 py-2 text-right">
              <UButton
                v-if="token.state === 'active'"
                class="token-revoke"
                size="xs"
                :color="confirming === token.id ? 'error' : 'neutral'"
                :variant="confirming === token.id ? 'solid' : 'outline'"
                :disabled="busy"
                :aria-label="confirming === token.id ? `Sure? Revoke ${token.name}` : `Revoke ${token.name}`"
                @click="revoke(token)"
              >{{ confirming === token.id ? "Sure? Revoke" : "Revoke" }}</UButton>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="inactive || showAll" class="flex flex-wrap items-center gap-3 border-t border-default px-4 py-2 text-sm">
      <UButton id="token-show-all" size="xs" color="neutral" variant="link" :aria-expanded="showAll" @click="showAll = !showAll">
        {{ showAll ? "Only the active ones" : inactiveText(inactive) }}
      </UButton>
      <UButton
        v-if="showAll && inactive"
        id="token-purge"
        size="xs"
        :color="confirming === 'purge' ? 'error' : 'neutral'"
        :variant="confirming === 'purge' ? 'solid' : 'outline'"
        :disabled="busy"
        @click="purge"
      >{{ confirming === "purge" ? `Sure? Delete ${inactive.toLocaleString("en")} for good` : "Purge…" }}</UButton>
    </div>
    <p v-if="message" id="token-message" class="border-t border-default px-4 py-2 text-sm text-muted" role="status">{{ message }}</p>
  </section>
</template>
