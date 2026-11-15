<!-- The Admin page's identities (#721): who may sign in, by the user name the
     sign-in gives, with roles and scopes of their own, and their keys.  Everything
     the API's /api/v1/identities does; a change counts on this server at once (on
     the others within 30 s).  Identities made for one key (token create --role)
     are folded. -->
<script setup lang="ts">
import {
  deleteIdentityText,
  identityMatches,
  keysText,
  normalScope,
  SCOPE_HINT,
  validIdentityName,
  type Identity,
  type RoleInfo,
} from "~/utils/access";
import { EXPIRY_CHOICES, expiryBody, validName, type NewToken } from "~/utils/admin";
import { copyText } from "~/utils/clipboard";
import { errorMessage } from "~/utils/format";

const api = () => useNuxtApp().$api;
// Bumped by any change here or in Roles: the other section reads again.
const changes = useState("admin-access-changes", () => 0);
const tokensChanged = useState("admin-tokens-changes", () => 0);

const identities = shallowRef<Identity[]>([]);
const roles = shallowRef<RoleInfo[]>([]);
const loaded = ref(false);
const error = ref("");
const message = ref("");
const failed = ref(false);
const query = ref("");
const showKeyOnes = ref(false);
const busy = ref(false);

async function load() {
  try {
    const [found, held] = (await Promise.all([
      unwrap(api().GET("/api/v1/identities")),
      unwrap(api().GET("/api/v1/roles")),
    ])) as [{ identities: Identity[] }, { roles: RoleInfo[] }];
    identities.value = found.identities;
    roles.value = held.roles;
    error.value = "";
  } catch (err) {
    error.value = `Could not load the identities: ${errorMessage(err)}`;
  } finally {
    loaded.value = true;
  }
}
onMounted(load);
watch(changes, load);
watch(tokensChanged, load);

const people = computed(() => identities.value.filter((each) => !each.for_a_key));
const keyOnes = computed(() => identities.value.filter((each) => each.for_a_key));
const shown = computed(() =>
  (showKeyOnes.value ? identities.value : people.value).filter((each) =>
    identityMatches(each, query.value),
  ),
);

/** A change: answered with the identity as it is now, or a refusal said in place. */
async function change(what: string, run: () => Promise<unknown>): Promise<boolean> {
  busy.value = true;
  message.value = "";
  try {
    await run();
    changes.value++;
    await load();
    message.value = what;
    failed.value = false;
    return true;
  } catch (err) {
    message.value = errorMessage(err);
    failed.value = true;
    return false;
  } finally {
    busy.value = false;
  }
}

// One row open at a time: its roles, scopes and keys can change.
const open = ref<string | null>(null);
const addRole = ref("");
const addScope = ref("");
const scopeValid = computed(() => normalScope(addScope.value) !== null);
function toggle(identity: Identity) {
  open.value = open.value === identity.unique_name ? null : identity.unique_name;
  addRole.value = "";
  addScope.value = "";
  keyName.value = `${identity.unique_name}-key`;
  created.value = null;
}
function roleItems(identity: Identity) {
  return roles.value
    .filter((role) => !identity.roles.includes(role.name))
    .map((role) => ({ label: role.label, value: role.name, description: role.description }));
}
const path = (name: string) => ({ params: { path: { name } } });

function giveRole(identity: Identity) {
  const role = addRole.value;
  if (!role) return;
  void change(`${identity.unique_name} has ${role} now.`, () =>
    unwrap(
      api().POST("/api/v1/identities/{name}/roles", {
        ...path(identity.unique_name),
        body: { role } as never,
      }),
    ),
  ).then((done) => done && (addRole.value = ""));
}
function takeRole(identity: Identity, role: string) {
  void change(`${identity.unique_name} no longer has ${role}.`, () =>
    unwrap(
      api().DELETE("/api/v1/identities/{name}/roles/{role}", {
        params: { path: { name: identity.unique_name, role } },
      }),
    ),
  );
}
function giveScope(identity: Identity) {
  const scope = normalScope(addScope.value);
  if (!scope) return;
  void change(`${identity.unique_name} has ${scope} now.`, () =>
    unwrap(
      api().POST("/api/v1/identities/{name}/scopes", {
        ...path(identity.unique_name),
        body: { scope } as never,
      }),
    ),
  ).then((done) => done && (addScope.value = ""));
}
function takeScope(identity: Identity, scope: string) {
  void change(`${identity.unique_name} no longer has ${scope}.`, () =>
    unwrap(
      api().DELETE("/api/v1/identities/{name}/scopes/{scope}", {
        params: { path: { name: identity.unique_name, scope } },
      }),
    ),
  );
}

