// The tenant status page (templates/status.html, #291): refreshes every minute.
import { refresh } from "./page.js";

const REFRESH_MS = 60 * 1000;

// No theme button here: the page follows the system's light or dark.
const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");

const tick = async () => {
  // A refused link stays refused; stop asking.
  if (await refresh()) window.setTimeout(tick, REFRESH_MS);
};
tick();
