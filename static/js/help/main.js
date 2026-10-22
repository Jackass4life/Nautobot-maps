// The documentation pages (templates/help.html, #433).
import { initTopBar } from "../lib/nav.js";

// On a narrow screen the list of guides starts folded, above the text.
const nav = document.getElementById("help-nav");
if (nav && window.matchMedia("(max-width: 760px)").matches) nav.open = false;

initTopBar();