// Delete: the button asks "Sure?" first, saying what stops working.
const confirming = ref<string | null>(null);
let confirmTimer: ReturnType<typeof setTimeout> | undefined;
onBeforeUnmount(() => clearTimeout(confirmTimer));
function remove(identity: Identity) {
  if (confirming.value !== identity.unique_name) {
    confirming.value = identity.unique_name;
    clearTimeout(confirmTimer);
    confirmTimer = setTimeout(() => (confirming.value = null), 6000);
    return;
  }
  confirming.value = null;
  void change(`Deleted ${identity.unique_name}.`, async () => {
    await unwrap(api().DELETE("/api/v1/identities/{name}", path(identity.unique_name)));
    tokensChanged.value++;
    if (open.value === identity.unique_name) open.value = null;
  });
}

// A key with exactly the identity's rights (#719), its secret shown once.
const keyName = ref("");
const keyExpiry = ref("365");
const created = shallowRef<NewToken | null>(null);
const announce = ref("");
async function makeKey(identity: Identity) {
  busy.value = true;
  message.value = "";
  try {
    const answer = (await unwrap(
      api().POST("/api/v1/tokens", {
        body: {
          name: keyName.value.trim(),
          identity: identity.unique_name,
          ...expiryBody(keyExpiry.value),
        } as never,
      }),
    )) as { token: NewToken };
    created.value = answer.token;
    tokensChanged.value++;
  } catch (err) {
    message.value = errorMessage(err);
    failed.value = true;
  } finally {
    busy.value = false;
  }
}
async function copy(text: string) {
  announce.value = (await copyText(text))
    ? "Copied to the clipboard"
    : "Could not copy: select it and copy it";
}

// A new identity.
const creating = ref(false);
const form = reactive({ name: "", commonName: "", role: "" });
const formError = ref("");
const canCreate = computed(() => validIdentityName(form.name));
async function create() {
  formError.value = "";
  const name = form.name.trim().toLowerCase();
  const done = await change(`Added ${name}.`, () =>
    unwrap(
      api().POST("/api/v1/identities", {
        body: {
          name,
          common_name: form.commonName.trim(),
          roles: form.role ? [form.role] : [],
        } as never,
      }),
    ),
  );
  if (done) {
    creating.value = false;
    Object.assign(form, { name: "", commonName: "", role: "" });
  } else {
    formError.value = message.value;
    message.value = "";
  }
}
const allRoleItems = computed(() =>
  roles.value.map((role) => ({
    label: role.label,
    value: role.name,
    description: role.description,
  })),
);
</script>

