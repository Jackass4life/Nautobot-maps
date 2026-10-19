// The top bar on every page (#392): the theme icon, and the menu of pages,
// which closes on a press outside it (also on the map's controls, which keep
// clicks to themselves) or Escape.  Called once by each page.
import { initThemeToggle } from "./theme.js";

export function initTopBar() {
  initThemeToggle(document.getElementById("theme-toggle"));
  const menu = document.querySelector(".app-menu");
  if (!menu) return;
  // While open, the page knows (the map lifts its panel above the site panel).
  menu.addEventListener("toggle", () => document.body.classList.toggle("app-menu-open", menu.open));
  document.addEventListener("pointerdown", (event) => {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  }, true);
  // First (capture): Escape closes the menu, not also the site panel behind it.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !menu.open) return;
    menu.open = false;
    menu.querySelector("summary")?.focus();
    event.stopPropagation();
  }, true);
}
