<!-- Customers (#697): every tenant, each opening its status in the app; for NWO and
     admins (status.links.view) its "Share…" too. At /tenants, /customer/tenants and
     /nwo/tenants. -->
<script setup lang="ts">
import { errorMessage } from "~/utils/format";

type Tenant = { name: string; description: string };

const { can } = useCan();
const tenants = shallowRef<Tenant[] | null>(null);
const error = ref("");
const query = ref("");

useHead({ title: "Customers" });

// A tenant's status, under this page's address: /nwo/tenants/<name>.
const base = window.location.pathname.replace(/\/+$/, "");
const href = (name: string) => `${base}/${encodeURIComponent(name)}`;

const shown = computed(() => {
  const words = query.value.trim().toLowerCase();
  return (tenants.value ?? []).filter(
    (t) => !words || `${t.name} ${t.description}`.toLowerCase().includes(words),
  );
});

onMounted(async () => {
  try {
    tenants.value = (
      (await unwrap(useNuxtApp().$api.GET("/api/v1/tenants"))) as { tenants: Tenant[] }
    ).tenants;
  } catch (err) {
    error.value = errorMessage(err);
  }
});
</script>

<template>
  <div>
    <AppBar active="tenants" />
    <main class="mx-auto max-w-4xl space-y-4 px-4 py-6">
      <h1 class="text-2xl font-bold text-highlighted">Customers</h1>
      <p class="max-w-3xl text-sm text-muted">
        Each customer's status, as they see it on their status page: their sites, and what is down
        or in maintenance.
      </p>
      <UInput
        id="tenants-search"
        v-model="query"
        type="search"
        icon="i-lucide-search"
        placeholder="Search customers"
        aria-label="Search customers"
        autocomplete="off"
        class="w-full sm:w-96"
      />
      <p v-if="error" id="tenants-error" class="text-error" role="alert">{{ error }}</p>
      <p v-else-if="tenants === null" class="text-muted">Loading…</p>
      <p v-else-if="!shown.length" id="tenants-none" class="text-muted">{{
        tenants.length ? "No customer matches the search." : "No tenants in Nautobot yet."
      }}</p>
      <UCard v-else :ui="{ body: 'p-0 sm:p-0' }">
        <ul id="tenants-list" class="divide-y divide-default">
          <li
            v-for="tenant in shown"
            :key="tenant.name"
            class="tenant-row flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3"
            :data-name="tenant.name"
          >
            <div class="min-w-0 flex-1">
              <a
                class="tenant-status-link font-medium text-highlighted hover:text-primary hover:underline"
                :href="href(tenant.name)"
                >{{ tenant.name }}</a
              >
              <p v-if="tenant.description" class="text-sm text-muted">{{ tenant.description }}</p>
            </div>
            <StatusLinkControls v-if="can('status.links.view')" :tenant="tenant.name" />
          </li>
        </ul>
      </UCard>
    </main>
  </div>
</template>
