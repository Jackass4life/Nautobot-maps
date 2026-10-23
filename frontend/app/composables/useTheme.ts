// The theme button (#294, #392): the same choice and key as the old pages,
// so it carries over between them (plugins/theme.client.ts applies it).
import { THEME_KEY } from "~/plugins/theme.client";

export function useTheme() {
  const dark = useState("theme-dark", () => document.documentElement.classList.contains("dark"));
  function toggle() {
    dark.value = !dark.value;
    document.documentElement.classList.toggle("dark", dark.value);
    document.documentElement.setAttribute("data-theme", dark.value ? "dark" : "light");
    try {
      window.localStorage.setItem(THEME_KEY, dark.value ? "dark" : "light");
    } catch {
      // Storage blocked: for this page only.
    }
  }
  return { dark, toggle };
}
