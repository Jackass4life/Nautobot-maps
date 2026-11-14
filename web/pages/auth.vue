<!-- Sign in with AD (#717; the example's pages/auth.vue): user name and password,
     then back to the page that sent you here (?redirect=, a path on this site only).
     ?signed_out=1: the top bar's Sign out, which ends the sign-in first. The token
     is an HttpOnly cookie the page never sees. -->
<script setup lang="ts">
import { safeRedirect } from "~/utils/signin";
import { errorMessage } from "~/utils/format";

useHead({ title: "Sign in · Nautobot Maps" });

const params = new URLSearchParams(window.location.search);
const back = safeRedirect(params.get("redirect"));
const signedOut = ref(params.get("signed_out") === "1");

const form = reactive({ username: "", password: "" });
const busy = ref(false);
const error = ref("");

onMounted(async () => {
  if (!signedOut.value) return;
  try {
    await unwrap(useNuxtApp().$api.POST("/api/v1/auth/logout"));
  } catch {
    // Signed out already, or the API is away: the form is still the way back in.
  }
  window.history.replaceState(null, "", "/auth");
});

async function submit() {
  error.value = "";
  busy.value = true;
  try {
    await unwrap(
      useNuxtApp().$api.POST("/api/v1/auth/login", {
        body: { username: form.username.trim(), password: form.password } as never,
      }),
    );
    // A page load, not a route change: every page reads who is signed in afresh.
    window.location.assign(back);
  } catch (err) {
    form.password = "";
    error.value = errorMessage(err);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <main class="mx-auto flex min-h-[80vh] max-w-sm flex-col justify-center gap-6 px-4">
    <div class="space-y-1">
      <p class="flex items-center gap-2 font-semibold text-primary">
        <UIcon name="i-lucide-map-pin" aria-hidden="true" /> Nautobot Maps
      </p>
      <h1 class="text-2xl font-bold text-highlighted">Sign in</h1>
      <p class="text-sm text-muted">With your company (AD) user name and password.</p>
    </div>
    <p v-if="signedOut" id="auth-signed-out" class="text-sm text-success" role="status"
      >You are signed out.</p
    >
    <form id="auth-form" class="space-y-4" @submit.prevent="submit">
      <UFormField label="User name" required>
        <UInput
          id="auth-username"
          v-model="form.username"
          class="w-full"
          autocomplete="username"
          autocapitalize="none"
          spellcheck="false"
          :maxlength="256"
          autofocus
        />
      </UFormField>
      <UFormField label="Password" required>
        <UInput
          id="auth-password"
          v-model="form.password"
          class="w-full"
          type="password"
          autocomplete="current-password"
        />
      </UFormField>
      <p v-if="error" id="auth-error" class="text-sm text-error" role="alert">{{ error }}</p>
      <UButton
        id="auth-submit"
        type="submit"
        block
        :loading="busy"
        :disabled="busy || !form.username.trim() || !form.password"
        >Sign in</UButton
      >
    </form>
  </main>
</template>
