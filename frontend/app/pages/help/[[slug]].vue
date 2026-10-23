<!-- The documentation (#433; on Nuxt since #463): the guides on the left,
     the text on the right; on a phone the list folds above it.  The HTML is
     the server's, made from the repository's Markdown (no raw HTML in it). -->
<script setup lang="ts">
import type { components } from "~/api/schema";

type HelpPage = components["schemas"]["HelpPage"];

const route = useRoute();
const slug = computed(() => String(route.params.slug || "overview"));
const page = shallowRef<HelpPage | null>(null);
const error = ref("");
const article = useTemplateRef<HTMLElement>("article");
// A wide screen shows the list; a phone folds it above the text.
const listOpen = ref(window.matchMedia?.("(min-width: 768px)").matches ?? true);

useHead({ title: () => `${page.value?.title || "Documentation"} · Nautobot Maps` });

// The heading in the address (#anchor), once the text is there.
async function showAnchor() {
  await nextTick();
  const id = decodeURIComponent(window.location.hash.slice(1));
  if (id) document.getElementById(id)?.scrollIntoView();
  else window.scrollTo(0, 0);
}

// Guides already read, so going back and forth shows them at once.
const loaded = new Map<string, HelpPage>();
async function fetchGuide(name: string): Promise<HelpPage> {
  const found = loaded.get(name) ?? (await $fetch<HelpPage>(`/api/help/${encodeURIComponent(name)}`));
  loaded.set(name, found);
  return found;
}

async function load() {
  try {
    page.value = await fetchGuide(slug.value);
    error.value = "";
  } catch {
    page.value = null;
    error.value = "This guide could not be loaded; try again shortly.";
  }
  await showAnchor();
}
watch(slug, load, { immediate: true });

// A link to another guide opens it here, without a reload; a link to a
// heading on this page only scrolls.
function onClick(event: MouseEvent) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const link = (event.target as HTMLElement).closest<HTMLAnchorElement>("a[href]");
  if (!link || link.target) return;
  const url = new URL(link.href, window.location.href);
  if (url.origin !== window.location.origin || !/^\/help(\/[^/]+)?$/.test(url.pathname)) return;
  event.preventDefault();
  if (url.pathname === window.location.pathname) {
    window.history.pushState(null, "", url.hash || url.pathname);
    showAnchor();
    return;
  }
  // The guide first, then the address: no blank page in between.
  const name = url.pathname.split("/")[2] || "overview";
  fetchGuide(name)
    .catch(() => undefined)
    .finally(() => navigateTo(url.pathname + url.hash));
}
</script>

<template>
  <div>
    <AppBar active="help" />
    <div class="help-shell mx-auto grid max-w-6xl grid-cols-1 items-start gap-6 px-4 py-6 md:grid-cols-[220px_minmax(0,1fr)]">
      <details id="help-nav" class="help-nav group rounded-lg border border-default md:sticky md:top-16 md:border-0" :open="listOpen" @toggle="listOpen = ($event.target as HTMLDetailsElement).open">
        <summary class="flex cursor-pointer list-none items-center gap-2 px-4 py-3 font-semibold md:hidden">
          <UIcon name="i-lucide-chevron-right" class="size-4 text-muted transition-transform group-open:rotate-90" />
          Guides
        </summary>
        <nav aria-label="Guides" class="px-1.5 pb-2 md:p-0">
          <ul class="space-y-0.5">
            <li v-for="item in page?.contents ?? []" :key="item.slug">
              <NuxtLink
                :to="item.href"
                class="block rounded-md px-3 py-1.5 text-sm"
                :class="item.slug === slug ? 'bg-elevated font-semibold text-primary' : 'text-muted hover:bg-elevated/60 hover:text-highlighted'"
                :aria-current="item.slug === slug ? 'page' : undefined"
              >{{ item.title }}</NuxtLink>
            </li>
          </ul>
        </nav>
      </details>
      <p v-if="error" class="text-sm text-error" role="alert">{{ error }}</p>
      <!-- eslint-disable-next-line vue/no-v-html -- the server's HTML from our own Markdown, no raw HTML (guide.py) -->
      <article v-else-if="page" ref="article" class="help-doc rounded-lg border border-default bg-default px-5 pt-2 pb-10 sm:px-10" @click="onClick" v-html="page.html" />
    </div>
  </div>
</template>
