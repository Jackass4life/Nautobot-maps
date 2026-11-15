<!-- The Admin page's roles (#721): what each role may do (its scopes) and how many
     identities have it.  The five built-in ones can't be deleted, but their scopes
     can change; a change that would leave nobody an admin is refused by the API.
     Everything the API's /api/v1/roles does. -->
<script setup lang="ts">
import {
  deleteRoleText,
  SCOPE_HINT,
  splitScopes,
  validLanding,
  validRoleName,
  type RoleInfo,
} from "~/utils/access";
import { errorMessage } from "~/utils/format";

const api = () => useNuxtApp().$api;
const changes = useState("admin-access-changes", () => 0);

const roles = shallowRef<RoleInfo[]>([]);
const loaded = ref(false);
const error = ref("");
const message = ref("");
const failed = ref(false);
const busy = ref(false);

async function load() {
  try {
    roles.value = ((await unwrap(api().GET("/api/v1/roles"))) as { roles: RoleInfo[] }).roles;
    error.value = "";
  } catch (err) {
    error.value = `Could not load the roles: ${errorMessage(err)}`;
  } finally {
    loaded.value = true;
  }
}
onMounted(load);
watch(changes, load);

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
const path = (name: string) => ({ params: { path: { name } } });

// One role open at a time: its texts and scopes can change.
const open = ref<string | null>(null);
const edit = reactive({ label: "", description: "", landing: "", scopes: "" });
const added = computed(() => splitScopes(edit.scopes));
function toggle(role: RoleInfo) {
  open.value = open.value === role.name ? null : role.name;
  Object.assign(edit, {
    label: role.label,
    description: role.description,
    landing: role.landing,
    scopes: "",
  });
}
function saveTexts(role: RoleInfo) {
  void change(`Saved ${role.name}.`, () =>
    unwrap(
      api().PATCH("/api/v1/roles/{name}", {
        ...path(role.name),
        body: {
          label: edit.label.trim(),
          description: edit.description.trim(),
          landing: edit.landing.trim(),
        } as never,
      }),
    ),
  );
}
async function addScopes(role: RoleInfo) {
  const { scopes } = added.value;
  if (!scopes.length) return;
  const done = await change(`${role.name} has ${scopes.join(", ")} now.`, async () => {
    for (const scope of scopes)
      await unwrap(
        api().POST("/api/v1/roles/{name}/scopes", { ...path(role.name), body: { scope } as never }),
      );
  });
  if (done) edit.scopes = "";
}
function removeScope(role: RoleInfo, scope: string) {
  void change(`${role.name} no longer has ${scope}.`, () =>
    unwrap(
      api().DELETE("/api/v1/roles/{name}/scopes/{scope}", {
        params: { path: { name: role.name, scope } },
      }),
    ),
  );
}

const confirming = ref<string | null>(null);
let confirmTimer: ReturnType<typeof setTimeout> | undefined;
onBeforeUnmount(() => clearTimeout(confirmTimer));
function remove(role: RoleInfo) {
  if (confirming.value !== role.name) {
    confirming.value = role.name;
    clearTimeout(confirmTimer);
    confirmTimer = setTimeout(() => (confirming.value = null), 6000);
    return;
  }
  confirming.value = null;
  void change(`Deleted ${role.name}.`, async () => {
    await unwrap(api().DELETE("/api/v1/roles/{name}", path(role.name)));
    if (open.value === role.name) open.value = null;
  });
}

// A new role.
const creating = ref(false);
const form = reactive({ name: "", label: "", landing: "/", description: "", scopes: "" });
const formScopes = computed(() => splitScopes(form.scopes));
const formError = ref("");
const canCreate = computed(
  () => validRoleName(form.name) && validLanding(form.landing) && !formScopes.value.bad.length,
);
async function create() {
  formError.value = "";
  const name = form.name.trim().toLowerCase();
  const done = await change(`Added ${name}: give it to identities above.`, () =>
    unwrap(
      api().POST("/api/v1/roles", {
        body: {
          name,
          label: form.label.trim(),
          landing: form.landing.trim(),
          description: form.description.trim(),
          scopes: formScopes.value.scopes,
        } as never,
      }),
    ),
  );
  if (done) {
    creating.value = false;
    Object.assign(form, { name: "", label: "", landing: "/", description: "", scopes: "" });
  } else {
    formError.value = message.value;
    message.value = "";
  }
}
</script>

