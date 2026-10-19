// The theme button (#294; an icon, #392): the stored choice, else the system's.
const THEME_KEY = "nautobot-maps-theme";

export function applyTheme(theme, themeToggle) {
  document.documentElement.setAttribute("data-theme", theme);
  if (themeToggle) {
    const darkEnabled = theme === "dark";
    // An icon (#392): what a click does is its label and tooltip.
    const label = darkEnabled ? "Switch to light mode" : "Switch to dark mode";
    themeToggle.setAttribute("aria-label", label);
    themeToggle.title = darkEnabled ? "Light mode" : "Dark mode";
    themeToggle.setAttribute("aria-pressed", darkEnabled ? "true" : "false");
  }
}

function initialTheme() {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "dark" || stored === "light") return stored;
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) return "dark";
  } catch (_err) {
    // Storage unavailable: light.
  }
  return "light";
}

// The stored or system theme, and the button (if the page has one) to change it.
export function initThemeToggle(themeToggle) {
  applyTheme(initialTheme(), themeToggle);
  if (!themeToggle) return;
  themeToggle.addEventListener("click", () => {
    const nextTheme = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(nextTheme, themeToggle);
    try {
      localStorage.setItem(THEME_KEY, nextTheme);
    } catch (_err) {
      // noop
    }
  });
}
