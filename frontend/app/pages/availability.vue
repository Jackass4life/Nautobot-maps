<!-- Availability (#287, #341, #342; on Nuxt since #463): how much of the
     time each site and tenant was up in a period.  The answer first (four
     cards), then the worst tenants and sites.  ?from=&to= (or ?month=) and
     ?basis= in the address, so a report can be shared. -->
<script setup lang="ts">
import { plural, siteRows, tenantRows, formatDowntime, type Report } from "~/utils/availability";

useHead({ title: "Availability · Nautobot Maps" });

const { data: config } = usePageConfig();
const limits = computed(() => config.value?.availability);

const BASES = [
  { value: "any_down", label: "Any monitored device down" },
  { value: "critical", label: "Site at Critical" },
];
const HELP = {
  page: "How much of the time each site and tenant was up in a period. Pick the period and what counts as down. The worst come first. Maintenance windows are never downtime.",
  basis:
    "What counts as down. Any monitored device down: a site is down while at least one of its monitored devices is down (several at once count once). Site at Critical: only the time the site was at Critical on the board.",
  csv: "For a spreadsheet, with the period and totals at the top. Sites and tenants: the tables below, with more detail per site. Every incident: each device outage in the period, to check the figures. Times are UTC, downtime in minutes.",
  downtime: "All sites' downtime in the period added up, without maintenance. An incident is one device going down and coming back.",
  worst: "The tenant with the lowest availability: the average of its sites, each site counting the same.",
  tenants: "Each tenant's availability is the average of its sites. The worst first; click a column to sort by it.",
  sites: "Every monitored site, the worst first. Click a column to sort; search for a site or pick a tenant. Incidents: device outages in the period. The bar runs from 98 to 100 %.",
};

// What was asked for, from the address.
const params = new URLSearchParams(window.location.search);
const asked = reactive({
  month: params.get("month") || "",
  from: params.get("from") || "",
  to: params.get("to") || "",
  basis: params.get("basis") || "",
});

// Up to 1,200 sites: not deeply reactive.
const report = shallowRef<Report | null>(null);
const error = ref("");
const loading = ref(true);

const basis = computed(() => report.value?.basis || asked.basis || limits.value?.basis || "any_down");
// What was just picked, at once; with nothing in the address, the report's (this month).
const shownFrom = computed(() => asked.from || report.value?.period.from || "");
const shownTo = computed(() => asked.to || report.value?.period.to || "");
const csvQuery = computed(() => new URLSearchParams({ from: shownFrom.value, to: shownTo.value, basis: basis.value }).toString());
const sites = computed(() => (report.value ? siteRows(report.value.sites) : []));
const tenants = computed(() => (report.value ? tenantRows(report.value.tenants) : []));
const tenantNames = computed(() => (report.value ? report.value.tenants.map((t) => t.tenant).sort() : []));
const summary = computed(() => report.value?.summary);
const periodNote = computed(() => {
  const period = report.value?.period;
  if (!period) return "";
  return `${period.start.slice(0, 10)} to ${period.end.slice(0, 16).replace("T", " ")} UTC${period.complete ? "" : " (so far)"}`;
});

function query(): URLSearchParams {
  const search = new URLSearchParams();
  if (asked.from || asked.to) {
    search.set("from", asked.from);
    search.set("to", asked.to);
  } else if (asked.month) search.set("month", asked.month);
  if (asked.basis) search.set("basis", asked.basis);
  return search;
}

async function load() {
  loading.value = true;
  try {
    report.value = await $fetch<Report>(`/api/availability?${query()}`);
    error.value = "";
  } catch (err) {
    report.value = null;
    const data = (err as { data?: { error?: string } })?.data;
    error.value = data?.error || "The report could not be made; try again shortly.";
  } finally {
    loading.value = false;
  }
}

// A new period or basis: in the address too, so it can be shared.
function apply() {
  window.history.pushState(null, "", `/availability?${query()}`);
  load();
}
function pickPeriod(from: string, to: string) {
  Object.assign(asked, { from, to, month: "" });
  apply();
}
function pickBasis(value: unknown) {
  asked.basis = String(value);
  apply();
}
function onPopState() {
  const again = new URLSearchParams(window.location.search);
  Object.assign(asked, { month: again.get("month") || "", from: again.get("from") || "", to: again.get("to") || "", basis: again.get("basis") || "" });
  load();
}

onMounted(() => {
  load();
  window.addEventListener("popstate", onPopState);
});
onBeforeUnmount(() => window.removeEventListener("popstate", onPopState));
</script>

<template>
  <div>
    <AppBar active="availability" />
    <main class="mx-auto max-w-6xl space-y-5 px-4 py-6">
      <h1 class="flex items-center gap-2 text-2xl font-bold text-highlighted">Availability <HelpTip :text="HELP.page" /></h1>

      <!-- The period and what counts as down (#287, #341). -->
      <section id="availability-form" class="flex flex-wrap items-center gap-3">
        <span class="text-sm text-muted">Period</span>
        <PeriodPicker
          v-if="limits?.today && (shownFrom || !loading)"
          :from="shownFrom"
          :to="shownTo"
          :min="limits.oldest_day || undefined"
          :max="limits.today"
          :today="limits.today"
          @pick="pickPeriod"
        />
        <span class="ml-2 flex items-center gap-1 text-sm text-muted">Downtime <HelpTip :text="HELP.basis" /></span>
        <USelect id="availability-basis" :model-value="basis" :items="BASES" class="w-64" aria-label="What counts as down" @update:model-value="pickBasis" />
      </section>

      <p v-if="error" class="availability-note text-sm text-error" role="alert">{{ error }}</p>
      <p v-else-if="loading && !report" class="text-sm text-muted">Loading…</p>

      <template v-if="report && summary">
        <p class="availability-note text-sm text-muted">
          {{ periodNote }} · maintenance is not downtime · CSV <HelpTip :text="HELP.csv" />:
          <a class="text-primary hover:underline" :href="`/api/availability.csv?${csvQuery}&view=sites&header=1`">sites</a>,
          <a class="text-primary hover:underline" :href="`/api/availability.csv?${csvQuery}&view=tenants&header=1`">tenants</a>,
          <a
            class="text-primary hover:underline"
            :href="`/api/availability.csv?${csvQuery}&view=incidents&header=1`"
            title="Every outage in the period, to check the figures"
          >every incident</a>
        </p>

        <!-- The answer first (#342): four cards. -->
        <section class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="Summary">
          <UCard class="avail-card">
            <p class="flex items-center gap-1 text-sm text-muted">
              Availability
              <HelpTip :text="`For each site: the share of the period it was up (1 − downtime ÷ period, in UTC). This card is the average of all sites, each site counting the same. Below ${summary.target} % is red.`" />
            </p>
            <p class="mt-1 text-3xl font-bold tabular-nums" :class="summary.availability < summary.target ? 'text-error' : 'text-highlighted'">
              {{ summary.availability.toFixed(3) }} %
            </p>
            <p class="text-sm text-muted">average of {{ plural(summary.sites, "site") }}</p>
          </UCard>
          <UCard class="avail-card">
            <p class="flex items-center gap-1 text-sm text-muted">Downtime <HelpTip :text="HELP.downtime" /></p>
            <p class="mt-1 text-3xl font-bold text-highlighted">{{ formatDowntime(summary.downtime_seconds) }}</p>
            <p class="text-sm text-muted">{{ plural(summary.incidents, "incident") }}, all sites</p>
          </UCard>
          <UCard class="avail-card">
            <p class="flex items-center gap-1 text-sm text-muted">
              Below {{ summary.target }} %
              <HelpTip :text="`How many sites were up less than ${summary.target} % of the period (the target, shown in red), and how many had no downtime at all.`" />
            </p>
            <p class="mt-1 text-3xl font-bold tabular-nums" :class="summary.below_target ? 'text-error' : 'text-highlighted'">
              {{ summary.below_target.toLocaleString("en") }}
            </p>
            <p class="text-sm text-muted">of {{ plural(summary.sites, "site") }} · {{ summary.no_downtime.toLocaleString("en") }} with no downtime</p>
          </UCard>
          <UCard class="avail-card">
            <p class="flex items-center gap-1 text-sm text-muted">Worst tenant <HelpTip :text="HELP.worst" /></p>
            <template v-if="summary.worst_tenant">
              <p class="mt-1 text-xl font-bold text-highlighted">
                <TenantList :tenants="[summary.worst_tenant.tenant]" :descriptions="report.tenant_descriptions" links />
              </p>
              <p class="text-sm" :class="summary.worst_tenant.availability < summary.target ? 'text-error' : 'text-muted'">
                {{ summary.worst_tenant.availability.toFixed(3) }} %
              </p>
            </template>
            <template v-else>
              <p class="mt-1 text-3xl font-bold text-highlighted">—</p>
              <p class="text-sm text-muted">{{ report.tenants.length ? "none had downtime" : "no tenants" }}</p>
            </template>
          </UCard>
        </section>

        <AvailTable
          kind="tenants"
          title="Tenants"
          :help="HELP.tenants"
          :rows="tenants"
          :target="summary.target"
          :descriptions="report.tenant_descriptions"
          :limit="10"
        />
        <AvailTable
          kind="sites"
          title="Sites"
          :help="HELP.sites"
          :rows="sites"
          :target="summary.target"
          :descriptions="report.tenant_descriptions"
          :limit="25"
          :tenant-choices="tenantNames"
        />
      </template>
    </main>
  </div>
</template>