<template>
  <section id="admin-roles" class="overflow-hidden rounded-lg border border-default">
    <div class="flex flex-wrap items-center gap-3 bg-elevated/50 px-4 py-3">
      <h2 class="flex items-center gap-2 font-semibold text-highlighted">
        Roles
        <UBadge color="neutral" variant="subtle" size="sm">{{
          roles.length.toLocaleString("en")
        }}</UBadge>
        <HelpTip
          text="What each role may do: its scopes, e.g. alerts:ack; * stands for any one part (alerts:*). The five built-in roles can be changed, not deleted; the groups give only those. A change counts on this server at once, on the others within 30 seconds."
        />
      </h2>
      <UButton
        id="role-new"
        class="ml-auto"
        size="sm"
        icon="i-lucide-plus"
        :disabled="creating"
        @click="creating = true"
        >New role</UButton
      >
    </div>

    <form
      v-if="creating"
      id="role-form"
      class="grid gap-3 border-t border-default px-4 py-3 text-sm sm:grid-cols-3"
      @submit.prevent="create"
    >
      <UFormField label="Name" hint="a-z first, then a-z, 0-9, _ or -" required>
        <UInput
          v-model="form.name"
          class="role-name w-full"
          :maxlength="64"
          placeholder="auditor"
          autocomplete="off"
        />
      </UFormField>
      <UFormField label="Label" hint="What the pages call it">
        <UInput v-model="form.label" class="role-label w-full" placeholder="Auditor" />
      </UFormField>
      <UFormField label="Opens on" hint="A page of this app">
        <UInput
          v-model="form.landing"
          class="role-landing w-full"
          placeholder="/noc"
          :color="validLanding(form.landing) ? undefined : 'error'"
          :highlight="!validLanding(form.landing)"
        />
      </UFormField>
      <UFormField label="Description" class="sm:col-span-3">
        <UInput v-model="form.description" class="role-description w-full" />
      </UFormField>
      <UFormField
        label="Scopes"
        class="sm:col-span-3"
        :hint="`By commas or spaces. session:basic (the top bar, the help pages) is added. ${SCOPE_HINT}`"
        :error="formScopes.bad.length ? `Not a scope: ${formScopes.bad.join(', ')}` : undefined"
      >
        <UInput
          v-model="form.scopes"
          class="role-scopes w-full font-mono"
          placeholder="page:wall, alerts:history"
          autocomplete="off"
        />
      </UFormField>
      <div class="flex flex-wrap items-center gap-2 sm:col-span-3">
        <UButton
          id="role-create"
          type="submit"
          size="sm"
          :loading="busy"
          :disabled="!canCreate || busy"
          >Add role</UButton
        >
        <UButton size="sm" color="neutral" variant="ghost" @click="creating = false"
          >Cancel</UButton
        >
        <p v-if="formError" class="role-form-error text-error" role="alert">{{ formError }}</p>
      </div>
    </form>

    <p v-if="error" class="border-t border-default px-4 py-3 text-sm text-error" role="alert">{{
      error
    }}</p>
    <ul v-else id="role-list" class="divide-y divide-default border-t border-default text-sm">
      <li v-if="loaded && !roles.length" class="px-4 py-3 text-muted">No roles.</li>
      <li v-for="role in roles" :key="role.name" class="role-row px-4 py-2" :data-name="role.name">
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
          <div class="min-w-0 flex-1">
            <span class="font-medium text-highlighted">{{ role.label }}</span>
            <span class="text-muted"> · {{ role.name }} · opens {{ role.landing }}</span>
            <UBadge v-if="role.built_in" class="ml-2" color="neutral" variant="subtle" size="sm"
              >built in</UBadge
            >
            <span v-if="role.description" class="block text-xs text-muted">{{
              role.description
            }}</span>
          </div>
          <span class="role-identities text-muted tabular-nums">{{
            role.identities === 1 ? "1 identity" : `${role.identities} identities`
          }}</span>
          <UButton
            class="role-edit"
            size="xs"
            color="neutral"
            variant="outline"
            :icon="open === role.name ? 'i-lucide-chevron-up' : 'i-lucide-pencil'"
            :aria-expanded="open === role.name"
            :aria-label="`Change ${role.name}`"
            @click="toggle(role)"
            >Change</UButton
          >
          <UButton
            v-if="!role.built_in"
            class="role-delete"
            size="xs"
            :color="confirming === role.name ? 'error' : 'neutral'"
            :variant="confirming === role.name ? 'solid' : 'outline'"
            :disabled="busy"
            :aria-label="
              confirming === role.name ? `Sure? Delete ${role.name}` : `Delete ${role.name}`
            "
            @click="remove(role)"
            >{{ confirming === role.name ? "Sure? Delete" : "Delete" }}</UButton
          >
        </div>
        <p v-if="confirming === role.name" class="role-delete-note mt-1 text-xs text-error">{{
          deleteRoleText(role)
        }}</p>
        <div class="role-scopes-list mt-1 flex flex-wrap gap-1">
          <template v-if="open === role.name">
            <UButton
              v-for="scope in role.scopes"
              :key="scope"
              class="role-scope-remove font-mono"
              size="xs"
              color="neutral"
              variant="outline"
              trailing-icon="i-lucide-x"
              :disabled="busy"
              :aria-label="`Take ${scope} from ${role.name}`"
              @click="removeScope(role, scope)"
              >{{ scope }}</UButton
            >
          </template>
          <template v-else>
            <UBadge
              v-for="scope in role.scopes"
              :key="scope"
              color="neutral"
              variant="outline"
              size="sm"
              class="font-mono"
              >{{ scope }}</UBadge
            >
          </template>
          <span v-if="!role.scopes.length" class="text-xs text-muted"
            >No scopes: it may do nothing</span
          >
        </div>

        <div
          v-if="open === role.name"
          class="role-panel mt-3 grid gap-3 rounded-md bg-elevated/40 p-3 md:grid-cols-2"
        >
          <form class="space-y-2" @submit.prevent="addScopes(role)">
            <UFormField
              label="Give scopes"
              :hint="SCOPE_HINT"
              :error="added.bad.length ? `Not a scope: ${added.bad.join(', ')}` : undefined"
            >
              <div class="flex gap-2">
                <UInput
                  v-model="edit.scopes"
                  class="role-scope-add min-w-0 flex-1 font-mono"
                  size="sm"
                  placeholder="page:board alerts:ack"
                  autocomplete="off"
                />
                <UButton
                  type="submit"
                  size="sm"
                  :disabled="!added.scopes.length || added.bad.length > 0 || busy"
                  >Give</UButton
                >
              </div>
            </UFormField>
          </form>
          <form class="grid gap-2 sm:grid-cols-2" @submit.prevent="saveTexts(role)">
            <UFormField label="Label">
              <UInput v-model="edit.label" class="role-edit-label w-full" size="sm" />
            </UFormField>
            <UFormField label="Opens on">
              <UInput
                v-model="edit.landing"
                class="role-edit-landing w-full"
                size="sm"
                :color="validLanding(edit.landing) ? undefined : 'error'"
                :highlight="!validLanding(edit.landing)"
              />
            </UFormField>
            <UFormField label="Description" class="sm:col-span-2">
              <UInput v-model="edit.description" class="role-edit-description w-full" size="sm" />
            </UFormField>
            <div class="sm:col-span-2">
              <UButton
                class="role-save"
                type="submit"
                size="sm"
                :disabled="!validLanding(edit.landing) || busy"
                >Save</UButton
              >
            </div>
          </form>
        </div>
      </li>
    </ul>
    <p
      v-if="message"
      id="role-message"
      class="border-t border-default px-4 py-2 text-sm"
      :class="failed ? 'text-error' : 'text-muted'"
      :role="failed ? 'alert' : 'status'"
      >{{ message }}</p
    >
  </section>
</template>
