// Each role at its own addresses (#697): "/" to its landing page, a page to the same
// page in its workspace, one outside it to the landing page with a note. The customer
// status link (/status/…) is left alone. Convenience only: the API checks every page.
import { toast } from "vue-sonner";
import { workspaceMove } from "~/utils/workspaces";

export default defineNuxtRouteMiddleware(async (to) => {
  if (to.path.startsWith("/status/")) return;
  const { data: config } = await usePageConfig();
  const access = config.value?.access;
  const query = new URLSearchParams(
    Object.entries(to.query).flatMap(([key, value]) =>
      (Array.isArray(value) ? value : [value])
        .filter((v) => v !== null)
        .map((v) => [key, String(v)]),
    ),
  ).toString();
  const move = workspaceMove(access?.role, access?.landing || "/", to.path, query);
  if (!move) return;
  // After the app is up: on a first load the toasts' place isn't there yet.
  if (move.refused)
    onNuxtReady(() => toast("That page isn't part of your workspace: here is your start page."));
  return navigateTo(move.to + to.hash, { replace: true });
});