<template>
  <section id="admin-identities" class="overflow-hidden rounded-lg border border-default">
    <div class="flex flex-wrap items-center gap-3 bg-elevated/50 px-4 py-3">
      <h2 class="flex items-center gap-2 font-semibold text-highlighted">
        Identities
        <UBadge color="neutral" variant="subtle" size="sm">{{
          people.length.toLocaleString("en")
        }}</UBadge>
        <HelpTip
          text="The people who may sign in, by the user name the sign-in gives, with their roles and scopes of their own. Someone without an identity gets the role their groups give (AUTH_GROUP_ROLES). A change counts on this server at once, on the others within 30 seconds."
        />
      </h2>
      <div class="ml-auto flex flex-wrap items-center gap-2">
        <UInput
          v-model="query"
          class="identity-search w-56"
          size="sm"
          type="search"
          icon="i-lucide-search"
          placeholder="Search identities"
          aria-label="Search identities"
          autocomplete="off"
        />
        <UButton
          id="identity-new"
          size="sm"
          icon="i-lucide-plus"
          :disabled="creating"
          @click="creating = true"
          >New identity</UButton
        >
      </div>
    </div>

    <form
      v-if="creating"
      id="identity-form"
      class="grid gap-3 border-t border-default px-4 py-3 text-sm sm:grid-cols-3"
      @submit.prevent="create"
    >
      <UFormField label="User name" hint="As the sign-in gives it; no spaces" required>
        <UInput
          v-model="form.name"
          class="identity-name w-full"
          :maxlength="255"
          placeholder="jdoe"
          autocomplete="off"
        />
      </UFormField>
      <UFormField label="Display name">
        <UInput
          v-model="form.commonName"
          class="identity-common-name w-full"
          :maxlength="255"
          placeholder="Jane Doe"
          autocomplete="off"
        />
      </UFormField>
      <UFormField label="Role" hint="More can be given after">
        <USelect
          v-model="form.role"
          class="identity-role w-full"
          :items="allRoleItems"
          placeholder="No role"
        />
      </UFormField>
      <div class="flex flex-wrap items-center gap-2 sm:col-span-3">
        <UButton
          id="identity-create"
          type="submit"
          size="sm"
          :loading="busy"
          :disabled="!canCreate || busy"
          >Add identity</UButton
        >
        <UButton size="sm" color="neutral" variant="ghost" @click="creating = false"
          >Cancel</UButton
        >
        <p v-if="formError" class="identity-form-error text-error" role="alert">{{ formError }}</p>
      </div>
    </form>

    <p v-if="error" class="border-t border-default px-4 py-3 text-sm text-error" role="alert">{{
      error
    }}</p>
    <ul v-else id="identity-list" class="divide-y divide-default border-t border-default text-sm">
      <li v-if="loaded && !shown.length" class="px-4 py-3 text-muted">{{
        query ? "No identity matches." : "No identities yet: everyone gets their groups' role."
      }}</li>
      <li
        v-for="identity in shown"
        :key="identity.unique_name"
        class="identity-row px-4 py-2"
        :data-name="identity.unique_name"
      >
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
          <div class="min-w-0 flex-1">
            <span class="font-medium text-highlighted">{{ identity.unique_name }}</span>
            <span v-if="identity.common_name" class="text-muted">
              · {{ identity.common_name }}</span
            >
            <span class="identity-roles mt-0.5 flex flex-wrap gap-1">
              <UBadge
                v-for="role in identity.roles"
                :key="role"
                color="primary"
                variant="subtle"
                size="sm"
                >{{ role }}</UBadge
              >
              <UBadge
                v-for="scope in identity.scopes"
                :key="scope"
                color="neutral"
                variant="outline"
                size="sm"
                class="font-mono"
                >{{ scope }}</UBadge
              >
              <span v-if="!identity.roles.length && !identity.scopes.length" class="text-muted"
                >No role</span
              >
            </span>
          </div>
          <span class="identity-keys text-muted tabular-nums">{{ keysText(identity.keys) }}</span>
          <UButton
            class="identity-edit"
            size="xs"
            color="neutral"
            variant="outline"
            :icon="open === identity.unique_name ? 'i-lucide-chevron-up' : 'i-lucide-pencil'"
            :aria-expanded="open === identity.unique_name"
            :aria-label="`Change ${identity.unique_name}`"
            @click="toggle(identity)"
            >Change</UButton
          >
          <UButton
            class="identity-delete"
            size="xs"
            :color="confirming === identity.unique_name ? 'error' : 'neutral'"
            :variant="confirming === identity.unique_name ? 'solid' : 'outline'"
            :disabled="busy"
            :aria-label="
              confirming === identity.unique_name
                ? `Sure? Delete ${identity.unique_name}`
                : `Delete ${identity.unique_name}`
            "
            @click="remove(identity)"
            >{{ confirming === identity.unique_name ? "Sure? Delete" : "Delete" }}</UButton
          >
        </div>
        <p
          v-if="confirming === identity.unique_name"
          class="identity-delete-note mt-1 text-xs text-error"
          >{{ deleteIdentityText(identity) }}</p
        >

        <div
          v-if="open === identity.unique_name"
          class="identity-panel mt-3 grid gap-4 rounded-md bg-elevated/40 p-3 md:grid-cols-3"
        >
          <div class="space-y-2">
            <h3 class="text-xs font-semibold uppercase text-muted">Roles</h3>
            <div class="flex flex-wrap gap-1">
              <UButton
                v-for="role in identity.roles"
                :key="role"
                class="identity-role-remove"
                size="xs"
                color="primary"
                variant="subtle"
                trailing-icon="i-lucide-x"
                :disabled="busy"
                :aria-label="`Take ${role} from ${identity.unique_name}`"
                @click="takeRole(identity, role)"
                >{{ role }}</UButton
              >
              <span v-if="!identity.roles.length" class="text-xs text-muted">None</span>
            </div>
            <form class="flex gap-2" @submit.prevent="giveRole(identity)">
              <USelect
                v-model="addRole"
                class="identity-role-add min-w-0 flex-1"
                size="sm"
                :items="roleItems(identity)"
                placeholder="Give a role"
                aria-label="Role to give"
              />
              <UButton type="submit" size="sm" :disabled="!addRole || busy">Give</UButton>
            </form>
          </div>
          <div class="space-y-2">
            <h3 class="text-xs font-semibold uppercase text-muted">Own scopes</h3>
            <div class="flex flex-wrap gap-1">
              <UButton
                v-for="scope in identity.scopes"
                :key="scope"
                class="identity-scope-remove font-mono"
                size="xs"
                color="neutral"
                variant="outline"
                trailing-icon="i-lucide-x"
                :disabled="busy"
                :aria-label="`Take ${scope} from ${identity.unique_name}`"
                @click="takeScope(identity, scope)"
                >{{ scope }}</UButton
              >
              <span v-if="!identity.scopes.length" class="text-xs text-muted"
                >None: only their roles'</span
              >
            </div>
            <form class="flex gap-2" @submit.prevent="giveScope(identity)">
              <UInput
                v-model="addScope"
                class="identity-scope-add min-w-0 flex-1 font-mono"
                size="sm"
                placeholder="alerts:ack"
                aria-label="Scope to give"
                autocomplete="off"
                :color="addScope && !scopeValid ? 'error' : undefined"
                :highlight="Boolean(addScope && !scopeValid)"
              />
              <UButton type="submit" size="sm" :disabled="!scopeValid || busy">Give</UButton>
            </form>
            <p v-if="addScope && !scopeValid" class="text-xs text-error">{{ SCOPE_HINT }}</p>
          </div>
          <div class="space-y-2">
            <h3 class="text-xs font-semibold uppercase text-muted">A new key</h3>
            <p class="text-xs text-muted">With exactly {{ identity.unique_name }}'s rights.</p>
            <form class="space-y-2" @submit.prevent="makeKey(identity)">
              <UInput
                v-model="keyName"
                class="identity-key-name w-full"
                size="sm"
                :maxlength="100"
                aria-label="The key's name"
                autocomplete="off"
              />
              <div class="flex gap-2">
                <USelect
                  v-model="keyExpiry"
                  class="min-w-0 flex-1"
                  size="sm"
                  :items="EXPIRY_CHOICES"
                  aria-label="Works for"
                />
                <UButton
                  class="identity-key-create"
                  type="submit"
                  size="sm"
                  :disabled="!validName(keyName) || busy"
                  >Make key</UButton
                >
              </div>
            </form>
            <div v-if="created" class="identity-key-created space-y-2 rounded bg-success/10 p-2">
              <p class="text-xs font-medium text-highlighted"
                >{{ created.name }}: shown only now. Copy it and store it.</p
              >
              <div class="flex gap-2">
                <UInput
                  class="identity-key-secret min-w-0 flex-1 font-mono"
                  size="sm"
                  :model-value="created.token"
                  readonly
                  aria-label="The new key"
                  @focus="($event.target as HTMLInputElement).select()"
                />
                <UButton size="sm" icon="i-lucide-copy" @click="copy(created.token)">Copy</UButton>
              </div>
              <UButton
                size="xs"
                color="neutral"
                variant="outline"
                @click="((created = null), (announce = ''))"
                >Done</UButton
              >
              <p class="sr-only" role="status">{{ announce }}</p>
            </div>
          </div>
        </div>
      </li>
    </ul>

    <div
      v-if="keyOnes.length"
      class="flex flex-wrap items-center gap-3 border-t border-default px-4 py-2 text-sm"
    >
      <UButton
        id="identity-show-keys"
        size="xs"
        color="neutral"
        variant="link"
        :aria-expanded="showKeyOnes"
        @click="showKeyOnes = !showKeyOnes"
      >
        {{
          showKeyOnes
            ? "Only people"
            : `+${keyOnes.length.toLocaleString("en")} made for one API key`
        }}
      </UButton>
    </div>
    <p
      v-if="message"
      id="identity-message"
      class="border-t border-default px-4 py-2 text-sm"
      :class="failed ? 'text-error' : 'text-muted'"
      :role="failed ? 'alert' : 'status'"
      >{{ message }}</p
    >
  </section>
</template>
