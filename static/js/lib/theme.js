// The Dark mode / Light mode button (#294): the stored choice, else the system's.
const THEME_KEY = "nautobot-maps-theme";

export function applyTheme(theme, themeToggle) {
  document.documentElement.setAttribute("data-theme", theme);
  if (themeToggle) {
    const darkEnabled = theme === "dark";
    themeToggle.textContent = darkEnabled ? "Light mode" : "Dark mode";
    themeToggle.setAttribute("aria-label", darkEnabled ? "Switch to light mode" : "Switch to dark mode");
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

export function initThemeToggle(themeToggle) {
  if (!themeToggle) return;
  applyTheme(initialTheme(), themeToggle);
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
