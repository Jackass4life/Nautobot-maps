// The availability page (#287) is rendered on the server; this only keeps the
// light/dark choice the other pages use.  An entry module, like */main.js.
import { initThemeToggle } from "./lib/theme.js";

initThemeToggle(document.getElementById("theme-toggle"));
