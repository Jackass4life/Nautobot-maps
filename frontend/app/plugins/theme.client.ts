// Light or dark before the first paint (#463), without an inline script:
// the choice the old pages keep (static/js/lib/theme.js), else the system's.
// A page can ask to follow the system only (the public status page).
export const THEME_KEY = "nautobot-maps-theme";

export function wantsDark(saved: string | null, systemDark: boolean): boolean {
  if (saved === "dark") return true;
  if (saved === "light") return false;
  return systemDark;
}

export default defineNuxtPlugin(() => {
  let saved: string | null = null;
  try {
    saved = window.localStorage.getItem(THEME_KEY);
  } catch {
    // Storage blocked: the system's choice.
  }
  const system = window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  const followSystem = window.location.pathname.startsWith("/status/");
  const dark = wantsDark(followSystem ? null : saved, system);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
});
