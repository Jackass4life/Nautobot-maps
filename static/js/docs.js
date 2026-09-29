"use strict";

// The API explorer (#230): Try it sends the request and shows the response.

const MAX_BODY_CHARS = 50000;
const themeToggle = document.getElementById("theme-toggle");
const endpointFilter = document.getElementById("endpoint-filter");
const endpointCount = document.getElementById("endpoint-count");
const endpointCards = Array.from(document.querySelectorAll(".endpoint"));

// "/api/x/<id>/detail" or "/api/x/<string:id>" with {id: "a b"} -> "/api/x/a%20b/detail".
function fillPath(template, values) {
  return template.replace(/<(?:[^:>]+:)?([^>]+)>/g, (_match, name) => encodeURIComponent(values[name] ?? ""));
}

function buildUrl(template, pathValues, queryValues) {
  const params = new URLSearchParams();
  Object.entries(queryValues).forEach(([name, value]) => {
    if (value !== "") params.set(name, value);
  });
  const query = params.toString();
  return `${fillPath(template, pathValues)}${query ? `?${query}` : ""}`;
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, "'\\''")}'`;
}

// The same request as a curl command, to rerun outside the browser.
function curlCommand(method, url, body, origin) {
  const parts = ["curl", "-sS"];
  if (method !== "GET") parts.push("-X", method);
  if (body !== null) parts.push("-H", shellQuote("Content-Type: application/json"), "--data", shellQuote(body));
  parts.push(shellQuote(`${origin}${url}`));
  return parts.join(" ");
}

// Pretty JSON when the response is JSON; otherwise the text, shortened.
function formatResponseBody(text, contentType) {
  if ((contentType || "").includes("json")) {
    try {
      return JSON.stringify(JSON.parse(text), null, 2);
    } catch (_err) {
      // Not valid JSON after all: show it as it is.
    }
  }
  if (text.length > MAX_BODY_CHARS) return `${text.slice(0, MAX_BODY_CHARS)}\n… (${text.length - MAX_BODY_CHARS} more characters)`;
  return text;
}

function readForm(form) {
  const pathValues = {};
  const queryValues = {};
  form.querySelectorAll("[data-path-param]").forEach((input) => {
    pathValues[input.dataset.pathParam] = input.value.trim();
  });
  form.querySelectorAll("[data-query-param]").forEach((input) => {
    queryValues[input.dataset.queryParam] = input.value.trim();
  });
  const bodyInput = form.querySelector(".body-input");
  return { pathValues, queryValues, body: bodyInput ? bodyInput.value : null };
}

async function tryEndpoint(card, form) {
  const result = card.querySelector(".try-result");
  const status = result.querySelector(".try-status");
  const curl = result.querySelector(".try-curl");
  const output = result.querySelector(".try-body");
  const button = form.querySelector(".try-btn");
  const method = card.dataset.method;
  const { pathValues, queryValues, body } = readForm(form);
  const url = buildUrl(card.dataset.path, pathValues, queryValues);

  result.hidden = false;
  result.classList.remove("try-ok", "try-failed");
  if (body !== null) {
    try {
      JSON.parse(body);
    } catch (err) {
      status.textContent = `The JSON body is not valid: ${err.message}`;
      result.classList.add("try-failed");
      curl.textContent = "";
      output.textContent = "";
      return;
    }
  }
  if (method !== "GET" && !window.confirm(`${method} ${url} changes data. Send it?`)) return;

  curl.textContent = curlCommand(method, url, body, window.location.origin);
  status.textContent = `${method} ${url} …`;
  output.textContent = "";
  button.disabled = true;
  const started = performance.now();
  try {
    const resp = await fetch(url, {
      method,
      cache: "no-store",
      headers: body !== null ? { "Content-Type": "application/json" } : {},
      body: body !== null ? body : undefined,
    });
    const text = await resp.text();
    const elapsed = Math.round(performance.now() - started);
    const contentType = resp.headers.get("content-type") || "";
    status.textContent = `${resp.status} ${resp.statusText} · ${elapsed} ms · ${contentType || "no content type"}`;
    result.classList.add(resp.ok ? "try-ok" : "try-failed");
    output.textContent = formatResponseBody(text, contentType);
  } catch (err) {
    status.textContent = `Request failed: ${err.message}`;
    result.classList.add("try-failed");
  } finally {
    button.disabled = false;
  }
}

function applyFilter() {
  const needle = (endpointFilter?.value || "").trim().toLowerCase();
  let shown = 0;
  endpointCards.forEach((card) => {
    const match = !needle || card.dataset.search.includes(needle);
    card.hidden = !match;
    if (match) shown += 1;
  });
  document.querySelectorAll(".docs-group").forEach((group) => {
    group.hidden = !group.querySelector(".endpoint:not([hidden])");
  });
  if (endpointCount) endpointCount.textContent = `${shown} of ${endpointCards.length} shown`;
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  if (themeToggle) {
    const darkEnabled = theme === "dark";
    themeToggle.textContent = darkEnabled ? "Light mode" : "Dark mode";
    themeToggle.setAttribute("aria-label", darkEnabled ? "Switch to light mode" : "Switch to dark mode");
    themeToggle.setAttribute("aria-pressed", darkEnabled ? "true" : "false");
  }
}

function initTheme() {
  let theme = "light";
  try {
    const stored = localStorage.getItem("nautobot-maps-theme");
    if (stored === "dark" || stored === "light") {
      theme = stored;
    } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      theme = "dark";
    }
  } catch (_err) {
    theme = "light";
  }
  applyTheme(theme);
}

document.addEventListener("submit", (event) => {
  const form = event.target.closest(".try-form");
  if (!form) return;
  event.preventDefault();
  tryEndpoint(form.closest(".endpoint"), form);
});

endpointFilter?.addEventListener("input", applyFilter);
applyFilter();

initTheme();
themeToggle?.addEventListener("click", () => {
  const nextTheme = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  applyTheme(nextTheme);
  try {
    localStorage.setItem("nautobot-maps-theme", nextTheme);
  } catch (_err) {
    // noop
  }
});
