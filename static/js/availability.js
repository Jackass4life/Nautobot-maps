"use strict";

// The availability page (#287) is rendered on the server; this only keeps the
// light/dark choice the other pages use.
const THEME_KEY = "nautobot-maps-theme";
const themeToggle = document.getElementById("theme-toggle");

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  if (!themeToggle) return;
  const dark = theme === "dark";
  themeToggle.textContent = dark ? "Light mode" : "Dark mode";
  themeToggle.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
  themeToggle.setAttribute("aria-pressed", dark ? "true" : "false");
}

function storedTheme() {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "dark" || stored === "light") return stored;
  } catch (_err) {
    // Storage unavailable: follow the system.
  }
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

applyTheme(storedTheme());
if (themeToggle) {
  themeToggle.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch (_err) {
      // noop
    }
  });
}
